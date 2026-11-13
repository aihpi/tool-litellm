from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    slack_app_token: SecretStr = Field(alias="SLACK_APP_TOKEN")
    slack_bot_token: SecretStr = Field(alias="SLACK_BOT_TOKEN")
    # Read from auth.test with the bot token; set only to override the workspace.
    slack_team_id: str | None = Field(default=None, alias="SLACK_TEAM_ID")

    litellm_base_url: str = Field(alias="LITELLM_BASE_URL")
    litellm_mint_key: SecretStr = Field(alias="LITELLM_MINT_KEY")
    litellm_team_id: str | None = Field(default=None, alias="LITELLM_TEAM_ID")

    agents_namespace: str = Field(default="kiseno-agents", alias="AGENTS_NAMESPACE")
    agents_poll_seconds: float = Field(default=60.0, alias="AGENTS_POLL_SECONDS")

    llm_portal_url: str | None = Field(default=None, alias="LLM_PORTAL_URL")
    access_help_text: str | None = Field(default=None, alias="ACCESS_HELP_TEXT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def normalized_litellm_base_url(self) -> str:
        return self.litellm_base_url.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment
