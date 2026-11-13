"""Agent definitions from ConfigMaps in the agents namespace.

Argo CD renders each agent of the GitLab agents repo into a ConfigMap labelled
``kiseno/agent`` whose ``agent.yaml`` key holds the definition. The bot only
reads ``name``, ``teams`` and ``slack`` and ignores every other field.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml

logger = logging.getLogger(__name__)

API_VERSION = "kiseno/v1"
AGENT_LABEL = "kiseno/agent"
AGENT_KEY = "agent.yaml"

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

ListenMode = Literal["mentions", "all"]


class AgentConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SlackSettings:
    default: bool = False
    channels: tuple[str, ...] = ()
    listen: ListenMode = "mentions"
    dm: bool = False


@dataclass(frozen=True)
class AgentSpec:
    name: str
    teams: tuple[str, ...] = ()
    slack: SlackSettings = field(default_factory=SlackSettings)


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise AgentConfigError(f"{field_name} must be a list of non-empty strings")
    return tuple(value)


def _parse_slack(value: Any) -> SlackSettings:
    if value is None:
        return SlackSettings()
    if not isinstance(value, Mapping):
        raise AgentConfigError("slack must be a mapping")
    listen = value.get("listen", "mentions")
    if listen not in ("mentions", "all"):
        raise AgentConfigError("slack.listen must be 'mentions' or 'all'")
    default = value.get("default", False)
    dm = value.get("dm", False)
    if not isinstance(default, bool) or not isinstance(dm, bool):
        raise AgentConfigError("slack.default and slack.dm must be booleans")
    return SlackSettings(
        default=default,
        channels=_string_list(value.get("channels"), "slack.channels"),
        listen=listen,
        dm=dm,
    )


def parse_agent_yaml(text: str) -> AgentSpec:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AgentConfigError(f"invalid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise AgentConfigError("agent.yaml must be a mapping")
    if document.get("apiVersion") != API_VERSION:
        raise AgentConfigError(f"apiVersion must be {API_VERSION}")
    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        raise AgentConfigError("name is required")
    return AgentSpec(
        name=name.strip(),
        teams=_string_list(document.get("teams"), "teams"),
        slack=_parse_slack(document.get("slack")),
    )


def agents_from_configmaps(items: Iterable[Mapping[str, Any]]) -> list[AgentSpec]:
    """Parse ConfigMap objects, skipping invalid ones and duplicate names."""
    agents: dict[str, AgentSpec] = {}
    for item in items:
        metadata = item.get("metadata") or {}
        cm_name = metadata.get("name", "<unnamed>")
        text = (item.get("data") or {}).get(AGENT_KEY)
        if not isinstance(text, str):
            logger.warning("ConfigMap %s has no %s key, skipping", cm_name, AGENT_KEY)
            continue
        try:
            agent = parse_agent_yaml(text)
        except AgentConfigError as exc:
            logger.warning("ConfigMap %s is not a valid agent: %s", cm_name, exc)
            continue
        if agent.name in agents:
            logger.warning("ConfigMap %s repeats agent name %s, skipping", cm_name, agent.name)
            continue
        agents[agent.name] = agent
    return list(agents.values())


class AgentStore:
    """The current set of agents, replaced wholesale on every successful poll."""

    def __init__(self, agents: Iterable[AgentSpec] = ()):
        self._agents: dict[str, AgentSpec] = {}
        self.replace(agents)

    def replace(self, agents: Iterable[AgentSpec]) -> None:
        self._agents = {agent.name: agent for agent in sorted(agents, key=lambda a: a.name)}
        defaults = [a.name for a in self._agents.values() if a.slack.default]
        if len(defaults) > 1:
            logger.warning("Several agents set slack.default: %s; using %s", defaults, defaults[0])
        claimed: dict[str, str] = {}
        for agent in self._agents.values():
            for channel in agent.slack.channels:
                if channel in claimed:
                    logger.warning(
                        "Channel %s is listed by %s and %s; using %s",
                        channel,
                        claimed[channel],
                        agent.name,
                        claimed[channel],
                    )
                else:
                    claimed[channel] = agent.name

    def all(self) -> list[AgentSpec]:
        return list(self._agents.values())

    def get(self, name: str) -> AgentSpec | None:
        return self._agents.get(name)

    def default_agent(self) -> AgentSpec | None:
        return next((a for a in self._agents.values() if a.slack.default), None)

    def agent_for_channel(self, channel_id: str) -> AgentSpec | None:
        return next((a for a in self._agents.values() if channel_id in a.slack.channels), None)


class KubernetesConfigMapSource:
    """Lists agent ConfigMaps with the pod's service account (get/list on configmaps)."""

    def __init__(
        self,
        namespace: str,
        *,
        api_url: str | None = None,
        token_path: Path = SERVICE_ACCOUNT_DIR / "token",
        ca_path: Path = SERVICE_ACCOUNT_DIR / "ca.crt",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ):
        self.namespace = namespace
        self.api_url = (api_url or self._in_cluster_api_url()).rstrip("/")
        self.token_path = token_path
        self.ca_path = ca_path
        self.transport = transport
        self.timeout = timeout

    @staticmethod
    def _in_cluster_api_url() -> str:
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS") or os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if not host:
            return "https://kubernetes.default.svc"
        if ":" in host:
            host = f"[{host}]"
        return f"https://{host}:{port}"

    async def list_agents(self) -> list[AgentSpec]:
        # The projected token rotates, so read it on every call.
        token = self.token_path.read_text().strip()
        verify: Any = str(self.ca_path) if self.transport is None and self.ca_path.exists() else True
        async with httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout,
            transport=self.transport,
            verify=verify,
        ) as client:
            response = await client.get(
                f"/api/v1/namespaces/{self.namespace}/configmaps",
                params={"labelSelector": AGENT_LABEL},
            )
        response.raise_for_status()
        return agents_from_configmaps(response.json().get("items") or [])


async def refresh_agents(source: KubernetesConfigMapSource, store: AgentStore) -> bool:
    """Replace the store's agents from the source; keep the old ones if the list fails."""
    try:
        agents = await source.list_agents()
    except Exception as exc:  # noqa: BLE001 - any failure keeps the last known agents
        logger.warning("Could not list agent ConfigMaps in %s: %s", source.namespace, exc)
        return False
    before = {a.name for a in store.all()}
    store.replace(agents)
    after = {a.name for a in store.all()}
    if before != after:
        logger.info("Agents now: %s", sorted(after) or "none")
    return True


async def poll_agents_forever(source: KubernetesConfigMapSource, store: AgentStore, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        await refresh_agents(source, store)
