from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.agents import AgentSpec, AgentStore, SlackSettings
from app.identity import KeyCache, Refusal, exact_email_matches, parse_expiry
from app.litellm_client import LiteLLMClient
from conftest import INTERNAL_TEAM, MINT_KEY, Clock, member, qwen_bot


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_bot": True},
        {"is_restricted": True},
        {"is_ultra_restricted": True},
        {"is_stranger": True},
        {"team_id": "T0OTHER"},
        {"deleted": True},
    ],
    ids=["bot", "guest", "single-channel-guest", "slack-connect", "other-workspace", "deactivated"],
)
@pytest.mark.asyncio
async def test_refuses_users_who_are_not_full_workspace_members(slack, fake_litellm, make_identity, overrides):
    slack.users["U1"] = member("U1", **overrides)
    with pytest.raises(Refusal, match="full members"):
        await make_identity().authorize("U1", qwen_bot())
    assert fake_litellm.requests == []


@pytest.mark.asyncio
async def test_refuses_a_profile_without_email(slack, make_identity):
    slack.users["U1"]["profile"].pop("email")
    with pytest.raises(Refusal, match="email address"):
        await make_identity().authorize("U1", qwen_bot())


@pytest.mark.asyncio
async def test_refuses_when_only_partial_email_matches_exist(fake_litellm, make_identity):
    # LiteLLM matches "u1@hpi.de" inside these addresses too; none of them is the writer.
    fake_litellm.users = [
        {"user_id": "someone", "user_email": "stu1@hpi.de"},
        {"user_id": "other", "user_email": "u1@hpi.de.example"},
    ]
    with pytest.raises(Refusal, match="log in once at the LLM portal at https://llm.example.test"):
        await make_identity().authorize("U1", qwen_bot())
    assert fake_litellm.keys_minted == []


@pytest.mark.asyncio
async def test_picks_the_exact_case_insensitive_email_match(slack, fake_litellm, make_identity):
    slack.users["U1"]["profile"]["email"] = "Anna.Schmidt@HPI.de"
    fake_litellm.users = [
        {"user_id": "johanna", "user_email": "johanna.schmidt@hpi.de"},
        {"user_id": "anna", "user_email": "anna.schmidt@hpi.de"},
        {"user_id": "anna-old", "user_email": "anna.schmidt@hpi.de.old"},
    ]
    fake_litellm.teams[INTERNAL_TEAM] = ["anna"]

    grant = await make_identity().authorize("U1", qwen_bot())

    assert grant.litellm_user_id == "anna"
    assert fake_litellm.keys_minted[0]["user_id"] == "anna"


def test_exact_email_matches_ignores_case_and_whitespace():
    users = [{"user_email": " A@B.de "}, {"user_email": "xa@b.de"}, {"user_email": None}, {}]
    assert exact_email_matches(users, "a@b.DE") == [users[0]]


@pytest.mark.asyncio
async def test_refuses_when_several_accounts_share_the_email(fake_litellm, make_identity):
    fake_litellm.users = [
        {"user_id": "a", "user_email": "u1@hpi.de"},
        {"user_id": "b", "user_email": "U1@hpi.de"},
    ]
    with pytest.raises(Refusal, match="Several LLM portal accounts"):
        await make_identity().authorize("U1", qwen_bot())


@pytest.mark.asyncio
async def test_refuses_users_outside_the_agents_teams(fake_litellm, make_identity):
    fake_litellm.teams[INTERNAL_TEAM] = ["someone-else"]
    with pytest.raises(Refusal, match="only open to a pilot group"):
        await make_identity().authorize("U1", qwen_bot())
    assert fake_litellm.keys_minted == []


@pytest.mark.asyncio
async def test_refuses_when_the_agents_team_does_not_exist(fake_litellm, make_identity):
    fake_litellm.teams = {}
    with pytest.raises(Refusal, match="only open to a pilot group"):
        await make_identity().authorize("U1", qwen_bot())


@pytest.mark.asyncio
async def test_refuses_when_the_agent_is_not_registered_in_litellm(fake_litellm, make_identity):
    fake_litellm.agents = []
    with pytest.raises(Refusal, match="isn't available"):
        await make_identity().authorize("U1", qwen_bot())
    assert fake_litellm.keys_minted == []


@pytest.mark.asyncio
async def test_mints_a_key_for_exactly_the_agents_the_user_may_use(fake_litellm, make_identity):
    other = AgentSpec(name="company-bot", teams=("team-company",), slack=SlackSettings())
    helper = AgentSpec(name="helper", teams=(INTERNAL_TEAM,), slack=SlackSettings())
    fake_litellm.teams["team-company"] = ["someone-else"]
    fake_litellm.agents += [
        {"agent_id": "agent-id-company", "agent_name": "company-bot"},
        {"agent_id": "agent-id-helper", "agent_name": "helper"},
    ]
    identity = make_identity(store=AgentStore([qwen_bot(), other, helper]))

    grant = await identity.authorize("U1", qwen_bot())

    assert grant.agent_id == "agent-id-qwen"
    assert grant.key == "sk-user-key-1"
    assert fake_litellm.keys_minted == [
        {
            "user_id": "litellm-u1",
            "duration": "24h",
            "allowed_routes": ["agent_inference_routes"],
            "object_permission": {"agents": ["agent-id-helper", "agent-id-qwen"]},
            "metadata": {"managed_by": "kiseno-slack-bot", "slack_user_id": "U1"},
        }
    ]


@pytest.mark.asyncio
async def test_reuses_the_cached_key_until_an_hour_before_expiry(fake_litellm, make_identity):
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    current = {"now": now}
    fake_litellm.key_expires = (now + timedelta(hours=24)).isoformat()
    identity = make_identity(key_cache=KeyCache(clock=lambda: current["now"]))

    first = await identity.authorize("U1", qwen_bot())
    current["now"] = now + timedelta(hours=22, minutes=59)
    second = await identity.authorize("U1", qwen_bot())
    current["now"] = now + timedelta(hours=23)
    third = await identity.authorize("U1", qwen_bot())

    assert first.key == second.key == "sk-user-key-1"
    assert third.key == "sk-user-key-2"
    assert len(fake_litellm.keys_minted) == 2


@pytest.mark.asyncio
async def test_mints_a_new_key_when_the_users_agents_change(fake_litellm, make_identity, store):
    clock = Clock(0.0)
    identity = make_identity(monotonic=clock)
    await identity.authorize("U1", qwen_bot())

    helper = AgentSpec(name="helper", teams=(INTERNAL_TEAM,), slack=SlackSettings())
    fake_litellm.agents.append({"agent_id": "agent-id-helper", "agent_name": "helper"})
    store.replace([qwen_bot(), helper])
    clock.now = 5.0  # an unknown agent name re-reads /v1/agents, but at most every 10 s
    await identity.authorize("U1", qwen_bot())
    clock.now = 20.0
    await identity.authorize("U1", qwen_bot())

    assert [k["object_permission"]["agents"] for k in fake_litellm.keys_minted] == [
        ["agent-id-qwen"],
        ["agent-id-helper", "agent-id-qwen"],
    ]


@pytest.mark.asyncio
async def test_forgetting_a_key_mints_a_new_one(fake_litellm, make_identity):
    identity = make_identity()
    grant = await identity.authorize("U1", qwen_bot())
    identity.forget_key(grant.litellm_user_id)
    again = await identity.authorize("U1", qwen_bot())
    assert again.key == "sk-user-key-2"


def test_key_cache_expiry_boundary():
    now = datetime(2026, 11, 12, tzinfo=UTC)
    clock = {"now": now}
    cache = KeyCache(clock=lambda: clock["now"])
    agents = frozenset({"a"})
    cache.put("u", "sk-1", now + timedelta(hours=24), agents)

    assert cache.get("u", agents) == "sk-1"
    assert cache.get("u", frozenset({"a", "b"})) is None  # other agents drop the entry
    cache.put("u", "sk-1", now + timedelta(hours=24), agents)
    clock["now"] = now + timedelta(hours=23) - timedelta(seconds=1)
    assert cache.get("u", agents) == "sk-1"
    clock["now"] = now + timedelta(hours=23)
    assert cache.get("u", agents) is None


def test_parse_expiry_handles_naive_zulu_and_missing_values():
    now = datetime(2026, 11, 12, tzinfo=UTC)
    assert parse_expiry("2026-11-13T00:00:00", now=now) == datetime(2026, 11, 13, tzinfo=UTC)
    assert parse_expiry("2026-11-13T00:00:00Z", now=now) == datetime(2026, 11, 13, tzinfo=UTC)
    assert parse_expiry("2026-11-13T01:00:00+01:00", now=now) == datetime(2026, 11, 13, tzinfo=UTC)
    assert parse_expiry(None, now=now) == now + timedelta(hours=24)
    assert parse_expiry("garbage", now=now) == now + timedelta(hours=24)


@pytest.mark.asyncio
async def test_user_list_is_read_across_pages():
    pages = {
        "1": {"users": [{"user_id": "a", "user_email": "xu1@hpi.de"}], "total_pages": 2},
        "2": {"users": [{"user_id": "b", "user_email": "u1@hpi.de"}], "total_pages": 2},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["user_email"] == "u1@hpi.de"
        return httpx.Response(200, json=pages[request.url.params["page"]])

    client = LiteLLMClient("http://litellm.test", MINT_KEY, transport=httpx.MockTransport(handler))
    users = await client.find_users_by_email("u1@hpi.de")
    assert [u["user_id"] for u in users] == ["a", "b"]
