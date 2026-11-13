"""Slack events -> agent: routing, identity, history, the streamed reply."""

from __future__ import annotations

import contextlib
import logging
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.a2a import StreamState, build_stream_request
from app.agents import AgentSpec, AgentStore
from app.identity import Grant, IdentityService, Refusal, SlackWorkspace
from app.litellm_client import LiteLLMClient, LiteLLMError
from app.responder import ThrottledMessage
from app.slack_format import (
    SLACK_TEXT_LIMIT,
    history_line,
    markdown_to_mrkdwn,
    slack_to_plain,
    split_message,
    user_ids_in,
)

logger = logging.getLogger(__name__)

EventKind = Literal["mention", "message"]

DM_HISTORY_LIMIT = 20
THREAD_HISTORY_LIMIT = 50
THREAD_PAGES = 5
MAX_PROMPT_CHARS = 24_000
SEEN_EVENTS = 2_000
# Plain messages and the subtypes that are still a person writing something new.
ACCEPTED_SUBTYPES = frozenset({None, "file_share", "thread_broadcast"})
WORKING_REACTION = "eyes"
PLACEHOLDER = ":hourglass_flowing_sand: _Thinking…_"
GENERIC_ERROR = ":warning: Something went wrong on my side. Please try again in a moment."


def route(store: AgentStore, *, kind: EventKind, channel: str, channel_type: str | None) -> AgentSpec | None:
    """Which agent answers an event, or None to stay quiet.

    - Mentions go to the agent that lists the channel, else to the default agent.
    - DMs go to the default agent if it has ``slack.dm: true``.
    - Other channel messages go to the channel's agent only if it has ``slack.listen: all``.
    """
    if kind == "mention":
        return store.agent_for_channel(channel) or store.default_agent()
    if channel_type == "im":
        agent = store.default_agent()
        return agent if agent is not None and agent.slack.dm else None
    if channel_type in ("channel", "group"):
        agent = store.agent_for_channel(channel)
        return agent if agent is not None and agent.slack.listen == "all" else None
    return None


def is_ignored(event: Mapping[str, Any], *, kind: EventKind, bot_user_id: str | None) -> bool:
    """Bot messages, edits, joins and other housekeeping events get no answer."""
    if event.get("subtype") not in ACCEPTED_SUBTYPES:
        return True
    if event.get("bot_id") or event.get("bot_profile") or event.get("edited"):
        return True
    user = event.get("user")
    if not user or user == bot_user_id:
        return True
    if (
        kind == "message"
        and event.get("channel_type") != "im"
        and bot_user_id
        and f"<@{bot_user_id}>" in (event.get("text") or "")
    ):
        # The app_mention event for the same post handles it.
        return True
    return False


def render_progress(state: StreamState) -> str:
    answer = state.answer
    status = state.status or "Thinking…"
    if not answer:
        return f":hourglass_flowing_sand: _{status}_"
    text = markdown_to_mrkdwn(answer)
    if len(text) > SLACK_TEXT_LIMIT:
        text = text[: SLACK_TEXT_LIMIT - 1] + "…"
    return f"{text}\n\n:hourglass_flowing_sand: _{status}_"


def short(text: str, limit: int = 300) -> str:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    return first_line if len(first_line) <= limit else first_line[: limit - 1] + "…"


def render_final(state: StreamState) -> str:
    if state.error:
        return f":warning: The agent stopped with an error: {short(state.error)}"
    answer = state.answer
    if answer:
        return markdown_to_mrkdwn(answer)
    if state.state in ("completed", None) and state.done:
        return "_The agent finished without an answer._"
    return ":warning: The agent stopped before it finished. Please try again."


def error_text(exc: Exception) -> str:
    if isinstance(exc, Refusal):
        return str(exc)
    if isinstance(exc, LiteLLMError):
        if exc.status_code == 429 or "budget" in exc.detail.lower():
            return f":warning: LiteLLM refused the request: {short(exc.detail)}"
        if exc.status_code in (401, 403):
            return ":warning: LiteLLM did not let me run this agent for you. Please ask a LiteLLM admin."
        if exc.status_code == 404:
            return ":warning: The agent isn't available in LiteLLM right now. Please try again later."
        return ":warning: The LLM gateway returned an error. Please try again in a moment."
    if isinstance(exc, httpx.TimeoutException):
        return ":warning: The agent took too long to answer. Please try again."
    if isinstance(exc, httpx.TransportError):
        return ":warning: I couldn't reach the LLM gateway. Please try again in a moment."
    return GENERIC_ERROR


@dataclass(frozen=True)
class Reply:
    channel: str
    channel_type: str | None
    user: str
    ts: str
    thread_ts: str | None
    """Where the answer goes: the thread, or None for a top-level DM."""


class SlackBot:
    def __init__(
        self,
        *,
        client: Any,
        store: AgentStore,
        identity: IdentityService,
        litellm: LiteLLMClient,
        workspace: SlackWorkspace,
        bot_user_id: str | None,
        bot_name: str = "Qwen-Bot",
        update_interval: float = 1.5,
    ):
        self.client = client
        self.store = store
        self.identity = identity
        self.litellm = litellm
        self.workspace = workspace
        self.bot_user_id = bot_user_id
        self.bot_name = bot_name
        self.update_interval = update_interval
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()

    # Entry points for the Bolt listeners

    async def on_app_mention(self, event: Mapping[str, Any], body: Mapping[str, Any] | None = None) -> None:
        await self._dispatch(event, kind="mention", body=body)

    async def on_message(self, event: Mapping[str, Any], body: Mapping[str, Any] | None = None) -> None:
        await self._dispatch(event, kind="message", body=body)

    async def _dispatch(self, event: Mapping[str, Any], *, kind: EventKind, body: Mapping[str, Any] | None) -> None:
        if body is not None and not self._from_our_org(body):
            return
        if is_ignored(event, kind=kind, bot_user_id=self.bot_user_id):
            return
        channel = event.get("channel")
        ts = event.get("ts")
        if not channel or not ts or self._already_seen(channel, ts):
            return
        channel_type = event.get("channel_type") or ("im" if str(channel).startswith("D") else None)
        agent = route(self.store, kind=kind, channel=channel, channel_type=channel_type)
        if agent is None:
            if channel_type == "im":
                await self._post(channel, None, "Sorry, I don't take direct messages at the moment.")
            elif kind == "mention":
                await self._post(channel, event.get("thread_ts") or ts, "Sorry, no agent is set up to answer here yet.")
            return
        thread_ts = event.get("thread_ts") or (None if channel_type == "im" else ts)
        reply = Reply(channel=channel, channel_type=channel_type, user=event["user"], ts=ts, thread_ts=thread_ts)
        try:
            await self.handle(event, agent, reply)
        except Exception:
            logger.exception("Unhandled error for a message in %s", channel)

    def _from_our_org(self, body: Mapping[str, Any]) -> bool:
        """The envelope names the installation's organization or workspace; drop anything else.

        This is not proof of who wrote the message: Slack Connect users arrive in the same
        envelope, so the writer is checked separately with ``users.info``.
        """
        if self.workspace.enterprise_id and body.get("enterprise_id"):
            return body["enterprise_id"] == self.workspace.enterprise_id
        return body.get("team_id") in (None, self.workspace.team_id)

    def _already_seen(self, channel: str, ts: str) -> bool:
        key = (channel, ts)
        if key in self._seen:
            return True
        self._seen[key] = None
        while len(self._seen) > SEEN_EVENTS:
            self._seen.popitem(last=False)
        return False

    # The answer

    async def handle(self, event: Mapping[str, Any], agent: AgentSpec, reply: Reply) -> None:
        try:
            grant = await self.identity.authorize(reply.user, agent)
        except Refusal as refusal:
            logger.info("Refused Slack user %s for agent %s: %s", reply.user, agent.name, refusal.reason)
            await self._refuse(reply, str(refusal))
            return
        except Exception:
            logger.exception("Identity check failed for Slack user %s", reply.user)
            await self._refuse(reply, GENERIC_ERROR)
            return

        await self._react("reactions_add", reply)
        placeholder = await self._post(reply.channel, reply.thread_ts, PLACEHOLDER)
        if placeholder is None:
            return
        message = ThrottledMessage(
            self.client,
            channel=reply.channel,
            ts=placeholder,
            thread_ts=reply.thread_ts,
            min_interval=self.update_interval,
        )
        try:
            prompt = await self.build_prompt(event, reply, exclude={placeholder})
            final = await self._run_agent(agent, grant, prompt, reply, message)
        except Exception as exc:
            if isinstance(exc, (LiteLLMError, httpx.HTTPError, Refusal)):
                logger.warning("Agent call for %s failed: %s", agent.name, exc)
            else:
                logger.exception("Agent call for %s failed", agent.name)
            final = error_text(exc)
        await message.finish(final)
        await self._react("reactions_remove", reply)

    async def _run_agent(
        self,
        agent: AgentSpec,
        grant: Grant,
        prompt: str,
        reply: Reply,
        message: ThrottledMessage,
    ) -> str:
        request = build_stream_request(
            prompt,
            slack={
                "team": self.workspace.team_id,
                "channel": reply.channel,
                "thread_ts": reply.thread_ts or reply.ts,
                "user": reply.user,
            },
        )
        for attempt in (1, 2):
            state = StreamState()
            try:
                stream = self.litellm.stream_agent(grant.agent_id, grant.key, request)
                async with contextlib.aclosing(stream):
                    async for envelope in stream:
                        state.apply(envelope)
                        if state.done:
                            break
                        message.set(render_progress(state))
            except LiteLLMError as exc:
                # A cached key that LiteLLM no longer accepts: mint a fresh one once.
                if exc.status_code == 401 and attempt == 1 and state.events == 0:
                    self.identity.forget_key(grant.litellm_user_id)
                    grant = await self.identity.authorize(reply.user, agent)
                    continue
                raise
            return render_final(state)
        raise AssertionError("unreachable")

    async def build_prompt(self, event: Mapping[str, Any], reply: Reply, *, exclude: set[str]) -> str:
        """The conversation as ``<display name>: <text>`` lines, the current request last."""
        if reply.thread_ts is not None:
            messages = await self._thread_messages(reply.channel, reply.thread_ts)
        else:
            messages = await self._dm_messages(reply.channel)
        current_ts = float(reply.ts)
        earlier = [
            m
            for m in messages
            if m.get("ts") not in exclude
            and m.get("ts") != reply.ts
            and float(m.get("ts") or 0) < current_ts
            and m.get("subtype") in ACCEPTED_SUBTYPES | {"bot_message"}
            and (m.get("text") or "").strip()
        ]
        names = await self._names(earlier + [dict(event)])
        lines = [history_line(self._speaker(m, names), slack_to_plain(m.get("text") or "", names)) for m in earlier]
        current = history_line(names.get(reply.user, reply.user), slack_to_plain(event.get("text") or "", names))
        while lines and sum(len(line) + 1 for line in lines) + len(current) > MAX_PROMPT_CHARS:
            lines.pop(0)
        return "\n".join([*lines, current])

    async def _thread_messages(self, channel: str, thread_ts: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        cursor = None
        for _ in range(THREAD_PAGES):
            kwargs: dict[str, Any] = {"channel": channel, "ts": thread_ts, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            response = await self.client.conversations_replies(**kwargs)
            messages.extend(response.get("messages") or [])
            cursor = (response.get("response_metadata") or {}).get("next_cursor")
            if not response.get("has_more") or not cursor:
                break
        return messages[-THREAD_HISTORY_LIMIT:]

    async def _dm_messages(self, channel: str) -> list[dict[str, Any]]:
        response = await self.client.conversations_history(channel=channel, limit=DM_HISTORY_LIMIT)
        return list(reversed(response.get("messages") or []))

    async def _names(self, messages: list[Mapping[str, Any]]) -> dict[str, str]:
        ids: set[str] = set()
        for m in messages:
            if m.get("user") and not m.get("bot_id"):
                ids.add(m["user"])
            ids |= user_ids_in(m.get("text") or "")
        names = {}
        for user_id in sorted(ids):
            names[user_id] = self.bot_name if user_id == self.bot_user_id else await self.identity.display_name(user_id)
        return names

    def _speaker(self, message: Mapping[str, Any], names: dict[str, str]) -> str:
        if message.get("user") == self.bot_user_id:
            return self.bot_name
        if message.get("bot_id"):
            profile = message.get("bot_profile") or {}
            return profile.get("name") or message.get("username") or "bot"
        user = message.get("user") or "unknown"
        return names.get(user, user)

    # Slack calls (best effort: a failed reaction must not stop the answer)

    async def _post(self, channel: str, thread_ts: str | None, text: str) -> str | None:
        try:
            response = await self.client.chat_postMessage(
                channel=channel, thread_ts=thread_ts, text=split_message(text)[0]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat.postMessage failed in %s: %s", channel, exc)
            return None
        return response.get("ts")

    async def _refuse(self, reply: Reply, text: str) -> None:
        if reply.channel_type == "im":
            await self._post(reply.channel, reply.thread_ts, text)
            return
        try:
            await self.client.chat_postEphemeral(
                channel=reply.channel, user=reply.user, thread_ts=reply.thread_ts, text=text
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat.postEphemeral failed in %s: %s", reply.channel, exc)

    async def _react(self, method: str, reply: Reply) -> None:
        try:
            await getattr(self.client, method)(channel=reply.channel, timestamp=reply.ts, name=WORKING_REACTION)
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s failed: %s", method, exc)
