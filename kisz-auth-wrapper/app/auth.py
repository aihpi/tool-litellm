from __future__ import annotations

from typing import Any, Iterable

from authlib.integrations.starlette_client import OAuth

from app.config import Settings


def create_authentik_client(settings: Settings):
    oauth = OAuth()
    return oauth.register(
        name="authentik",
        server_metadata_url=settings.server_metadata_url,
        client_id=settings.authentik_client_id,
        client_secret=settings.authentik_client_secret,
        client_kwargs={"scope": "openid email profile"},
    )


def normalize_groups(raw_groups: Any) -> list[str]:
    if raw_groups is None:
        return []

    if isinstance(raw_groups, str):
        if "," in raw_groups:
            return [group.strip() for group in raw_groups.split(",") if group.strip()]
        if raw_groups.strip():
            return [raw_groups.strip()]
        return []

    if isinstance(raw_groups, Iterable):
        groups: list[str] = []
        for item in raw_groups:
            if item is None:
                continue
            value = str(item).strip()
            if value:
                groups.append(value)
        return groups

    value = str(raw_groups).strip()
    return [value] if value else []


def resolve_litellm_role(groups: list[str], settings: Settings) -> str:
    if settings.admin_authentik_group in groups:
        return "proxy_admin"
    return settings.default_user_role


def build_session_user(claims: dict[str, Any], settings: Settings) -> dict[str, Any]:
    subject = str(claims["sub"])
    email = claims.get("email")
    groups = normalize_groups(claims.get(settings.authentik_groups_claim))
    role = resolve_litellm_role(groups, settings)

    return {
        "user_id": subject,
        "email": email,
        "name": claims.get("name") or email or subject,
        "groups": groups,
        "role": role,
    }
