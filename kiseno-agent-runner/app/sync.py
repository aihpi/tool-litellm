"""Keeps LiteLLM's agent table and the runner's agent keys in line with the catalog.

The runner owns exactly the LiteLLM agents whose card URL starts with RUNNER_PUBLIC_URL + "/agents/". Agents an
admin created by hand point elsewhere and are never touched. Each managed agent gets one key bound to it
(`agent_id`), held only in memory; on startup the runner deletes its older keys for the agent and mints a new one.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.litellm_client import LiteLLMAdmin
from app.spec import AgentDefinition, Catalog

logger = logging.getLogger(__name__)

KEY_ALIAS_PREFIX = "kiseno-agent-runner:"


@dataclass
class AgentRuntime:
    definition: AgentDefinition
    agent_id: str
    key: str
    fingerprint: str
    key_expires_at: float = 0.0


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    invalid: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class Registry:
    """What the A2A side reads at request time. Replaced atomically per agent by the sync."""

    def __init__(self) -> None:
        self.agents: dict[str, AgentRuntime] = {}

    def get(self, name: str) -> AgentRuntime | None:
        return self.agents.get(name)


def agent_body(definition: AgentDefinition, settings: Settings, fingerprint: str) -> dict[str, Any]:
    spec = definition.spec
    model = spec.model or settings.default_model
    return {
        "agent_name": spec.name,
        "agent_card_params": {
            "name": spec.name,
            "description": spec.description,
            "url": settings.agent_url(spec.name),
            # The A2A card version carries the fingerprint, so the sync can tell when LiteLLM's copy is stale.
            "version": fingerprint[:16],
            "protocolVersion": "1.0",
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [{"id": s.name, "name": s.name, "description": s.description, "tags": []} for s in definition.skills],
        },
        "litellm_params": {"api_key": settings.runner_inbound_token, "a2a_protocol_version": "1.0"},
        "object_permission": {"mcp_access_groups": spec.access_groups, "models": [model]},
    }


def key_body(definition: AgentDefinition, settings: Settings, agent_id: str) -> dict[str, Any]:
    spec = definition.spec
    return {
        "agent_id": agent_id,
        "key_alias": f"{KEY_ALIAS_PREFIX}{spec.name}:{uuid.uuid4().hex[:8]}",
        "duration": f"{settings.agent_key_duration_days}d",
        "models": [spec.model or settings.default_model],
        "object_permission": {"mcp_access_groups": spec.access_groups},
        "metadata": {"managed_by": "kiseno-agent-runner", "agent_name": spec.name},
    }


def _key_fingerprint(definition: AgentDefinition, settings: Settings) -> str:
    spec = definition.spec
    return f"{spec.model or settings.default_model}|{','.join(spec.access_groups)}"


class Reconciler:
    def __init__(self, admin: LiteLLMAdmin, settings: Settings, registry: Registry):
        self.admin = admin
        self.settings = settings
        self.registry = registry
        self._key_fingerprints: dict[str, str] = {}

    def _is_managed(self, agent: dict[str, Any]) -> bool:
        url = (agent.get("agent_card_params") or {}).get("url") or ""
        return isinstance(url, str) and url.startswith(self.settings.agents_url_prefix)

    async def _replace_key(self, definition: AgentDefinition, agent_id: str) -> str:
        old = await self.admin.key_aliases_for_agent(agent_id, f"{KEY_ALIAS_PREFIX}{definition.name}:")
        key = await self.admin.generate_key(key_body(definition, self.settings, agent_id))
        await self.admin.delete_keys_by_alias(old)
        return key

    async def reconcile(self, catalog: Catalog) -> SyncReport:
        report = SyncReport(invalid=dict(catalog.invalid))
        existing = {a["agent_name"]: a for a in await self.admin.list_agents() if self._is_managed(a) and a.get("agent_name")}

        allowed: dict[str, AgentDefinition] = {}
        for name, definition in catalog.agents.items():
            refused = self.settings.disallowed_access_groups(name, definition.spec.access_groups)
            if refused:
                # Treated like a broken file: the agent keeps whatever it had, and nothing is widened.
                report.invalid[name] = f"mcp_access_groups not allowed for this agent: {', '.join(refused)}"
            else:
                allowed[name] = definition

        for name, definition in allowed.items():
            try:
                await self._sync_one(definition, existing.get(name), report)
            except Exception as exc:  # one broken agent must not stop the others
                logger.exception("sync failed for agent %s", name)
                report.errors[name] = str(exc)

        if not catalog.agents and not catalog.invalid and existing:
            # An empty catalog is far more likely a wrong label, an empty namespace or a half-finished Argo sync than
            # a real wish to remove every agent. Removing the last agent is a manual step (delete it in LiteLLM).
            report.errors["_catalog"] = "no agent definitions found; refusing to delete all managed agents"
            return report

        for name, agent in existing.items():
            if name in allowed or name in report.invalid:
                continue
            try:
                agent_id = agent["agent_id"]
                await self.admin.delete_keys_by_alias(
                    await self.admin.key_aliases_for_agent(agent_id, f"{KEY_ALIAS_PREFIX}{name}:")
                )
                await self.admin.delete_agent(agent_id)
                self.registry.agents.pop(name, None)
                self._key_fingerprints.pop(name, None)
                report.deleted.append(name)
            except Exception as exc:
                logger.exception("delete failed for agent %s", name)
                report.errors[name] = str(exc)
        return report

    async def _sync_one(self, definition: AgentDefinition, current: dict[str, Any] | None, report: SyncReport) -> None:
        name = definition.name
        fingerprint = definition.fingerprint(self.settings.default_model)
        body = agent_body(definition, self.settings, fingerprint)

        if current is None:
            created = await self.admin.create_agent(body)
            agent_id = created.get("agent_id")
            if not agent_id:
                raise RuntimeError("LiteLLM returned no agent_id")
            report.created.append(name)
        else:
            agent_id = current["agent_id"]
            if (current.get("agent_card_params") or {}).get("version") != fingerprint[:16]:
                await self.admin.update_agent(agent_id, body)
                report.updated.append(name)

        runtime = self.registry.get(name)
        key_fp = _key_fingerprint(definition, self.settings)
        now = time.time()
        renew_at = (runtime.key_expires_at - self.settings.agent_key_renew_days * 86400) if runtime else 0.0
        if (
            runtime is None
            or runtime.agent_id != agent_id
            or self._key_fingerprints.get(name) != key_fp
            or now >= renew_at
        ):
            key = await self._replace_key(definition, agent_id)
            key_expires_at = now + self.settings.agent_key_duration_days * 86400
            self._key_fingerprints[name] = key_fp
        else:
            key, key_expires_at = runtime.key, runtime.key_expires_at
        self.registry.agents[name] = AgentRuntime(
            definition=definition, agent_id=agent_id, key=key, fingerprint=fingerprint, key_expires_at=key_expires_at
        )
