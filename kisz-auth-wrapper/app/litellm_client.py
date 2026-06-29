from __future__ import annotations

import json
from typing import Any

import httpx


class LiteLLMAPIError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class LiteLLMNotFoundError(LiteLLMAPIError):
    pass


class LiteLLMClient:
    def __init__(
        self,
        base_url: str,
        master_key: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.master_key = master_key
        self.timeout = timeout
        self.transport = transport

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.master_key}",
            "Content-Type": "application/json",
            "litellm-changed-by": "kisz-auth-wrapper",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, json=json_body, params=params)

        if response.status_code == 404:
            raise LiteLLMNotFoundError(response.status_code, self._extract_detail(response))
        if response.is_error:
            raise LiteLLMAPIError(response.status_code, self._extract_detail(response))

        if not response.content:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    @staticmethod
    def _extract_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text or response.reason_phrase

        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error") or payload
        else:
            detail = payload

        if isinstance(detail, (dict, list)):
            return json.dumps(detail)
        return str(detail)

    async def get_user_info(self, user_id: str) -> dict[str, Any]:
        return await self._request("GET", "/user/info", params={"user_id": user_id})

    async def create_user(
        self,
        *,
        user_id: str,
        user_email: str | None,
        user_role: str,
        max_budget: float | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "user_id": user_id,
            "user_email": user_email,
            "user_role": user_role,
            "auto_create_key": False,
        }
        if max_budget is not None:
            payload["max_budget"] = max_budget
        return await self._request("POST", "/user/new", json_body=payload)

    async def update_user(
        self,
        *,
        user_id: str,
        user_email: str | None,
        user_role: str,
        max_budget: float | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "user_id": user_id,
            "user_email": user_email,
            "user_role": user_role,
        }
        if max_budget is not None:
            payload["max_budget"] = max_budget
        return await self._request("POST", "/user/update", json_body=payload)

    async def ensure_user(
        self,
        *,
        user_id: str,
        user_email: str | None,
        user_role: str,
        max_budget: float | None,
    ) -> dict[str, Any]:
        try:
            existing = await self.get_user_info(user_id)
        except LiteLLMNotFoundError:
            return await self.create_user(
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
                max_budget=max_budget,
            )

        current_user = existing.get("user_info") or {}
        if (
            current_user.get("user_email") != user_email
            or current_user.get("user_role") != user_role
        ):
            await self.update_user(
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
                max_budget=max_budget,
            )
        return existing

    async def generate_key(
        self,
        *,
        user_id: str,
        key_alias: str | None = None,
        max_budget: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"user_id": user_id}
        if key_alias:
            payload["key_alias"] = key_alias
        if max_budget is not None:
            payload["max_budget"] = max_budget
        return await self._request("POST", "/key/generate", json_body=payload)

    async def delete_key(self, key: str) -> dict[str, Any]:
        return await self._request("POST", "/key/delete", json_body={"keys": [key]})
