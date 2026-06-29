from app.auth import build_session_user, normalize_groups, resolve_litellm_role
from app.config import Settings


def make_settings() -> Settings:
    return Settings(
        AUTHENTIK_ISSUER="https://auth.example.com/application/o/kisz-llm",
        AUTHENTIK_CLIENT_ID="kisz-llm",
        AUTHENTIK_CLIENT_SECRET="secret",
        AUTHENTIK_REDIRECT_URI="https://portal.example.com/callback",
        LITELLM_BASE_URL="http://litellm-service:4000",
        LITELLM_MASTER_KEY="sk-test",
        SESSION_SECRET="session-secret",
    )


def test_normalize_groups_handles_strings_and_lists():
    assert normalize_groups("admins") == ["admins"]
    assert normalize_groups("admins, users") == ["admins", "users"]
    assert normalize_groups(["admins", " users "]) == ["admins", "users"]


def test_resolve_litellm_role_returns_proxy_admin_for_admin_group():
    settings = make_settings()
    assert resolve_litellm_role(["kisz-admins"], settings) == "proxy_admin"
    assert resolve_litellm_role(["students"], settings) == "internal_user"


def test_build_session_user_uses_sub_for_user_id():
    settings = make_settings()
    session_user = build_session_user(
        {
            "sub": "authentik-sub-123",
            "email": "user@example.com",
            "name": "Test User",
            "groups": ["kisz-admins"],
        },
        settings,
    )

    assert session_user["user_id"] == "authentik-sub-123"
    assert session_user["email"] == "user@example.com"
    assert session_user["role"] == "proxy_admin"
