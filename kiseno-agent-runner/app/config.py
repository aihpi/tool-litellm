from __future__ import annotations

import fnmatch
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LiteLLM, reached over the cluster network (never the public host).
    litellm_base_url: str = Field(alias="LITELLM_BASE_URL")
    # Proxy-admin key limited by allowed_routes to agent management and /key/generate|delete|list.
    litellm_sync_key: str = Field(alias="LITELLM_SYNC_KEY")

    # Shared secret LiteLLM sends as `Authorization: Bearer` on every A2A call (the agents' litellm_params.api_key).
    runner_inbound_token: str = Field(alias="RUNNER_INBOUND_TOKEN")
    # How LiteLLM reaches this service, e.g. http://kisz-agent-runner.litellm.svc:8080.
    # Agents whose card URL starts with "<this>/agents/" are the ones this runner manages.
    runner_public_url: str = Field(alias="RUNNER_PUBLIC_URL")

    agents_source: Literal["kubernetes", "directory"] = Field(default="kubernetes", alias="AGENTS_SOURCE")
    agents_namespace: str = Field(default="kisz-agents", alias="AGENTS_NAMESPACE")
    agents_dir: str | None = Field(default=None, alias="AGENTS_DIR")
    sync_interval_seconds: float = Field(default=30.0, alias="SYNC_INTERVAL_SECONDS")

    # Which LiteLLM MCP access groups an agent.yaml may name. Comma-separated, shell-style wildcards, "{name}" is the
    # agent's own name. The default means an agent can only use the group named after itself, so a merged agent file
    # can never grant MCP access: only an admin adding a server to that group in LiteLLM can.
    allowed_mcp_access_groups: str = Field(default="{name}", alias="ALLOWED_MCP_ACCESS_GROUPS")

    # Agent keys get an explicit lifetime and are replaced before they run out, so a proxy-wide default or upper
    # bound on key duration (litellm_settings.default_key_generate_params / upperbound_key_generate_params) can't
    # silently break an agent.
    agent_key_duration_days: int = Field(default=30, ge=2, alias="AGENT_KEY_DURATION_DAYS")
    agent_key_renew_days: int = Field(default=3, ge=1, alias="AGENT_KEY_RENEW_DAYS")

    default_model: str = Field(default="qwen3-8-27b", alias="DEFAULT_MODEL")
    max_turns_cap: int = Field(default=20, alias="MAX_TURNS_CAP")
    request_timeout_seconds: float = Field(default=600.0, alias="REQUEST_TIMEOUT_SECONDS")
    llm_timeout_seconds: float = Field(default=300.0, alias="LLM_TIMEOUT_SECONDS")
    tool_result_max_chars: int = Field(default=20_000, alias="TOOL_RESULT_MAX_CHARS")

    @property
    def normalized_litellm_base_url(self) -> str:
        return self.litellm_base_url.rstrip("/")

    @property
    def agents_url_prefix(self) -> str:
        return f"{self.runner_public_url.rstrip('/')}/agents/"

    def agent_url(self, name: str) -> str:
        return f"{self.agents_url_prefix}{name}/"

    def disallowed_access_groups(self, agent_name: str, groups: list[str]) -> list[str]:
        patterns = [p.strip().replace("{name}", agent_name) for p in self.allowed_mcp_access_groups.split(",") if p.strip()]
        return [g for g in groups if not any(fnmatch.fnmatchcase(g, p) for p in patterns)]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
