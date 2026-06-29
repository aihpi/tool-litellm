from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from litellm_enterprise.proxy.management_endpoints.internal_user_endpoints import (
    router,
    user_api_key_auth,
)
from litellm.proxy.auth.auth_utils import _has_free_sso_user_limit


@pytest.fixture
def client():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[user_api_key_auth] = lambda: {
        "user_id": "test_user",
        "api_key": "test_key",
    }
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_sso_env(monkeypatch):
    for env_var in (
        "GOOGLE_CLIENT_ID",
        "MICROSOFT_CLIENT_ID",
        "GENERIC_CLIENT_ID",
        "AUTHENTIK_CLIENT_ID",
    ):
        monkeypatch.delenv(env_var, raising=False)


@pytest.fixture
def mock_proxy_server_module():
    mock_proxy_server = ModuleType("litellm.proxy.proxy_server")
    mock_proxy_server.prisma_client = None
    mock_proxy_server.premium_user = True
    mock_proxy_server.premium_user_data = None

    with patch.dict(
        "sys.modules", {"litellm.proxy.proxy_server": mock_proxy_server}
    ):
        yield mock_proxy_server


class TestAvailableEnterpriseUsers:
    @pytest.mark.asyncio
    async def test_available_users_does_not_apply_free_sso_limit_for_authentik(
        self, client, mock_proxy_server_module
    ):
        """Test that fork-specific Authentik SSO does not show the legacy 5-user meter."""
        mock_proxy_server_module.prisma_client = AsyncMock()
        mock_proxy_server_module.prisma_client.db.litellm_usertable.count = AsyncMock(
            return_value=4
        )
        mock_proxy_server_module.prisma_client.db.litellm_teamtable.count = AsyncMock(
            return_value=1
        )
        mock_proxy_server_module.premium_user = False
        mock_proxy_server_module.premium_user_data = None
        with patch(
            "litellm.proxy.auth.auth_utils._has_free_sso_user_limit",
            return_value=False,
        ):
            response = client.get("/user/available_users")

        assert response.status_code == 200
        data = response.json()

        assert data["total_users"] is None
        assert data["total_users_used"] == 4
        assert data["total_users_remaining"] is None

    @pytest.mark.asyncio
    async def test_available_users_with_max_users_set(
        self, client, mock_proxy_server_module
    ):
        """Test when max_users is set and user count is within limit"""
        mock_proxy_server_module.prisma_client = AsyncMock()
        mock_proxy_server_module.prisma_client.db.litellm_usertable.count = AsyncMock(
            return_value=5
        )
        mock_proxy_server_module.prisma_client.db.litellm_teamtable.count = AsyncMock(
            return_value=2
        )
        mock_proxy_server_module.premium_user = True
        mock_proxy_server_module.premium_user_data = {"max_users": 10}

        response = client.get("/user/available_users")

        assert response.status_code == 200
        data = response.json()

        assert data["total_users"] == 10
        assert data["total_users_used"] == 5
        assert data["total_users_remaining"] == 5
        assert data["total_teams"] is None
        assert data["total_teams_used"] == 2
        assert data["total_teams_remaining"] is None
        assert data["total_users_remaining"] >= 0

    @pytest.mark.asyncio
    async def test_available_users_without_max_users_set(
        self, client, mock_proxy_server_module
    ):
        """Test when max_users is not set (premium_user_data is None or doesn't contain max_users)"""
        mock_proxy_server_module.prisma_client = AsyncMock()
        mock_proxy_server_module.prisma_client.db.litellm_usertable.count = AsyncMock(
            return_value=3
        )
        mock_proxy_server_module.prisma_client.db.litellm_teamtable.count = AsyncMock(
            return_value=1
        )
        mock_proxy_server_module.premium_user = True
        mock_proxy_server_module.premium_user_data = None

        response = client.get("/user/available_users")

        assert response.status_code == 200
        data = response.json()

        assert data["total_users"] is None
        assert data["total_users_used"] == 3
        assert data["total_users_remaining"] is None
        assert data["total_teams"] is None
        assert data["total_teams_used"] == 1
        assert data["total_teams_remaining"] is None

    @pytest.mark.asyncio
    async def test_available_users_negative_remaining_bug(
        self, client, mock_proxy_server_module
    ):
        """Test the current bug where total_users_remaining can be negative"""
        mock_proxy_server_module.prisma_client = AsyncMock()
        mock_proxy_server_module.prisma_client.db.litellm_usertable.count = AsyncMock(
            return_value=8
        )
        mock_proxy_server_module.prisma_client.db.litellm_teamtable.count = AsyncMock(
            return_value=3
        )
        mock_proxy_server_module.premium_user = True
        mock_proxy_server_module.premium_user_data = {"key": "value"}

        response = client.get("/user/available_users")

        assert response.status_code == 200
        data = response.json()

        print(f"data: {data}")

        assert data["total_users"] == None
        assert data["total_users_used"] == 8
        assert data["total_teams"] == None
        assert data["total_teams_used"] == 3
        assert data["total_users_remaining"] == None
        assert data["total_teams_remaining"] == None

    @pytest.mark.asyncio
    async def test_available_users_no_database_connection(
        self, client, mock_proxy_server_module
    ):
        """Test when prisma_client is None (no database connection)"""
        from litellm.proxy._types import CommonProxyErrors

        mock_proxy_server_module.prisma_client = None
        mock_proxy_server_module.premium_user = True

        response = client.get("/user/available_users")

        assert response.status_code == 500
        assert (
            CommonProxyErrors.db_not_connected_error.value
            in response.json()["detail"]["error"]
        )


class TestFreeSSOUserLimit:
    def test_has_free_sso_user_limit_returns_true_for_google(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")

        assert _has_free_sso_user_limit() is True

    def test_has_free_sso_user_limit_returns_false_for_authentik_only(
        self, monkeypatch
    ):
        monkeypatch.setenv("AUTHENTIK_CLIENT_ID", "authentik-client-id")

        assert _has_free_sso_user_limit() is False
