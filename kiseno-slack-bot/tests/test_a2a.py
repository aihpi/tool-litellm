from __future__ import annotations

import httpx
import pytest
from app.a2a import StreamState, build_stream_request, iter_sse_data
from app.bot import render_final
from app.litellm_client import LiteLLMClient, LiteLLMError
from conftest import MINT_KEY, a2a_v1_stream, sse


async def lines(*items: str):
    for item in items:
        yield item


async def collect(source) -> list:
    return [item async for item in source]


def text_part(text: str) -> dict:
    return {"kind": "text", "text": text}


def status(state: str, text: str | None = None, *, final: bool = False) -> dict:
    status_body: dict = {"state": state}
    if text is not None:
        status_body["message"] = {"kind": "message", "role": "agent", "messageId": "m", "parts": [text_part(text)]}
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {"kind": "status-update", "taskId": "t", "contextId": "c", "status": status_body, "final": final},
    }


def artifact(text: str, *, artifact_id: str = "answer", append: bool = False) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "kind": "artifact-update",
            "taskId": "t",
            "contextId": "c",
            "artifact": {"artifactId": artifact_id, "parts": [text_part(text)]},
            "append": append,
        },
    }


@pytest.mark.asyncio
async def test_sse_parser_skips_comments_and_fields_and_joins_multiline_data():
    data = await collect(
        iter_sse_data(
            lines(
                ": keepalive",
                "",
                "event: message",
                "id: 7",
                'data: {"a":',
                "data:1}",
                "",
                "data: second\r",
                "\r",
                "",
                "data: tail-without-blank-line",
            )
        )
    )
    assert data == ['{"a":\n1}', "second", "tail-without-blank-line"]


def test_stream_request_is_one_user_message_with_one_text_part_and_slack_metadata():
    slack = {"team": "T", "channel": "C", "thread_ts": "1.2", "user": "U"}
    request = build_stream_request("Anna: hi", slack=slack, request_id="req-1")
    assert request["jsonrpc"] == "2.0"
    assert request["id"] == "req-1"
    assert request["method"] == "message/stream"
    message = request["params"]["message"]
    assert message["role"] == "user"
    assert message["kind"] == "message"
    assert message["parts"] == [{"kind": "text", "text": "Anna: hi"}]
    assert message["messageId"]
    assert request["params"]["metadata"] == {"slack": slack}


def test_status_updates_then_streamed_artifact_then_final_status():
    state = StreamState()
    state.apply(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {"kind": "task", "id": "t", "contextId": "c", "status": {"state": "submitted"}},
        }
    )
    state.apply(status("working", "calling tool crm_find"))
    assert state.status == "calling tool crm_find"
    assert state.answer is None
    state.apply(artifact("Hello "))
    state.apply(artifact("world", append=True))
    assert state.answer == "Hello world"
    assert not state.done
    state.apply(status("completed", final=True))
    assert state.done
    assert state.error is None
    assert state.answer == "Hello world"


def test_artifact_without_append_replaces_the_text():
    state = StreamState()
    state.apply(artifact("draft"))
    state.apply(artifact("final"))
    assert state.answer == "final"


def test_agent_message_is_the_answer():
    state = StreamState()
    state.apply(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {
                "kind": "message",
                "role": "agent",
                "parts": [text_part("Hi "), {"kind": "data", "data": {}}, text_part("there")],
            },
        }
    )
    assert state.answer == "Hi there"
    assert state.done


def test_completed_task_with_artifacts_is_the_answer():
    state = StreamState()
    state.apply(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {
                "kind": "task",
                "id": "t",
                "status": {"state": "completed"},
                "artifacts": [{"artifactId": "a", "parts": [text_part("Done.")]}],
            },
        }
    )
    assert state.done
    assert state.answer == "Done."


def test_final_status_text_is_the_answer_when_nothing_else_came():
    state = StreamState()
    state.apply(status("completed", "All set.", final=True))
    assert state.answer == "All set."


def test_failed_task_and_jsonrpc_error_become_errors():
    failed = StreamState()
    failed.apply(status("failed", "tool crashed", final=True))
    assert failed.error == "tool crashed"
    assert failed.done

    rpc = StreamState()
    rpc.apply({"jsonrpc": "2.0", "id": "1", "error": {"code": -32603, "message": "Budget exceeded"}})
    assert rpc.error == "Budget exceeded"
    assert rpc.done


def test_a2a_1_0_state_names_are_understood():
    state = StreamState()
    state.apply(status("TASK_STATE_INPUT_REQUIRED", "Which workshop?"))
    assert state.state == "input-required"
    assert state.done
    assert state.answer == "Which workshop?"


# LiteLLM answers in the protocol version configured on the agent, whatever the request used.
# The runner registers its agents as 1.0, so their streams reach the bot in the 1.0 shape.


def test_a2a_1_0_stream_reports_progress_then_the_answer():
    submitted, working, tool_call, answer, completed = a2a_v1_stream("completed")
    state = StreamState()
    for event in (submitted, working, tool_call):
        state.apply(event)
    assert state.state == "working"
    assert state.status == "calling tool crm_find_events"
    assert state.answer is None
    assert not state.done

    state.apply(answer)
    assert state.answer.startswith("Two workshops are planned for December:")
    assert not state.done

    state.apply(completed)
    assert state.done
    assert state.state == "completed"
    assert state.error is None
    assert render_final(state) == (
        "Two workshops are planned for December:\n\n• *RAG Forum* on 4 December\n• *MCP* on 11 December"
    )


def test_a2a_1_0_failed_task_ends_in_its_error():
    state = StreamState()
    for event in a2a_v1_stream("failed"):
        state.apply(event)
    assert state.done
    assert state.state == "failed"
    assert render_final(state) == ":warning: The agent stopped with an error: The agent took too long and was stopped."


def test_a2a_1_0_agent_message_is_the_answer_but_a_user_message_is_not():
    question = a2a_v1_stream("completed")[0]["result"]["task"]["history"][0]
    assert question["role"] == "ROLE_USER"
    state = StreamState()
    state.apply({"jsonrpc": "2.0", "id": 1, "result": {"message": question}})
    assert state.answer is None
    assert not state.done

    for event in a2a_v1_stream("message"):
        state.apply(event)
    assert state.done
    assert render_final(state) == "Hello from the agent."


@pytest.mark.asyncio
async def test_stream_agent_uses_the_user_key_and_yields_each_event():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            content=sse(status("working", "thinking"), artifact("Hi"), status("completed", final=True)),
        )

    client = LiteLLMClient("http://litellm.test", MINT_KEY, transport=httpx.MockTransport(handler))
    events = await collect(client.stream_agent("agent-id-qwen", "sk-user", build_stream_request("x", slack={})))

    assert seen == {"path": "/a2a/agent-id-qwen/message/stream", "auth": "Bearer sk-user"}
    assert [e["result"]["kind"] for e in events] == ["status-update", "artifact-update", "status-update"]


@pytest.mark.asyncio
async def test_stream_agent_yields_a_plain_jsonrpc_refusal():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "1", "error": {"code": -32000, "message": "Agent 'x' not found"}}
        )

    client = LiteLLMClient("http://litellm.test", MINT_KEY, transport=httpx.MockTransport(handler))
    events = await collect(client.stream_agent("x", "sk-user", build_stream_request("x", slack={})))
    assert events[0]["error"]["message"] == "Agent 'x' not found"


@pytest.mark.asyncio
async def test_stream_agent_raises_on_http_errors_with_the_litellm_message():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": {"message": "Budget has been exceeded! Current cost: 10.2", "code": "429"}}
        )

    client = LiteLLMClient("http://litellm.test", MINT_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(LiteLLMError) as caught:
        await collect(client.stream_agent("x", "sk-user", build_stream_request("x", slack={})))
    assert caught.value.status_code == 429
    assert caught.value.detail.startswith("Budget has been exceeded")
