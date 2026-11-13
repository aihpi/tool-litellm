"""Regenerate a2a_v1_stream.json: runner-shaped v0.3 stream events, converted to A2A 1.0 by LiteLLM itself.

LiteLLM serves an agent in the protocol version configured on the agent, so for the
runner's agents (``protocolVersion: "1.0"``) it rewrites every streamed event with
``normalize_stream_event`` before the bot sees it. This feeds the events that
kiseno-agent-runner emits through that same function, so the tests need neither
litellm nor a2a-sdk. Run it from the repository root after LiteLLM's converter changes:

    uv run --no-project --with a2a-sdk==1.1.0 python kiseno-slack-bot/tests/fixtures/make_a2a_v1_stream.py
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

TASK = "3f1c9a52-7d8e-4b61-9f0a-2c4e8b7d1a90"
CONTEXT = "b2e4d6f8-1a3c-4e5f-8a7b-9c0d1e2f3a4b"
TIMESTAMP = "2026-11-13T09:15:02.481Z"


def load_version_convert() -> types.ModuleType:
    """Only litellm/proxy/a2a/version_convert.py: the package itself needs the built Rust module.

    Its two litellm imports are a logger and a card helper that the stream path never calls.
    """
    for name in ("litellm", "litellm.proxy", "litellm.proxy.a2a"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["litellm._logging"] = types.SimpleNamespace(verbose_proxy_logger=logging.getLogger("litellm"))
    sys.modules["litellm.proxy.a2a.agent_card"] = types.SimpleNamespace(normalize_protocol_version=None)
    spec = importlib.util.spec_from_file_location("version_convert", REPO / "litellm/proxy/a2a/version_convert.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def message(message_id: str, role: str, text: str) -> dict:
    return {
        "kind": "message",
        "messageId": message_id,
        "role": role,
        "parts": [{"kind": "text", "text": text}],
        "taskId": TASK,
        "contextId": CONTEXT,
    }


def status(state: str, *, final: bool = False, text: str | None = None, message_id: str = "") -> dict:
    body: dict = {"state": state, "timestamp": TIMESTAMP}
    if text is not None:
        body["message"] = message(message_id, "agent", text)
    return {"kind": "status-update", "taskId": TASK, "contextId": CONTEXT, "status": body, "final": final}


QUESTION = message("9d8c7b6a-5f4e-4d3c-2b1a-0f9e8d7c6b5a", "user", "Felix: Which workshops are planned for December?")
SUBMITTED = {
    "kind": "task",
    "id": TASK,
    "contextId": CONTEXT,
    "status": {"state": "submitted", "timestamp": TIMESTAMP},
    "history": [QUESTION],
}

# What the runner's executor emits: the task, start_work(), one status per tool call,
# the "answer" artifact, then complete() or failed(message).
STREAMS = {
    "completed": [
        SUBMITTED,
        status("working"),
        status("working", text="calling tool espocrm_find_events", message_id="4a5b6c7d-8e9f-4a0b-1c2d-3e4f5a6b7c8d"),
        {
            "kind": "artifact-update",
            "taskId": TASK,
            "contextId": CONTEXT,
            "artifact": {
                "artifactId": "7e6d5c4b-3a29-4180-9f7e-6d5c4b3a2918",
                "name": "answer",
                "parts": [
                    {
                        "kind": "text",
                        "text": "Two workshops are planned for December:\n\n"
                        "- **RAG Forum** on 4 December\n- **MCP** on 11 December",
                    }
                ],
            },
        },
        status("completed", final=True),
    ],
    "failed": [
        SUBMITTED,
        status("working"),
        status(
            "failed",
            final=True,
            text="The agent took too long and was stopped.",
            message_id="0c1d2e3f-4a5b-4c6d-7e8f-9a0b1c2d3e4f",
        ),
    ],
    "message": [message("5f6e7d8c-9b0a-4f1e-2d3c-4b5a6f7e8d9c", "agent", "Hello from the agent.")],
}


def main() -> None:
    version_convert = load_version_convert()
    converted = {}
    for name, events in STREAMS.items():
        converted[name] = []
        for event in events:
            # normalize_stream_event hands back the v0.3 event if conversion fails; call the converter first to see why.
            version_convert._stream_result_to(event, "1.0", 1)
            envelope = version_convert.normalize_stream_event(
                {"jsonrpc": "2.0", "id": 1, "result": event}, "1.0", request_id=1
            )
            assert "kind" not in envelope["result"], envelope
            converted[name].append(envelope)
    (HERE / "a2a_v1_stream.json").write_text(json.dumps(converted, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
