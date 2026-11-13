"""Calls to LiteLLM: admin lookups with the minting key, agent calls with a per-user key."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import quote

import httpx

from app.a2a import iter_sse_data

USER_PAGE_SIZE = 100
MAX_USER_PAGES = 10
# The agent may go quiet for minutes while a tool runs; LiteLLM only sends
# keepalives when an operator enables them.
STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
CHANGED_BY = "kiseno-slack-bot"


class LiteLLMError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"LiteLLM returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _extract_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or response.reason_phrase)[:500]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:500]
        detail = payload.get("detail") or error or payload
    else:
        detail = payload
    if isinstance(detail, (dict, list)):
        return json.dumps(detail)[:500]
    return str(detail)[:500]


class LiteLLMClient:
    def __init__(
        self,
        base_url: str,
        mint_key: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._admin = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {mint_key}",
                "litellm-changed-by": CHANGED_BY,
            },
            timeout=timeout,
            transport=transport,
        )
        # Agent calls carry the user's key only, never the minting key.
        self._agents = httpx.AsyncClient(base_url=self.base_url, timeout=STREAM_TIMEOUT, transport=transport)

    async def aclose(self) -> None:
        await self._admin.aclose()
        await self._agents.aclose()

    async def _admin_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._admin.request(method, path, params=params, json=json_body)
        if response.is_error:
            raise LiteLLMError(response.status_code, _extract_detail(response))
        return response.json() if response.content else {}

    async def find_users_by_email(self, email: str) -> list[dict[str, Any]]:
        """Users whose email contains ``email`` (LiteLLM matches partially and case-insensitively)."""
        users: list[dict[str, Any]] = []
        for page in range(1, MAX_USER_PAGES + 1):
            payload = await self._admin_request(
                "GET",
                "/user/list",
                params={"user_email": email, "page": page, "page_size": USER_PAGE_SIZE},
            )
            users.extend(u for u in payload.get("users") or [] if isinstance(u, dict))
            if page >= int(payload.get("total_pages") or 1):
                break
        return users

    async def team_member_ids(self, team_id: str) -> set[str] | None:
        """User ids in ``members_with_roles`` of the team, or None if the team does not exist."""
        try:
            payload = await self._admin_request("GET", "/team/info", params={"team_id": team_id})
        except LiteLLMError as exc:
            if exc.status_code in (400, 404):
                return None
            raise
        info = payload.get("team_info") or {}
        members = info.get("members_with_roles") or []
        return {m["user_id"] for m in members if isinstance(m, dict) and m.get("user_id")}

    async def list_agents(self) -> list[dict[str, Any]]:
        payload = await self._admin_request("GET", "/v1/agents")
        if isinstance(payload, list):
            return [a for a in payload if isinstance(a, dict)]
        return [a for a in payload.get("data") or [] if isinstance(a, dict)]

    async def generate_key(
        self,
        *,
        user_id: str,
        team_id: str | None,
        agent_ids: list[str],
        slack_user_id: str,
        duration: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "user_id": user_id,
            "duration": duration,
            "allowed_routes": ["agent_inference_routes"],
            "object_permission": {"agents": agent_ids},
            "metadata": {"managed_by": CHANGED_BY, "slack_user_id": slack_user_id},
        }
        if team_id:
            # The team's budget, model access and MCP grants apply, and spend shows under the team.
            body["team_id"] = team_id
        return await self._admin_request("POST", "/key/generate", json_body=body)

    async def stream_agent(
        self, agent: str, user_key: str, request: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """POST a JSON-RPC ``message/stream`` request and yield each JSON-RPC object from the SSE stream."""
        path = f"/a2a/{quote(agent, safe='')}/message/stream"
        async with self._agents.stream(
            "POST",
            path,
            json=request,
            headers={"Authorization": f"Bearer {user_key}", "Accept": "text/event-stream"},
        ) as response:
            if response.is_error:
                await response.aread()
                raise LiteLLMError(response.status_code, _extract_detail(response))
            if "text/event-stream" not in response.headers.get("content-type", ""):
                # Refusals such as "agent not found" come back as one JSON-RPC object.
                await response.aread()
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise LiteLLMError(response.status_code, "unexpected response body") from exc
                if isinstance(payload, dict):
                    yield payload
                return
            async for data in iter_sse_data(response.aiter_lines()):
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue
                if isinstance(payload, dict):
                    yield payload
