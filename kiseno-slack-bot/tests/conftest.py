from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents import AgentSpec, AgentStore, SlackSettings  # noqa: E402
from app.identity import IdentityService, KeyCache, SlackWorkspace  # noqa: E402
from app.litellm_client import LiteLLMClient  # noqa: E402

TEAM = "T0KISENO"
BOT_USER = "UBOT"
MINT_KEY = "sk-mint-secret"
USER_KEY = "sk-user-key-1"
INTERNAL_TEAM = "team-internal"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def a2a_v1_stream(name: str) -> list[dict[str, Any]]:
    """A stream as LiteLLM sends it for an agent registered with A2A 1.0; see fixtures/make_a2a_v1_stream.py."""
    return json.loads((FIXTURES / "a2a_v1_stream.json").read_text())[name]


def member(user_id: str = "U1", **overrides: Any) -> dict[str, Any]:
    user = {
        "id": user_id,
        "team_id": TEAM,
        "name": user_id.lower(),
        "is_bot": False,
        "is_restricted": False,
        "is_ultra_restricted": False,
        "profile": {
            "display_name": f"Name {user_id}",
            "real_name": f"Real {user_id}",
            "email": f"{user_id.lower()}@hpi.de",
        },
    }
    user.update(overrides)
    return user


class FakeSlack:
    """Records every Web API call the bot makes."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {"U1": member("U1"), BOT_USER: member(BOT_USER, is_bot=True)}
        self.users[BOT_USER]["profile"]["display_name"] = "Qwen-Bot"
        self.replies: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ts = 1000

    def called(self, method: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == method]

    async def users_info(self, *, user: str) -> dict[str, Any]:
        self.calls.append(("users_info", {"user": user}))
        return {"ok": True, "user": self.users[user]}

    async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat_postMessage", kwargs))
        self._ts += 1
        return {"ok": True, "ts": f"{self._ts}.000100"}

    async def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat_update", kwargs))
        return {"ok": True}

    async def chat_postEphemeral(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat_postEphemeral", kwargs))
        return {"ok": True}

    async def reactions_add(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("reactions_add", kwargs))
        return {"ok": True}

    async def reactions_remove(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("reactions_remove", kwargs))
        return {"ok": True}

    async def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("conversations_replies", kwargs))
        return {"ok": True, "messages": self.replies, "has_more": False}

    async def conversations_history(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("conversations_history", kwargs))
        return {"ok": True, "messages": list(reversed(self.history))[: kwargs.get("limit", 100)]}


def sse(*events: dict[str, Any], comments: bool = True) -> bytes:
    chunks = [": ping\n\n"] if comments else []
    chunks += [f"data: {json.dumps(e)}\n\n" for e in events]
    return "".join(chunks).encode()


class FakeLiteLLM:
    """An httpx MockTransport handler for the LiteLLM routes the bot uses."""

    def __init__(self) -> None:
        self.users: list[dict[str, Any]] = [{"user_id": "litellm-u1", "user_email": "u1@hpi.de"}]
        self.teams: dict[str, list[str]] = {INTERNAL_TEAM: ["litellm-u1"]}
        self.agents: list[dict[str, Any]] = [{"agent_id": "agent-id-qwen", "agent_name": "qwen-bot"}]
        self.keys_minted: list[dict[str, Any]] = []
        self.key_expires: str | None = None
        self.stream_responses: list[httpx.Response] = []
        self.requests: list[httpx.Request] = []
        self.stream_requests: list[dict[str, Any]] = []
        self.stream_auth: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/a2a/"):
            self.stream_requests.append(json.loads(request.content))
            self.stream_auth.append(request.headers.get("authorization", ""))
            if self.stream_responses:
                return self.stream_responses.pop(0)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(
                    {
                        "jsonrpc": "2.0",
                        "id": "1",
                        "result": {"kind": "message", "role": "agent", "parts": [{"kind": "text", "text": "Hi!"}]},
                    }
                ),
            )
        assert request.headers["authorization"] == f"Bearer {MINT_KEY}"
        if path == "/user/list":
            needle = request.url.params["user_email"].lower()
            found = [u for u in self.users if needle in u["user_email"].lower()]
            return httpx.Response(
                200, json={"users": found, "total": len(found), "page": 1, "page_size": 100, "total_pages": 1}
            )
        if path == "/team/info":
            team_id = request.url.params["team_id"]
            if team_id not in self.teams:
                return httpx.Response(404, json={"detail": "team not found"})
            members = [{"user_id": u, "role": "user"} for u in self.teams[team_id]]
            return httpx.Response(
                200,
                json={"team_id": team_id, "team_info": {"team_id": team_id, "members_with_roles": members}, "keys": []},
            )
        if path == "/v1/agents":
            return httpx.Response(200, json=self.agents)
        if path == "/key/generate":
            body = json.loads(request.content)
            self.keys_minted.append(body)
            return httpx.Response(
                200, json={"key": f"sk-user-key-{len(self.keys_minted)}", "expires": self.key_expires}
            )
        return httpx.Response(404, json={"detail": "no route"})


def qwen_bot(**slack: Any) -> AgentSpec:
    settings = {"default": True, "channels": ("CWORKFLOWS",), "listen": "mentions", "dm": True} | slack
    return AgentSpec(name="qwen-bot", teams=(INTERNAL_TEAM,), slack=SlackSettings(**settings))


@pytest.fixture
def slack() -> FakeSlack:
    return FakeSlack()


@pytest.fixture
def fake_litellm() -> FakeLiteLLM:
    return FakeLiteLLM()


@pytest.fixture
def litellm_client(fake_litellm: FakeLiteLLM) -> LiteLLMClient:
    return LiteLLMClient("http://litellm.test", MINT_KEY, transport=httpx.MockTransport(fake_litellm.handler))


@pytest.fixture
def store() -> AgentStore:
    return AgentStore([qwen_bot()])


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def make_identity(slack: FakeSlack, litellm_client: LiteLLMClient, store: AgentStore) -> Callable[..., IdentityService]:
    def make(**kwargs: Any) -> IdentityService:
        options: dict[str, Any] = {
            "slack": slack,
            "litellm": litellm_client,
            "store": store,
            "workspace": SlackWorkspace(TEAM),
            "portal_url": "https://llm.example.test",
            "key_cache": KeyCache(),
        }
        options.update(kwargs)
        return IdentityService(**options)

    return make
