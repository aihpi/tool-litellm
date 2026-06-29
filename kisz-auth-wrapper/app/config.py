from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    authentik_issuer: str = Field(alias="AUTHENTIK_ISSUER")
    authentik_client_id: str = Field(alias="AUTHENTIK_CLIENT_ID")
    authentik_client_secret: str = Field(alias="AUTHENTIK_CLIENT_SECRET")
    authentik_redirect_uri: str = Field(alias="AUTHENTIK_REDIRECT_URI")

    litellm_base_url: str = Field(alias="LITELLM_BASE_URL")
    litellm_master_key: str = Field(alias="LITELLM_MASTER_KEY")

    session_secret: str = Field(alias="SESSION_SECRET")
    default_user_budget: float = Field(default=10.0, alias="DEFAULT_USER_BUDGET")
    default_user_role: str = Field(default="internal_user", alias="DEFAULT_USER_ROLE")
    admin_authentik_group: str = Field(
        default="kisz-admins", alias="ADMIN_AUTHENTIK_GROUP"
    )
    authentik_groups_claim: str = Field(
        default="groups", alias="AUTHENTIK_GROUPS_CLAIM"
    )
    session_https_only: bool = Field(default=True, alias="SESSION_HTTPS_ONLY")

    @property
    def normalized_litellm_base_url(self) -> str:
        return self.litellm_base_url.rstrip("/")

    @property
    def server_metadata_url(self) -> str:
        issuer = self.authentik_issuer.rstrip("/")
        return f"{issuer}/.well-known/openid-configuration"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
