import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from litellm.proxy._types import LitellmUserRoles, ProxyException
from litellm.proxy.management_endpoints.types import CustomOpenID
from litellm.repositories.user_repository import UserRepository

EXISTING_USERS = 10


def load_copy():
    spec = importlib.util.spec_from_file_location("aihpi_ui_sso", Path(__file__).parent / "authentik" / "ui_sso.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UserTable:
    def __init__(self):
        self.created = []

    async def count(self, where=None):
        return 0 if where else EXISTING_USERS

    async def find_unique(self, where):
        return None

    async def upsert(self, where, data):
        self.created.append(where["user_id"])
        return SimpleNamespace(user_id=where["user_id"])


class Prisma:
    def __init__(self):
        self.litellm_usertable = UserTable()
        self.db = self

    async def execute_raw(self, query):
        return 0

    async def query_raw(self, query):
        return [{"locked": 1}]

    @asynccontextmanager
    async def tx(self):
        yield self


@pytest.fixture
def first_login(monkeypatch):
    module = load_copy()
    prisma = Prisma()

    async def new_user(data, user_api_key_dict):
        row = await UserRepository(prisma).upsert_user({"user_id": data.user_id})
        if row is None:
            raise ProxyException(message="seat limit", type="auth_error", param=None, code=403)
        return row

    monkeypatch.setattr(module, "new_user", new_user)
    monkeypatch.setattr("litellm.proxy.proxy_server.premium_user", False)

    async def login(provider):
        result = CustomOpenID(id="sub-123", email="new@example.org", provider=provider, team_ids=[])
        values = {
            "models": [],
            "user_id": "sub-123",
            "user_email": "new@example.org",
            "user_role": LitellmUserRoles.INTERNAL_USER.value,
            "max_budget": None,
            "budget_duration": None,
        }
        await module.insert_sso_user(result_openid=result, user_defined_values=values)
        return prisma.litellm_usertable.created

    return login


@pytest.mark.asyncio
async def test_first_authentik_login_is_not_capped_by_the_free_seat_limit(first_login):
    assert await first_login("authentik") == ["sub-123"]


@pytest.mark.asyncio
async def test_other_sso_providers_still_hit_the_free_seat_limit(first_login):
    with pytest.raises(ProxyException):
        await first_login("generic")
