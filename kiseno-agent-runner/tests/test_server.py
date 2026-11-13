"""End to end through the A2A protocol, with the same a2a-sdk client LiteLLM uses to call agent backends."""

import uuid
from pathlib import Path

import httpx
import pytest
from a2a.client import ClientConfig, create_client
from a2a.helpers.proto_helpers import get_artifact_text
from a2a.types.a2a_pb2 import AgentCard, Message, Part, Role, SendMessageRequest, TaskState
from google.protobuf.json_format import ParseDict

from app.main import create_app
from app.sources import DirectorySource
from fakes import FakeLiteLLM, mcp_tool, settings, text_reply, tool_reply

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agents-repo"
AUTH = {"Authorization": "Bearer inbound-secret", "X-LiteLLM-User-Id": "u-1", "X-LiteLLM-Team-Id": "t-1"}


async def started(fake: FakeLiteLLM):
    app = create_app(settings(), source=DirectorySource(EXAMPLE), transport=fake.transport(), run_sync_loop=False)
    await app.state.sync()
    return app


def http(app, headers=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner.test", headers=headers or {})


async def send(app, text: str, *, streaming: bool):
    async with http(app, AUTH) as client:
        card_json = (await client.get("/agents/qwen-bot/.well-known/agent-card.json")).json()
        card = ParseDict(card_json, AgentCard(), ignore_unknown_fields=True)
        a2a = await create_client(card, client_config=ClientConfig(httpx_client=client, streaming=streaming))
        message = Message(message_id=str(uuid.uuid4()), role=Role.ROLE_USER, parts=[Part(text=text)])
        return [event async for event in a2a.send_message(SendMessageRequest(message=message))]


@pytest.mark.asyncio
async def test_card_is_public_and_points_at_the_runner():
    app = await started(FakeLiteLLM())
    async with http(app) as client:
        response = await client.get("/agents/qwen-bot/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "qwen-bot"
    assert card["supportedInterfaces"][0]["url"] == "http://runner.test/agents/qwen-bot/"
    assert {s["id"] for s in card["skills"]} == {"meeting-summary", "calendar-lookup"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers", "status"),
    [
        ("/agents/qwen-bot/", {}, 401),
        ("/agents/qwen-bot/", {"Authorization": "Bearer wrong", "X-LiteLLM-User-Id": "u-1"}, 401),
        ("/agents/qwen-bot/", {"Authorization": "Bearer inbound-secret"}, 400),
        ("/agents/nobody/", AUTH, 404),
    ],
)
async def test_only_litellm_with_a_caller_gets_in(path, headers, status):
    app = await started(FakeLiteLLM())
    async with http(app, headers) as client:
        response = await client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}})
    assert response.status_code == status


@pytest.mark.asyncio
async def test_streaming_run_reports_progress_and_answers():
    fake = FakeLiteLLM(tools=[mcp_tool("create_event")])
    fake.chat_script = [tool_reply(("crm-create_event", {"title": "RAG"})), text_reply("Created the RAG workshop.")]
    app = await started(fake)

    events = await send(app, "Trag den RAG-Workshop ein", streaming=True)

    statuses = [e.status_update.status for e in events if e.HasField("status_update")]
    texts = [p.text for s in statuses if s.HasField("message") for p in s.message.parts]
    assert "Calling crm-create_event" in texts
    assert statuses[-1].state == TaskState.TASK_STATE_COMPLETED
    artifacts = [get_artifact_text(e.artifact_update.artifact) for e in events if e.HasField("artifact_update")]
    assert artifacts == ["Created the RAG workshop."]

    (tool_call,) = fake.calls_to("POST", "/mcp-rest/tools/call")
    assert tool_call.headers["x-litellm-user-id"] == "u-1"
    assert tool_call.headers["authorization"].startswith("Bearer sk-agent-")
    user_turn = fake.calls_to("POST", "/v1/chat/completions")[0].body["messages"][-1]
    assert user_turn == {"role": "user", "content": "Trag den RAG-Workshop ein"}


@pytest.mark.asyncio
async def test_blocking_send_ends_in_a_completed_task():
    fake = FakeLiteLLM()
    fake.chat_script = [text_reply("Hallo!")]
    app = await started(fake)

    events = await send(app, "Hallo", streaming=False)
    task = events[-1].task
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert [get_artifact_text(a) for a in task.artifacts] == ["Hallo!"]


@pytest.mark.asyncio
async def test_model_failure_fails_the_task_without_crashing():
    fake = FakeLiteLLM()  # empty chat script -> LiteLLM answers 500
    app = await started(fake)
    events = await send(app, "Hallo", streaming=False)
    assert events[-1].task.status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_readiness_follows_the_first_sync():
    app = create_app(settings(), source=DirectorySource(EXAMPLE), transport=FakeLiteLLM().transport(), run_sync_loop=False)
    async with http(app) as client:
        assert (await client.get("/readyz")).status_code == 503
        await app.state.sync()
        ready = await client.get("/readyz")
        status = (await client.get("/status")).json()
    assert ready.status_code == 200 and ready.json()["agents"] == ["qwen-bot"]
    assert status["last_report"]["created"] == ["qwen-bot"]
    assert "sk-" not in str(status)
