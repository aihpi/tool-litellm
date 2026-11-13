"""Start the bot: load agents, connect to Slack over Socket Mode, poll agents every minute."""

from __future__ import annotations

import asyncio
import logging
import re
import sys

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from app.agents import AgentStore, KubernetesConfigMapSource, poll_agents_forever, refresh_agents
from app.bot import SlackBot
from app.config import Settings, get_settings
from app.identity import IdentityService, workspace_from_auth
from app.litellm_client import LiteLLMClient

logger = logging.getLogger("kiseno_slack_bot")

_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{4,}|xox[abeprs]-[A-Za-z0-9\-]+|xapp-[A-Za-z0-9\-]+)")


def redact(text: str) -> str:
    return _SECRET.sub("[REDACTED]", text)


class RedactingFilter(logging.Filter):
    """Last line of defence: no LiteLLM key or Slack token reaches the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs request URLs at INFO, and /user/list URLs carry email addresses.
    for noisy in ("httpx", "httpcore", "aiohttp", "slack_sdk", "slack_bolt"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


async def run(settings: Settings) -> None:
    app = AsyncApp(token=settings.slack_bot_token.get_secret_value())
    auth = await app.client.auth_test()
    try:
        workspace = workspace_from_auth(auth, settings.slack_team_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    logger.info("Slack workspace %s, organization %s", workspace.team_id, workspace.enterprise_id or "none")
    bot_user_id = auth.get("user_id")

    store = AgentStore()
    source = KubernetesConfigMapSource(settings.agents_namespace)
    await refresh_agents(source, store)
    logger.info("Loaded agents: %s", [a.name for a in store.all()] or "none")

    litellm = LiteLLMClient(settings.normalized_litellm_base_url, settings.litellm_mint_key.get_secret_value())
    identity = IdentityService(
        slack=app.client,
        litellm=litellm,
        store=store,
        workspace=workspace,
        litellm_team_id=settings.litellm_team_id,
        portal_url=settings.llm_portal_url,
        access_help=settings.access_help_text,
    )
    bot_name = await identity.display_name(bot_user_id) if bot_user_id else "Qwen-Bot"
    bot = SlackBot(
        client=app.client,
        store=store,
        identity=identity,
        litellm=litellm,
        workspace=workspace,
        bot_user_id=bot_user_id,
        bot_name=bot_name,
    )

    @app.event("app_mention")
    async def on_app_mention(event, body):  # noqa: ANN001 - Bolt injects these by name
        await bot.on_app_mention(event, body)

    @app.event("message")
    async def on_message(event, body):  # noqa: ANN001
        await bot.on_message(event, body)

    poller = asyncio.create_task(poll_agents_forever(source, store, settings.agents_poll_seconds))
    handler = AsyncSocketModeHandler(app, settings.slack_app_token.get_secret_value())
    try:
        await handler.start_async()
    finally:
        poller.cancel()
        await litellm.aclose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
