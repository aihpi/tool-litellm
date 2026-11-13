"""Who is writing, and which LiteLLM key acts for them.

Every Slack message runs as the writer's own LiteLLM user. The steps:

1. ``users.info``: refuse bots, guests, deactivated users, Slack Connect users and other
   organizations. On Enterprise Grid every workspace of the bot's organization counts.
2. Profile email -> ``/user/list?user_email=`` -> exactly one case-insensitive exact match.
3. Membership in the bot's LiteLLM team (``LITELLM_TEAM_ID``) and in one of the agent's ``teams``.
4. A per-user key for the agents the user may use, cached until an hour before it expires.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.agents import AgentSpec, AgentStore
from app.litellm_client import LiteLLMClient

logger = logging.getLogger(__name__)

KEY_DURATION = "24h"
KEY_LIFETIME = timedelta(hours=24)
KEY_REFRESH_MARGIN = timedelta(hours=1)
TEAM_TTL_SECONDS = 60.0
AGENT_IDS_TTL_SECONDS = 300.0
AGENT_IDS_MIN_REFRESH_SECONDS = 10.0
SLACK_USER_TTL_SECONDS = 300.0


class Refusal(Exception):
    """A refusal whose message is safe to post to the user in Slack.

    ``reason`` is a short code for the log, which never gets the message (it can hold an email).
    """

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


class SlackUsers(Protocol):
    async def users_info(self, *, user: str) -> Any: ...


@dataclass(frozen=True)
class Grant:
    """What one agent call needs: the LiteLLM user, the agent id and the user's key."""

    litellm_user_id: str
    agent_id: str
    key: str


@dataclass(frozen=True)
class CachedKey:
    key: str
    expires_at: datetime
    agent_ids: frozenset[str]


@dataclass(frozen=True)
class SlackWorkspace:
    """The bot's Slack workspace and, on Enterprise Grid, its organization."""

    team_id: str
    enterprise_id: str | None = None

    def admits(self, user: Mapping[str, Any]) -> bool:
        """Whether a ``users.info`` user belongs to the bot's organization (or workspace without Grid)."""
        if self.enterprise_id:
            enterprise = (user.get("enterprise_user") or {}).get("enterprise_id")
            return enterprise == self.enterprise_id or user.get("team_id") in (self.team_id, self.enterprise_id)
        return user.get("team_id") == self.team_id


def workspace_from_auth(auth: Mapping[str, Any], team_override: str | None = None) -> SlackWorkspace:
    """The workspace from ``auth.test`` with the bot token; ``SLACK_TEAM_ID`` overrides the team."""
    team_id = auth.get("team_id") or None
    if team_override:
        if team_id and team_id != team_override:
            logger.warning("SLACK_TEAM_ID %s overrides the bot token's workspace %s", team_override, team_id)
        team_id = team_override
    if not team_id:
        raise ValueError("auth.test returned no team_id; set SLACK_TEAM_ID")
    return SlackWorkspace(team_id=team_id, enterprise_id=auth.get("enterprise_id") or None)


def utcnow() -> datetime:
    return datetime.now(UTC)


class KeyCache:
    """Per-user keys in memory, reused until ``refresh_margin`` before they expire."""

    def __init__(self, *, refresh_margin: timedelta = KEY_REFRESH_MARGIN, clock: Callable[[], datetime] = utcnow):
        self._keys: dict[str, CachedKey] = {}
        self.refresh_margin = refresh_margin
        self.clock = clock

    def get(self, user_id: str, agent_ids: frozenset[str]) -> str | None:
        cached = self._keys.get(user_id)
        if cached is None:
            return None
        if cached.agent_ids != agent_ids or self.clock() >= cached.expires_at - self.refresh_margin:
            del self._keys[user_id]
            return None
        return cached.key

    def put(self, user_id: str, key: str, expires_at: datetime, agent_ids: frozenset[str]) -> None:
        self._keys[user_id] = CachedKey(key=key, expires_at=expires_at, agent_ids=agent_ids)

    def invalidate(self, user_id: str) -> None:
        self._keys.pop(user_id, None)


def parse_expiry(value: Any, *, now: datetime) -> datetime:
    """LiteLLM's ``expires``; a naive timestamp is UTC. Falls back to the requested 24 h."""
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return now + KEY_LIFETIME


def exact_email_matches(users: list[dict[str, Any]], email: str) -> list[dict[str, Any]]:
    wanted = email.strip().casefold()
    return [u for u in users if isinstance(u.get("user_email"), str) and u["user_email"].strip().casefold() == wanted]


class IdentityService:
    def __init__(
        self,
        *,
        slack: SlackUsers,
        litellm: LiteLLMClient,
        store: AgentStore,
        workspace: SlackWorkspace,
        litellm_team_id: str | None = None,
        portal_url: str | None = None,
        access_help: str | None = None,
        key_cache: KeyCache | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.slack = slack
        self.litellm = litellm
        self.store = store
        self.workspace = workspace
        self.litellm_team_id = litellm_team_id
        self.portal_url = portal_url
        self.access_help = access_help
        self.keys = key_cache or KeyCache()
        self.monotonic = monotonic
        self._slack_users: dict[str, tuple[float, dict[str, Any]]] = {}
        self._teams: dict[str, tuple[float, frozenset[str]]] = {}
        self._agent_ids: dict[str, str] = {}
        self._agent_ids_at: float | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    # Slack

    async def slack_user(self, slack_user_id: str) -> dict[str, Any]:
        """The ``users.info`` user object, cached for a few minutes."""
        cached = self._slack_users.get(slack_user_id)
        if cached and self.monotonic() - cached[0] < SLACK_USER_TTL_SECONDS:
            return cached[1]
        response = await self.slack.users_info(user=slack_user_id)
        user = response["user"] if response else None
        if not isinstance(user, dict):
            raise Refusal("I couldn't look up your Slack profile. Please try again in a moment.", "no_slack_profile")
        self._slack_users[slack_user_id] = (self.monotonic(), user)
        return user

    async def display_name(self, slack_user_id: str) -> str:
        try:
            user = await self.slack_user(slack_user_id)
        except Exception:  # noqa: BLE001 - a missing name must not stop the reply
            return slack_user_id
        profile = user.get("profile") or {}
        return (
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("real_name")
            or user.get("name")
            or slack_user_id
        )

    async def verified_email(self, slack_user_id: str) -> str:
        user = await self.slack_user(slack_user_id)
        if (
            user.get("is_bot")
            or user.get("is_app_user")
            or user.get("is_restricted")
            or user.get("is_ultra_restricted")
            or user.get("is_stranger")
            or user.get("deleted")
            or not self.workspace.admits(user)
        ):
            raise Refusal("Sorry, I only work for full members of our Slack organization.", "not_full_member")
        email = (user.get("profile") or {}).get("email")
        if not isinstance(email, str) or "@" not in email:
            raise Refusal(
                "I can't see an email address on your Slack profile, so I can't match you to an LLM portal account.",
                "no_email",
            )
        return email.strip()

    # LiteLLM

    def _portal_hint(self) -> str:
        return f" at {self.portal_url}" if self.portal_url else ""

    def no_access_text(self) -> str:
        """The one-liner for LiteLLM users outside the bot's or the agent's teams."""
        if self.access_help:
            return self.access_help
        if self.portal_url:
            return f"Sorry, I'm only open to a pilot group for now. You can ask for access at {self.portal_url}."
        return "Sorry, I'm only open to a pilot group for now. Please ask a LiteLLM admin for access."

    async def litellm_user_id(self, email: str) -> str:
        matches = exact_email_matches(await self.litellm.find_users_by_email(email), email)
        if not matches:
            raise Refusal(
                f"I couldn't find an LLM portal account for {email}. "
                f"Please log in once at the LLM portal{self._portal_hint()}, then write to me again.",
                "no_litellm_user",
            )
        if len(matches) > 1:
            raise Refusal(
                f"Several LLM portal accounts use {email}, so I can't tell which one is yours. "
                "Please ask a LiteLLM admin to sort that out.",
                "ambiguous_email",
            )
        user_id = matches[0].get("user_id")
        if not isinstance(user_id, str) or not user_id:
            raise Refusal("Your LLM portal account looks incomplete. Please ask a LiteLLM admin.", "no_user_id")
        return user_id

    async def _team_members(self, team_id: str) -> frozenset[str]:
        cached = self._teams.get(team_id)
        if cached and self.monotonic() - cached[0] < TEAM_TTL_SECONDS:
            return cached[1]
        members = await self.litellm.team_member_ids(team_id)
        if members is None:
            logger.warning("LiteLLM team %s does not exist", team_id)
        result = frozenset(members or ())
        self._teams[team_id] = (self.monotonic(), result)
        return result

    async def allowed_agents(self, litellm_user_id: str) -> list[AgentSpec]:
        """Agents with at least one team that has the user as a member."""
        agents = self.store.all()
        team_ids = sorted({team for agent in agents for team in agent.teams})
        memberships = await asyncio.gather(*(self._team_members(t) for t in team_ids))
        user_teams = {team for team, members in zip(team_ids, memberships, strict=True) if litellm_user_id in members}
        return [agent for agent in agents if user_teams.intersection(agent.teams)]

    async def _refresh_agent_ids(self) -> None:
        agents = await self.litellm.list_agents()
        self._agent_ids = {
            a["agent_name"]: a["agent_id"]
            for a in agents
            if isinstance(a.get("agent_name"), str) and isinstance(a.get("agent_id"), str)
        }
        self._agent_ids_at = self.monotonic()

    async def agent_ids(self, names: list[str]) -> dict[str, str]:
        """LiteLLM agent ids by name; key grants (``object_permission.agents``) need ids, not names."""
        age = None if self._agent_ids_at is None else self.monotonic() - self._agent_ids_at
        missing = any(name not in self._agent_ids for name in names)
        if age is None or age >= AGENT_IDS_TTL_SECONDS or (missing and age >= AGENT_IDS_MIN_REFRESH_SECONDS):
            await self._refresh_agent_ids()
        return {name: self._agent_ids[name] for name in names if name in self._agent_ids}

    async def authorize(self, slack_user_id: str, agent: AgentSpec) -> Grant:
        email = await self.verified_email(slack_user_id)
        user_id = await self.litellm_user_id(email)
        if self.litellm_team_id and user_id not in await self._team_members(self.litellm_team_id):
            raise Refusal(self.no_access_text(), "not_in_bot_team")
        allowed = [a.name for a in await self.allowed_agents(user_id)]
        if agent.name not in allowed:
            raise Refusal(self.no_access_text(), "not_in_agent_team")
        ids = await self.agent_ids(allowed)
        if agent.name not in ids:
            logger.warning("Agent %s is not registered in LiteLLM", agent.name)
            raise Refusal(f"{agent.name} isn't available right now. Please try again later.", "agent_not_registered")
        key = await self.user_key(user_id, frozenset(ids.values()), slack_user_id)
        return Grant(litellm_user_id=user_id, agent_id=ids[agent.name], key=key)

    async def user_key(self, user_id: str, agent_ids: frozenset[str], slack_user_id: str) -> str:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            cached = self.keys.get(user_id, agent_ids)
            if cached:
                return cached
            response = await self.litellm.generate_key(
                user_id=user_id,
                team_id=self.litellm_team_id,
                agent_ids=sorted(agent_ids),
                slack_user_id=slack_user_id,
                duration=KEY_DURATION,
            )
            key = response.get("key")
            if not isinstance(key, str) or not key:
                raise RuntimeError("LiteLLM /key/generate returned no key")
            expires_at = parse_expiry(response.get("expires"), now=self.keys.clock())
            self.keys.put(user_id, key, expires_at, agent_ids)
            logger.info("Minted a key for LiteLLM user %s (Slack %s)", user_id, slack_user_id)
            return key

    def forget_key(self, user_id: str) -> None:
        self.keys.invalidate(user_id)
