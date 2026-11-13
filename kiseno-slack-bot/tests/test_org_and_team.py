"""Enterprise Grid membership, the bot's LiteLLM team on keys, and the pilot one-liner."""

from __future__ import annotations

import pytest
from app.agents import AgentStore
from app.bot import SlackBot
from app.identity import Refusal, SlackWorkspace, workspace_from_auth
from conftest import BOT_USER, INTERNAL_TEAM, TEAM, member, qwen_bot

ORG = "E0HPI"
BOT_TEAM = "qwen-bot-users"
GRID = SlackWorkspace(TEAM, ORG)


def grid_member(user_id: str, team_id: str, enterprise_id: str | None = ORG, **overrides) -> dict:
    user = member(user_id, team_id=team_id, **overrides)
    if enterprise_id:
        user["enterprise_user"] = {"id": user_id, "enterprise_id": enterprise_id, "enterprise_name": "Org"}
    return user


# Workspace from auth.test


def test_workspace_comes_from_auth_test():
    workspace = workspace_from_auth({"ok": True, "team_id": TEAM, "enterprise_id": ORG, "user_id": BOT_USER})
    assert workspace == SlackWorkspace(TEAM, ORG)
    assert workspace_from_auth({"team_id": TEAM, "enterprise_id": ""}) == SlackWorkspace(TEAM, None)


def test_slack_team_id_overrides_the_workspace():
    assert workspace_from_auth({"team_id": TEAM, "enterprise_id": ORG}, "T0OVERRIDE") == SlackWorkspace(
        "T0OVERRIDE", ORG
    )


def test_missing_team_without_override_is_an_error():
    with pytest.raises(ValueError, match="SLACK_TEAM_ID"):
        workspace_from_auth({"enterprise_id": ORG})


# Enterprise Grid


@pytest.mark.asyncio
async def test_grid_user_from_a_sibling_workspace_is_accepted(slack, make_identity):
    slack.users["U1"] = grid_member("U1", team_id="T0SIBLING")
    grant = await make_identity(workspace=GRID).authorize("U1", qwen_bot())
    assert grant.litellm_user_id == "litellm-u1"


@pytest.mark.asyncio
async def test_grid_user_whose_team_is_the_org_is_accepted(slack, make_identity):
    slack.users["U1"] = member("U1", team_id=ORG)
    grant = await make_identity(workspace=GRID).authorize("U1", qwen_bot())
    assert grant.litellm_user_id == "litellm-u1"


@pytest.mark.parametrize(
    "user",
    [
        grid_member("U1", team_id="T0EXTERNAL", enterprise_id="E0EXTERNAL", is_stranger=True),
        grid_member("U1", team_id="T0EXTERNAL", enterprise_id=None, is_stranger=True),
        grid_member("U1", team_id="T0EXTERNAL", enterprise_id="E0EXTERNAL"),
        grid_member("U1", team_id="T0EXTERNAL", enterprise_id=None),
        grid_member("U1", team_id="T0SIBLING", is_stranger=True),
        grid_member("U1", team_id="T0SIBLING", is_restricted=True),
        grid_member("U1", team_id="T0SIBLING", is_ultra_restricted=True),
        grid_member("U1", team_id="T0SIBLING", is_bot=True),
        grid_member("U1", team_id="T0SIBLING", deleted=True),
    ],
    ids=[
        "external-grid-stranger",
        "external-workspace-stranger",
        "external-grid-connect-user",
        "external-workspace-connect-user",
        "same-org-but-stranger",
        "same-org-guest",
        "same-org-single-channel-guest",
        "same-org-bot",
        "same-org-deactivated",
    ],
)
@pytest.mark.asyncio
async def test_grid_refusals(slack, fake_litellm, make_identity, user):
    slack.users["U1"] = user
    with pytest.raises(Refusal, match="full members") as caught:
        await make_identity(workspace=GRID).authorize("U1", qwen_bot())
    assert caught.value.reason == "not_full_member"
    assert fake_litellm.requests == []


@pytest.mark.asyncio
async def test_without_grid_a_sibling_workspace_is_refused(slack, make_identity):
    slack.users["U1"] = grid_member("U1", team_id="T0SIBLING")
    with pytest.raises(Refusal, match="full members"):
        await make_identity(workspace=SlackWorkspace(TEAM)).authorize("U1", qwen_bot())


@pytest.mark.asyncio
async def test_events_from_a_sibling_workspace_of_the_org_are_handled_and_others_dropped(
    slack, fake_litellm, litellm_client, make_identity
):
    store = AgentStore([qwen_bot()])
    slack.users["U1"] = grid_member("U1", team_id="T0SIBLING")
    bot = SlackBot(
        client=slack,
        store=store,
        identity=make_identity(store=store, workspace=GRID),
        litellm=litellm_client,
        workspace=GRID,
        bot_user_id=BOT_USER,
        update_interval=0,
    )
    event = {"channel": "D1", "channel_type": "im", "user": "U1", "text": "hi", "ts": "10.000100"}

    await bot.on_message({**event, "ts": "11.000100"}, {"team_id": "T0SIBLING", "enterprise_id": "E0EXTERNAL"})
    assert slack.calls == []

    await bot.on_message(event, {"team_id": "T0SIBLING", "enterprise_id": ORG})
    assert slack.called("chat_update")[-1]["text"] == "Hi!"
    assert fake_litellm.stream_requests[0]["params"]["metadata"]["slack"]["team"] == TEAM


# The bot's LiteLLM team


@pytest.mark.asyncio
async def test_keys_carry_the_bots_litellm_team(fake_litellm, make_identity):
    fake_litellm.teams[BOT_TEAM] = ["litellm-u1"]
    await make_identity(litellm_team_id=BOT_TEAM).authorize("U1", qwen_bot())

    minted = fake_litellm.keys_minted[0]
    assert minted["team_id"] == BOT_TEAM
    assert minted["user_id"] == "litellm-u1"
    assert minted["allowed_routes"] == ["agent_inference_routes"]


@pytest.mark.asyncio
async def test_keys_have_no_team_when_none_is_configured(fake_litellm, make_identity):
    await make_identity().authorize("U1", qwen_bot())
    assert "team_id" not in fake_litellm.keys_minted[0]


@pytest.mark.asyncio
async def test_users_outside_the_bots_team_get_the_pilot_one_liner(fake_litellm, make_identity):
    fake_litellm.teams[BOT_TEAM] = ["someone-else"]
    assert "litellm-u1" in fake_litellm.teams[INTERNAL_TEAM]  # the agent's team alone is not enough

    with pytest.raises(Refusal) as caught:
        await make_identity(litellm_team_id=BOT_TEAM).authorize("U1", qwen_bot())

    assert caught.value.reason == "not_in_bot_team"
    assert str(caught.value) == (
        "Sorry, I'm only open to a pilot group for now. You can ask for access at https://llm.example.test."
    )
    assert fake_litellm.keys_minted == []


@pytest.mark.asyncio
async def test_the_one_liner_is_configurable(fake_litellm, make_identity):
    fake_litellm.teams[BOT_TEAM] = []
    identity = make_identity(litellm_team_id=BOT_TEAM, access_help="Qwen-Bot is in a pilot; ask Felix for access.")
    with pytest.raises(Refusal, match="^Qwen-Bot is in a pilot; ask Felix for access.$"):
        await identity.authorize("U1", qwen_bot())


@pytest.mark.asyncio
async def test_the_one_liner_without_portal_url(fake_litellm, make_identity):
    fake_litellm.teams[BOT_TEAM] = []
    with pytest.raises(Refusal, match="Please ask a LiteLLM admin for access"):
        await make_identity(litellm_team_id=BOT_TEAM, portal_url=None).authorize("U1", qwen_bot())


@pytest.mark.asyncio
async def test_pilot_refusal_in_workflows_is_an_ephemeral_one_liner(slack, fake_litellm, litellm_client, make_identity):
    fake_litellm.teams[BOT_TEAM] = []
    store = AgentStore([qwen_bot()])
    bot = SlackBot(
        client=slack,
        store=store,
        identity=make_identity(store=store, litellm_team_id=BOT_TEAM),
        litellm=litellm_client,
        workspace=SlackWorkspace(TEAM),
        bot_user_id=BOT_USER,
        update_interval=0,
    )
    await bot.on_app_mention({"channel": "CWORKFLOWS", "user": "U1", "text": f"<@{BOT_USER}> hi", "ts": "20.000100"})

    ephemeral = slack.called("chat_postEphemeral")
    assert [e["text"] for e in ephemeral] == [
        "Sorry, I'm only open to a pilot group for now. You can ask for access at https://llm.example.test."
    ]
    assert slack.called("chat_postMessage") == []
    assert fake_litellm.stream_requests == []
