from app.config import Settings


def test_settings_normalize_urls():
    settings = Settings(
        AUTHENTIK_ISSUER="https://auth.example.com/application/o/kisz-llm/",
        AUTHENTIK_CLIENT_ID="kisz-llm",
        AUTHENTIK_CLIENT_SECRET="secret",
        AUTHENTIK_REDIRECT_URI="https://portal.example.com/callback",
        LITELLM_BASE_URL="http://litellm-service:4000/",
        LITELLM_MASTER_KEY="sk-test",
        SESSION_SECRET="session-secret",
    )

    assert settings.normalized_litellm_base_url == "http://litellm-service:4000"
    assert (
        settings.server_metadata_url
        == "https://auth.example.com/application/o/kisz-llm/.well-known/openid-configuration"
    )
