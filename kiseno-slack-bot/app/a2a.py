"""A2A v0.3 JSON-RPC: the ``message/stream`` request and the events that come back.

LiteLLM relays the agent's stream as SSE, one ``data: <jsonrpc>`` event per A2A
event: ``message``, ``task``, ``status-update`` and ``artifact-update``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

WORKING_STATES = frozenset({"submitted", "working"})
FAILED_STATES = frozenset({"failed", "rejected", "canceled"})
WAITING_STATES = frozenset({"input-required", "auth-required"})
TERMINAL_STATES = frozenset({"completed"}) | FAILED_STATES | WAITING_STATES


def build_stream_request(text: str, *, slack: Mapping[str, Any], request_id: str | None = None) -> dict[str, Any]:
    """One user Message with one TextPart; Slack context goes into ``params.metadata.slack``."""
    return {
        "jsonrpc": "2.0",
        "id": request_id or uuid.uuid4().hex,
        "method": "message/stream",
        "params": {
            "message": {
                "kind": "message",
                "messageId": uuid.uuid4().hex,
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            },
            "metadata": {"slack": dict(slack)},
        },
    }


async def iter_sse_data(lines: AsyncIterable[str]) -> AsyncIterator[str]:
    """Yield the data of each SSE event; comments (keepalive pings) and other fields are skipped."""
    data: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
                data = []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if name == "data":
            data.append(value.removeprefix(" "))
    if data:
        yield "\n".join(data)


def _state(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    state = value.lower()
    state = state.removeprefix("task_state_")
    return state.replace("_", "-")


def parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    texts = []
    for part in parts:
        if isinstance(part, Mapping) and part.get("kind", "text") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "".join(texts)


def _message_text(message: Any) -> str:
    return parts_text(message.get("parts")) if isinstance(message, Mapping) else ""


@dataclass
class StreamState:
    """What the stream has said so far: a progress line, the answer, or an error."""

    state: str | None = None
    status: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    message: str | None = None
    final_status: str | None = None
    error: str | None = None
    done: bool = False
    events: int = 0

    @property
    def answer(self) -> str | None:
        text = "\n\n".join(t for t in self.artifacts.values() if t.strip())
        return text or self.message or self.final_status or None

    def apply(self, envelope: Mapping[str, Any]) -> None:
        self.events += 1
        error = envelope.get("error")
        if error is not None:
            message = error.get("message") if isinstance(error, Mapping) else None
            self.error = str(message or "the agent returned an error")
            self.done = True
            return
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            return
        kind = result.get("kind")
        if kind == "message":
            if result.get("role") != "user":
                self.message = _message_text(result)
                self.done = True
        elif kind == "task":
            for artifact in result.get("artifacts") or []:
                self._set_artifact(artifact, append=False)
            self._apply_status(result.get("status"), final=False)
        elif kind == "status-update":
            self._apply_status(result.get("status"), final=bool(result.get("final")))
        elif kind == "artifact-update":
            self._set_artifact(result.get("artifact"), append=bool(result.get("append")))

    def _set_artifact(self, artifact: Any, *, append: bool) -> None:
        if not isinstance(artifact, Mapping):
            return
        artifact_id = str(artifact.get("artifactId") or artifact.get("artifact_id") or len(self.artifacts))
        text = parts_text(artifact.get("parts"))
        if append and artifact_id in self.artifacts:
            self.artifacts[artifact_id] += text
        else:
            self.artifacts[artifact_id] = text

    def _apply_status(self, status: Any, *, final: bool) -> None:
        if not isinstance(status, Mapping):
            if final:
                self.done = True
            return
        state = _state(status.get("state"))
        text = _message_text(status.get("message")).strip() or None
        if state:
            self.state = state
        if state in FAILED_STATES:
            self.error = text or f"the agent's task was {state}"
        elif state in TERMINAL_STATES:
            if text:
                self.final_status = text
        elif text:
            self.status = text
        if final or state in TERMINAL_STATES:
            self.done = True
