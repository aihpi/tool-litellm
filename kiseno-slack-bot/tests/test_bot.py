from __future__ import annotations

import httpx
import pytest
from app.agents import AgentSpec, AgentStore, SlackSettings
from app.bot import PLACEHOLDER, SlackBot, is_ignored, route
from app.identity import SlackWorkspace
from conftest import BOT_USER, TEAM, a2a_v1_stream, member, qwen_bot, sse


def channel_agent(name: str, channel: str, listen: str) -> AgentSpec:
    return AgentSpec(name=name, teams=("team-internal",), slack=SlackSettings(channels=(channel,), listen=listen))


@pytest.fixture
def routing_store() -> AgentStore:
    return AgentStore(
        [qwen_bot(), channel_agent("protokoll", "CPROTO", "all"), channel_agent("quiet", "CQUIET", "mentions")]
    )


# Routing


def test_mentions_go_to_the_channels_agent_or_the_default(routing_store):
    assert route(routing_store, kind="mention", channel="CPROTO", channel_type=None).name == "protokoll"
    assert route(routing_store, kind="mention", channel="CWORKFLOWS", channel_type=None).name == "qwen-bot"
    assert route(routing_store, kind="mention", channel="CRANDOM", channel_type=None).name == "qwen-bot"


def test_dms_go_to_the_default_agent_only_if_it_takes_dms(routing_store):
    assert route(routing_store, kind="message", channel="D1", channel_type="im").name == "qwen-bot"
    no_dm = AgentStore([qwen_bot(dm=False)])
    assert route(no_dm, kind="message", channel="D1", channel_type="im") is None
    assert route(AgentStore(), kind="message", channel="D1", channel_type="im") is None


def test_channel_messages_need_listen_all(routing_store):
    assert route(routing_store, kind="message", channel="CPROTO", channel_type="channel").name == "protokoll"
    assert route(routing_store, kind="message", channel="CPROTO", channel_type="group").name == "protokoll"
    assert route(routing_store, kind="message", channel="CQUIET", channel_type="channel") is None
    assert route(routing_store, kind="message", channel="CWORKFLOWS", channel_type="channel") is None
    assert route(routing_store, kind="message", channel="CRANDOM", channel_type="channel") is None
    assert route(routing_store, kind="message", channel="G1", channel_type="mpim") is None


@pytest.mark.parametrize(
    "event",
    [
        {"user": "U1", "bot_id": "B1", "text": "from a bot"},
        {"subtype": "bot_message", "bot_id": "B1", "text": "x"},
        {"subtype": "message_changed", "message": {"user": "U1"}},
        {"subtype": "message_deleted"},
        {"subtype": "channel_join", "user": "U1"},
        {"subtype": "group_join", "user": "U1"},
        {"user": "U1", "text": "fixed typo", "edited": {"user": "U1", "ts": "2.0"}},
        {"user": BOT_USER, "text": "my own message"},
        {"text": "no user"},
    ],
    ids=["bot_id", "bot_message", "edit", "delete", "channel_join", "group_join", "edited", "self", "no-user"],
)
def test_ignored_events(event):
    assert is_ignored({"channel": "C1", "ts": "1.0", **event}, kind="message", bot_user_id=BOT_USER)


def test_accepted_events_and_mentions_left_to_app_mention():
    plain = {"user": "U1", "text": "hello", "channel_type": "channel"}
    assert not is_ignored(plain, kind="message", bot_user_id=BOT_USER)
    assert not is_ignored({**plain, "subtype": "thread_broadcast"}, kind="message", bot_user_id=BOT_USER)
    assert not is_ignored({**plain, "subtype": "file_share"}, kind="message", bot_user_id=BOT_USER)
    mention = {**plain, "text": f"<@{BOT_USER}> hello"}
    assert is_ignored(mention, kind="message", bot_user_id=BOT_USER)
    assert not is_ignored(mention, kind="mention", bot_user_id=BOT_USER)
    assert not is_ignored({**mention, "channel_type": "im"}, kind="message", bot_user_id=BOT_USER)


# Handling


@pytest.fixture
def bot(slack, fake_litellm, litellm_client, make_identity, store) -> SlackBot:
    return SlackBot(
        client=slack,
        store=store,
        identity=make_identity(),
        litellm=litellm_client,
        workspace=SlackWorkspace(TEAM),
        bot_user_id=BOT_USER,
        bot_name="Qwen-Bot",
        update_interval=0,
    )


def dm(text: str, ts: str = "100.000200", **extra) -> dict:
    return {"type": "message", "channel": "D1", "channel_type": "im", "user": "U1", "text": text, "ts": ts, **extra}


def mention(text: str, ts: str = "200.000100", **extra) -> dict:
    return {"type": "app_mention", "channel": "CWORKFLOWS", "user": "U1", "text": text, "ts": ts, **extra}


@pytest.mark.asyncio
async def test_dm_runs_the_agent_as_the_writer_and_edits_one_placeholder(bot, slack, fake_litellm):
    slack.users["U2"] = member("U2")
    slack.history = [
        {"user": "U1", "text": "earlier question", "ts": "90.000100"},
        {"user": BOT_USER, "bot_id": "B1", "text": "earlier answer", "ts": "91.000100"},
        {"user": "U1", "text": "hey <@U2>, what's new?", "ts": "100.000200"},
    ]
    fake_litellm.stream_responses = [
        httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {
                        "kind": "status-update",
                        "taskId": "t",
                        "status": {
                            "state": "working",
                            "message": {
                                "kind": "message",
                                "role": "agent",
                                "parts": [{"kind": "text", "text": "calling tool espocrm_find"}],
                            },
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {
                        "kind": "artifact-update",
                        "taskId": "t",
                        "artifact": {"artifactId": "a", "parts": [{"kind": "text", "text": "**Nothing** new."}]},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"kind": "status-update", "taskId": "t", "status": {"state": "completed"}, "final": True},
                },
            ),
        )
    ]

    await bot.on_message(dm("hey <@U2>, what's new?"), {"team_id": TEAM})

    assert slack.called("reactions_add") == [{"channel": "D1", "timestamp": "100.000200", "name": "eyes"}]
    posts = slack.called("chat_postMessage")
    assert posts == [{"channel": "D1", "thread_ts": None, "text": PLACEHOLDER}]
    updates = slack.called("chat_update")
    assert updates[-1]["text"] == "*Nothing* new."
    assert {u["ts"] for u in updates} == {"1001.000100"}
    assert slack.called("reactions_remove") == [{"channel": "D1", "timestamp": "100.000200", "name": "eyes"}]

    assert fake_litellm.stream_auth == ["Bearer sk-user-key-1"]
    request = fake_litellm.stream_requests[0]
    assert request["method"] == "message/stream"
    assert request["params"]["message"]["parts"] == [
        {
            "kind": "text",
            "text": "Name U1: earlier question\nQwen-Bot: earlier answer\nName U1: hey @Name U2, what's new?",
        }
    ]
    assert request["params"]["metadata"]["slack"] == {
        "team": TEAM,
        "channel": "D1",
        "thread_ts": "100.000200",
        "user": "U1",
    }
    assert fake_litellm.requests[-1].url.path == "/a2a/agent-id-qwen/message/stream"


@pytest.mark.asyncio
async def test_mention_in_a_thread_sends_the_thread_and_replies_in_it(bot, slack, fake_litellm):
    slack.users["U3"] = member("U3")
    slack.replies = [
        {"user": "U3", "text": "Can someone summarize?", "ts": "150.000100", "thread_ts": "150.000100"},
        {"user": "U1", "text": f"<@{BOT_USER}> please do", "ts": "200.000100", "thread_ts": "150.000100"},
        {"user": "U3", "text": "later reply", "ts": "300.000100", "thread_ts": "150.000100"},
    ]

    await bot.on_app_mention(mention(f"<@{BOT_USER}> please do", thread_ts="150.000100"))

    assert slack.called("conversations_replies")[0]["ts"] == "150.000100"
    assert slack.called("chat_postMessage")[0]["thread_ts"] == "150.000100"
    text = fake_litellm.stream_requests[0]["params"]["message"]["parts"][0]["text"]
    assert text == "Name U3: Can someone summarize?\nName U1: @Qwen-Bot please do"
    assert slack.called("chat_update")[-1]["text"] == "Hi!"


def a2a_v1_response(name: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*a2a_v1_stream(name)))


@pytest.mark.asyncio
async def test_an_a2a_1_0_stream_is_answered_in_the_thread(bot, slack, fake_litellm):
    fake_litellm.stream_responses = [a2a_v1_response("completed")]

    await bot.on_app_mention(mention(f"<@{BOT_USER}> which workshops are planned for December?"))

    updates = slack.called("chat_update")
    assert {u["ts"] for u in updates} == {"1001.000100"}
    assert updates[-1]["text"] == (
        "Two workshops are planned for December:\n\n• *RAG Forum* on 4 December\n• *MCP* on 11 December"
    )
    assert slack.called("chat_postMessage") == [
        {"channel": "CWORKFLOWS", "thread_ts": "200.000100", "text": PLACEHOLDER}
    ]


@pytest.mark.asyncio
async def test_a_failed_a2a_1_0_task_ends_in_its_error(bot, slack, fake_litellm):
    fake_litellm.stream_responses = [a2a_v1_response("failed")]

    await bot.on_app_mention(mention(f"<@{BOT_USER}> which workshops are planned for December?"))

    assert slack.called("chat_update")[-1]["text"] == (
        ":warning: The agent stopped with an error: The agent took too long and was stopped."
    )


@pytest.mark.asyncio
async def test_top_level_mention_starts_a_thread(bot, slack):
    await bot.on_app_mention(mention("hi"))
    assert slack.called("chat_postMessage")[0]["thread_ts"] == "200.000100"


@pytest.mark.asyncio
async def test_refusal_in_a_channel_is_ephemeral_and_calls_no_agent(bot, slack, fake_litellm):
    slack.users["U1"] = member("U1", is_restricted=True)
    await bot.on_app_mention(mention("hi"))

    ephemeral = slack.called("chat_postEphemeral")
    assert len(ephemeral) == 1
    assert ephemeral[0]["user"] == "U1"
    assert ephemeral[0]["thread_ts"] == "200.000100"
    assert "full members" in ephemeral[0]["text"]
    assert slack.called("chat_postMessage") == []
    assert fake_litellm.stream_requests == []


@pytest.mark.asyncio
async def test_refusal_in_a_dm_is_a_normal_reply(bot, slack, fake_litellm):
    fake_litellm.users = []
    await bot.on_message(dm("hi"))
    posts = slack.called("chat_postMessage")
    assert len(posts) == 1
    assert "log in once at the LLM portal" in posts[0]["text"]
    assert slack.called("reactions_add") == []


@pytest.mark.asyncio
async def test_stream_failure_posts_a_short_message_without_details(bot, slack, fake_litellm):
    fake_litellm.stream_responses = [httpx.Response(500, text="Traceback (most recent call last):\n  File ...")]
    await bot.on_message(dm("hi"))
    final = slack.called("chat_update")[-1]["text"]
    assert final == ":warning: The LLM gateway returned an error. Please try again in a moment."


@pytest.mark.asyncio
async def test_budget_errors_are_passed_on_briefly(bot, slack, fake_litellm):
    fake_litellm.stream_responses = [
        httpx.Response(
            429, json={"error": {"message": "Budget has been exceeded! Current cost: 10.2, Max budget: 10.0\nmore"}}
        )
    ]
    await bot.on_message(dm("hi"))
    assert slack.called("chat_update")[-1]["text"] == (
        ":warning: LiteLLM refused the request: Budget has been exceeded! Current cost: 10.2, Max budget: 10.0"
    )


@pytest.mark.asyncio
async def test_a_rejected_key_is_reminted_once(bot, slack, fake_litellm):
    fake_litellm.stream_responses = [httpx.Response(401, json={"error": {"message": "Invalid key"}})]
    await bot.on_message(dm("hi"))
    assert fake_litellm.stream_auth == ["Bearer sk-user-key-1", "Bearer sk-user-key-2"]
    assert slack.called("chat_update")[-1]["text"] == "Hi!"


@pytest.mark.asyncio
async def test_listen_all_channel_messages_are_answered_and_duplicates_dropped(
    slack, fake_litellm, litellm_client, make_identity
):
    store = AgentStore([qwen_bot(listen="all")])
    bot = SlackBot(
        client=slack,
        store=store,
        identity=make_identity(store=store),
        litellm=litellm_client,
        workspace=SlackWorkspace(TEAM),
        bot_user_id=BOT_USER,
        update_interval=0,
    )
    event = {"channel": "CWORKFLOWS", "channel_type": "channel", "user": "U1", "text": "anyone?", "ts": "400.000100"}
    await bot.on_message(event)
    await bot.on_message(event)
    await bot.on_message({**event, "text": f"<@{BOT_USER}> anyone?", "ts": "401.000100"})

    assert len(fake_litellm.stream_requests) == 1
    assert slack.called("chat_postMessage")[0]["thread_ts"] == "400.000100"


@pytest.mark.asyncio
async def test_events_from_another_workspace_are_dropped(bot, slack, fake_litellm):
    await bot.on_message(dm("hi"), {"team_id": "T0OTHER"})
    assert slack.calls == []


@pytest.mark.asyncio
async def test_dm_without_a_dm_agent_gets_a_short_note(slack, litellm_client, make_identity):
    store = AgentStore([qwen_bot(dm=False)])
    bot = SlackBot(
        client=slack,
        store=store,
        identity=make_identity(store=store),
        litellm=litellm_client,
        workspace=SlackWorkspace(TEAM),
        bot_user_id=BOT_USER,
    )
    await bot.on_message(dm("hi"))
    assert "direct messages" in slack.called("chat_postMessage")[0]["text"]


@pytest.mark.asyncio
async def test_refusals_log_a_reason_code_but_not_the_email(bot, fake_litellm, caplog):
    fake_litellm.users = []
    with caplog.at_level("INFO", logger="app.bot"):
        await bot.on_message(dm("hi"))
    assert "no_litellm_user" in caplog.text
    assert "u1@hpi.de" not in caplog.text
