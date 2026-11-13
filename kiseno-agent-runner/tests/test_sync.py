from pathlib import Path

import httpx
import pytest

from app.litellm_client import LiteLLMAdmin
from app.spec import build_catalog, load_directory
from app.sync import KEY_ALIAS_PREFIX, Reconciler, Registry
from fakes import FakeLiteLLM, settings

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agents-repo"
MANAGED_URL = "http://runner.test/agents/"


def make(fake: FakeLiteLLM, **overrides):
    s = settings(**overrides)
    client = httpx.AsyncClient(base_url=s.normalized_litellm_base_url, transport=fake.transport())
    registry = Registry()
    return Reconciler(LiteLLMAdmin(client, s.litellm_sync_key), s, registry), registry


@pytest.mark.asyncio
async def test_creates_agent_and_key():
    fake = FakeLiteLLM()
    reconciler, registry = make(fake)
    report = await reconciler.reconcile(load_directory(EXAMPLE))

    assert report.created == ["qwen-bot"] and not report.errors
    (agent,) = fake.agents.values()
    assert agent["agent_card_params"]["url"] == "http://runner.test/agents/qwen-bot/"
    assert agent["litellm_params"]["api_key"] == "inbound-secret"
    assert agent["object_permission"] == {"mcp_access_groups": ["qwen-bot"], "models": ["qwen3-8-27b"]}

    (key,) = fake.keys.values()
    assert key["agent_id"] == agent["agent_id"]
    assert key["object_permission"] == {"mcp_access_groups": ["qwen-bot"]}
    assert key["key_alias"].startswith(f"{KEY_ALIAS_PREFIX}qwen-bot:")
    assert registry.get("qwen-bot").key == key["key"]
    # Every admin call uses the sync key.
    assert all(c.headers["authorization"] == "Bearer sk-sync" for c in fake.calls)


@pytest.mark.asyncio
async def test_second_run_is_quiet():
    fake = FakeLiteLLM()
    reconciler, _ = make(fake)
    catalog = load_directory(EXAMPLE)
    await reconciler.reconcile(catalog)
    fake.calls.clear()
    report = await reconciler.reconcile(catalog)
    assert (report.created, report.updated, report.deleted) == ([], [], [])
    assert [(c.method, c.path) for c in fake.calls] == [("GET", "/v1/agents")]


@pytest.mark.asyncio
async def test_change_updates_agent_and_rekeys_on_access_change():
    fake = FakeLiteLLM()
    reconciler, registry = make(fake, ALLOWED_MCP_ACCESS_GROUPS="{name},crm")
    base = "apiVersion: kiseno/v1\nname: helper\ndescription: Helps.\n"
    await reconciler.reconcile(build_catalog([("helper", {"agent.yaml": base})], []))
    first_key = registry.get("helper").key

    report = await reconciler.reconcile(build_catalog([("helper", {"agent.yaml": base, "instructions.md": "New"})], []))
    assert report.updated == ["helper"]
    assert registry.get("helper").key == first_key  # instructions alone don't need a new key

    report = await reconciler.reconcile(
        build_catalog([("helper", {"agent.yaml": base + "mcp_access_groups: [crm]\n"})], [])
    )
    assert report.updated == ["helper"]
    assert registry.get("helper").key != first_key
    assert len(fake.keys) == 1 and next(iter(fake.keys.values()))["object_permission"] == {"mcp_access_groups": ["crm"]}


@pytest.mark.asyncio
async def test_restart_replaces_old_runner_keys_only():
    fake = FakeLiteLLM()
    agent_id = fake.add_agent("qwen-bot", MANAGED_URL + "qwen-bot/")
    fake.keys[f"{KEY_ALIAS_PREFIX}qwen-bot:old"] = {"key": "sk-old", "agent_id": agent_id}
    fake.keys["someone-elses-key"] = {"key": "sk-other", "agent_id": agent_id}
    reconciler, registry = make(fake)

    await reconciler.reconcile(load_directory(EXAMPLE))
    assert f"{KEY_ALIAS_PREFIX}qwen-bot:old" not in fake.keys
    assert "someone-elses-key" in fake.keys
    assert registry.get("qwen-bot").key not in ("sk-old", "sk-other")


@pytest.mark.asyncio
async def test_removed_agent_is_deleted_but_hand_made_agents_stay():
    fake = FakeLiteLLM()
    gone = fake.add_agent("old-bot", MANAGED_URL + "old-bot/")
    fake.keys[f"{KEY_ALIAS_PREFIX}old-bot:x"] = {"key": "sk-x", "agent_id": gone}
    manual = fake.add_agent("manual", "http://elsewhere.test/")
    reconciler, _ = make(fake)

    report = await reconciler.reconcile(load_directory(EXAMPLE))
    assert report.deleted == ["old-bot"]
    assert gone not in fake.agents and manual in fake.agents
    assert f"{KEY_ALIAS_PREFIX}old-bot:x" not in fake.keys


@pytest.mark.asyncio
async def test_invalid_agent_is_left_alone():
    fake = FakeLiteLLM()
    agent_id = fake.add_agent("helper", MANAGED_URL + "helper/")
    reconciler, _ = make(fake)
    broken = build_catalog([("helper", {"agent.yaml": "apiVersion: kiseno/v1\nname: helper\n"})], [])

    report = await reconciler.reconcile(broken)
    assert "helper" in report.invalid and report.deleted == []
    assert agent_id in fake.agents


@pytest.mark.asyncio
async def test_empty_catalog_never_wipes_all_agents():
    fake = FakeLiteLLM()
    agent_id = fake.add_agent("qwen-bot", MANAGED_URL + "qwen-bot/")
    reconciler, _ = make(fake)

    report = await reconciler.reconcile(build_catalog([], []))
    assert "_catalog" in report.errors and report.deleted == []
    assert agent_id in fake.agents


@pytest.mark.asyncio
async def test_agent_file_cannot_grant_other_access_groups():
    """A merged agent.yaml must not widen MCP access: by default only the group named after the agent is allowed."""
    fake = FakeLiteLLM()
    reconciler, registry = make(fake)
    yaml = "apiVersion: kiseno/v1\nname: helper\ndescription: x\nmcp_access_groups: [helper, admin-tools]\n"

    report = await reconciler.reconcile(build_catalog([("helper", {"agent.yaml": yaml})], []))
    assert "admin-tools" in report.invalid["helper"]
    assert fake.agents == {} and fake.keys == {} and registry.get("helper") is None


@pytest.mark.asyncio
async def test_admins_can_allow_shared_groups():
    fake = FakeLiteLLM()
    s = settings(ALLOWED_MCP_ACCESS_GROUPS="{name},shared-*")
    client = httpx.AsyncClient(base_url=s.normalized_litellm_base_url, transport=fake.transport())
    reconciler = Reconciler(LiteLLMAdmin(client, s.litellm_sync_key), s, Registry())
    yaml = "apiVersion: kiseno/v1\nname: helper\ndescription: x\nmcp_access_groups: [helper, shared-calendar]\n"

    report = await reconciler.reconcile(build_catalog([("helper", {"agent.yaml": yaml})], []))
    assert report.created == ["helper"] and report.invalid == {}


@pytest.mark.asyncio
async def test_agent_keys_expire_and_are_renewed_before_they_run_out():
    fake = FakeLiteLLM()
    reconciler, registry = make(fake, AGENT_KEY_DURATION_DAYS="30", AGENT_KEY_RENEW_DAYS="3")
    catalog = load_directory(EXAMPLE)
    await reconciler.reconcile(catalog)
    first = registry.get("qwen-bot")
    assert next(iter(fake.keys.values()))["duration"] == "30d"

    await reconciler.reconcile(catalog)
    assert registry.get("qwen-bot").key == first.key  # far from expiry: kept

    registry.get("qwen-bot").key_expires_at = __import__("time").time() + 2 * 86400  # inside the renew window
    await reconciler.reconcile(catalog)
    renewed = registry.get("qwen-bot")
    assert renewed.key != first.key and len(fake.keys) == 1
