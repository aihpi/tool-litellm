"""An in-memory stand-in for the LiteLLM endpoints the runner uses, served through httpx.MockTransport."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings


def settings(**overrides: Any) -> Settings:
    values = {
        "LITELLM_BASE_URL": "http://litellm.test",
        "LITELLM_SYNC_KEY": "sk-sync",
        "RUNNER_INBOUND_TOKEN": "inbound-secret",
        "RUNNER_PUBLIC_URL": "http://runner.test",
        "AGENTS_SOURCE": "directory",
        "AGENTS_DIR": "/nonexistent",
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportCallIssue]


@dataclass
class Call:
    method: str
    path: str
    headers: dict[str, str]
    body: Any
    params: dict[str, str]


@dataclass
class FakeLiteLLM:
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    keys: dict[str, dict[str, Any]] = field(default_factory=dict)  # alias -> key record
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    chat_script: list[dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls_to(self, method: str, path_prefix: str) -> list[Call]:
        return [c for c in self.calls if c.method == method and c.path.startswith(path_prefix)]

    def add_agent(self, name: str, url: str, version: str = "") -> str:
        agent_id = str(uuid.uuid4())
        self.agents[agent_id] = {
            "agent_id": agent_id,
            "agent_name": name,
            "agent_card_params": {"name": name, "url": url, "version": version},
            "litellm_params": {},
        }
        return agent_id

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append(Call(request.method, path, dict(request.headers), body, dict(request.url.params)))

        if path == "/v1/agents" and request.method == "GET":
            return httpx.Response(200, json=list(self.agents.values()))
        if path == "/v1/agents" and request.method == "POST":
            agent_id = str(uuid.uuid4())
            self.agents[agent_id] = {"agent_id": agent_id, **body}
            return httpx.Response(200, json=self.agents[agent_id])
        if path.startswith("/v1/agents/"):
            agent_id = path.rsplit("/", 1)[-1]
            if agent_id not in self.agents:
                return httpx.Response(404, json={"detail": "not found"})
            if request.method == "PUT":
                self.agents[agent_id] = {"agent_id": agent_id, **body}
                return httpx.Response(200, json=self.agents[agent_id])
            if request.method == "DELETE":
                del self.agents[agent_id]
                return httpx.Response(200, json={})
        if path == "/key/generate":
            key = f"sk-agent-{uuid.uuid4().hex[:8]}"
            self.keys[body["key_alias"]] = {"key": key, **body}
            return httpx.Response(200, json={"key": key, "key_alias": body["key_alias"]})
        if path == "/key/list":
            agent_id = request.url.params.get("agent_id")
            keys = [{"key_alias": a, "agent_id": k["agent_id"]} for a, k in self.keys.items() if k.get("agent_id") == agent_id]
            return httpx.Response(200, json={"keys": keys, "total_count": len(keys), "current_page": 1, "total_pages": 1})
        if path == "/key/delete":
            for alias in body.get("key_aliases") or []:
                self.keys.pop(alias, None)
            return httpx.Response(200, json={"deleted_keys": body.get("key_aliases")})
        if path == "/mcp-rest/tools/list":
            return httpx.Response(200, json={"tools": self.tools, "error": None})
        if path == "/mcp-rest/tools/call":
            result = self.tool_results.get(body["name"], {"content": [{"type": "text", "text": f"ran {body['name']}"}], "isError": False})
            return httpx.Response(200, json=result)
        if path == "/v1/chat/completions":
            if not self.chat_script:
                return httpx.Response(500, json={"error": "chat script exhausted"})
            step = self.chat_script.pop(0)
            return httpx.Response(200, json=step(body) if callable(step) else step)
        return httpx.Response(404, json={"detail": f"no fake for {request.method} {path}"})


def text_reply(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def tool_reply(*calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                        for i, (name, args) in enumerate(calls)
                    ],
                }
            }
        ]
    }


def mcp_tool(name: str, description: str = "", server: str = "espocrm") -> dict[str, Any]:
    return {
        "name": name,
        "description": description or f"{name} tool",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
        "mcp_info": {"server_name": server},
    }
