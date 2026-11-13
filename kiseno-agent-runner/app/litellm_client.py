"""HTTP calls to LiteLLM: admin calls for the sync, and model and MCP calls made with an agent key for a caller."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

USER_ID_HEADER = "X-LiteLLM-User-Id"
TEAM_ID_HEADER = "X-LiteLLM-Team-Id"
TRACE_ID_HEADER = "X-LiteLLM-Trace-Id"


class LiteLLMAPIError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"LiteLLM returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or response.reason_phrase)[:500]
    if isinstance(payload, dict):
        payload = payload.get("detail") or payload.get("error") or payload
    return (json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload))[:500]


async def _json(response: httpx.Response) -> Any:
    if response.is_error:
        raise LiteLLMAPIError(response.status_code, _detail(response))
    return response.json() if response.content else {}


@dataclass(frozen=True)
class Caller:
    """The human behind a request, as LiteLLM forwarded it. Echoed back so LiteLLM caps the agent key at their grants."""

    user_id: str
    team_id: str | None = None
    trace_id: str | None = None

    def headers(self) -> dict[str, str]:
        headers = {USER_ID_HEADER: self.user_id}
        if self.team_id:
            headers[TEAM_ID_HEADER] = self.team_id
        if self.trace_id:
            headers[TRACE_ID_HEADER] = self.trace_id
        return headers


class LiteLLMAdmin:
    """Agent and key management with the sync key. Never logs or returns key material except from generate_key."""

    def __init__(self, client: httpx.AsyncClient, sync_key: str):
        self._client = client
        self._headers = {"Authorization": f"Bearer {sync_key}", "litellm-changed-by": "kisz-agent-runner"}

    async def list_agents(self) -> list[dict[str, Any]]:
        payload = await _json(await self._client.get("/v1/agents", headers=self._headers))
        if isinstance(payload, dict):
            payload = payload.get("agents") or payload.get("data") or []
        return list(payload)

    async def create_agent(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return await _json(await self._client.post("/v1/agents", json=body, headers=self._headers))

    async def update_agent(self, agent_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return await _json(await self._client.put(f"/v1/agents/{agent_id}", json=body, headers=self._headers))

    async def delete_agent(self, agent_id: str) -> None:
        await _json(await self._client.delete(f"/v1/agents/{agent_id}", headers=self._headers))

    async def generate_key(self, body: Mapping[str, Any]) -> str:
        payload = await _json(await self._client.post("/key/generate", json=body, headers=self._headers))
        key = payload.get("key") if isinstance(payload, dict) else None
        if not isinstance(key, str) or not key:
            raise LiteLLMAPIError(500, "key/generate returned no key")
        return key

    async def key_aliases_for_agent(self, agent_id: str, alias_prefix: str) -> list[str]:
        aliases: list[str] = []
        page = 1
        while True:
            payload = await _json(
                await self._client.get(
                    "/key/list",
                    params={"agent_id": agent_id, "return_full_object": "true", "page": page, "size": 100},
                    headers=self._headers,
                )
            )
            keys = payload.get("keys") or [] if isinstance(payload, dict) else []
            for key in keys:
                alias = key.get("key_alias") if isinstance(key, dict) else None
                if isinstance(alias, str) and alias.startswith(alias_prefix):
                    aliases.append(alias)
            total_pages = payload.get("total_pages") if isinstance(payload, dict) else None
            if not keys or not isinstance(total_pages, int) or page >= total_pages:
                return aliases
            page += 1

    async def delete_keys_by_alias(self, aliases: Sequence[str]) -> None:
        if aliases:
            await _json(await self._client.post("/key/delete", json={"key_aliases": list(aliases)}, headers=self._headers))


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str | None


class LiteLLMGateway:
    """Model and MCP calls with an agent's own key plus the caller's identity headers."""

    def __init__(self, client: httpx.AsyncClient, *, llm_timeout: float):
        self._client = client
        self._llm_timeout = llm_timeout

    @staticmethod
    def _headers(agent_key: str, caller: Caller) -> dict[str, str]:
        return {"Authorization": f"Bearer {agent_key}", **caller.headers()}

    async def chat(self, agent_key: str, caller: Caller, body: Mapping[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/chat/completions", json=body, headers=self._headers(agent_key, caller), timeout=self._llm_timeout
        )
        return await _json(response)

    async def list_tools(self, agent_key: str, caller: Caller) -> list[McpTool]:
        payload = await _json(await self._client.get("/mcp-rest/tools/list", headers=self._headers(agent_key, caller)))
        tools = []
        for raw in payload.get("tools") or [] if isinstance(payload, dict) else []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            schema = raw.get("inputSchema") or raw.get("input_schema") or {}
            info = raw.get("mcp_info") or {}
            tools.append(
                McpTool(
                    name=str(raw["name"]),
                    description=str(raw.get("description") or ""),
                    input_schema=schema if isinstance(schema, dict) else {},
                    server_name=info.get("server_name") if isinstance(info, dict) else None,
                )
            )
        return tools

    async def call_tool(self, agent_key: str, caller: Caller, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            "/mcp-rest/tools/call",
            json={"name": name, "arguments": dict(arguments)},
            headers=self._headers(agent_key, caller),
            timeout=self._llm_timeout,
        )
        payload = await _json(response)
        return payload if isinstance(payload, dict) else {"content": [{"type": "text", "text": json.dumps(payload)}]}
