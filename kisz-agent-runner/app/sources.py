"""Where agent definitions come from: ConfigMaps in the cluster (synced by Argo CD) or a local folder."""

from __future__ import annotations

import os
import ssl
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.spec import AGENT_LABEL, SKILL_LABEL, Catalog, load_configmaps, load_directory

_SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class CatalogSource(Protocol):
    async def load(self) -> Catalog: ...


class DirectorySource:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    async def load(self) -> Catalog:
        return load_directory(self.root)


class KubernetesConfigMapSource:
    """Lists ConfigMaps by label with the pod's service account (needs get/list on configmaps in the namespace)."""

    def __init__(
        self,
        namespace: str,
        *,
        api_url: str | None = None,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool | None = None,
    ):
        self.namespace = namespace
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.api_url = (api_url or f"https://{host}:{port}").rstrip("/")
        self._token = token
        self._transport = transport
        self._verify = verify

    def _auth(self) -> dict[str, str]:
        token = self._token if self._token is not None else (_SA_DIR / "token").read_text().strip()
        return {"Authorization": f"Bearer {token}"}

    def _tls(self) -> ssl.SSLContext | bool:
        if self._verify is not None:
            return self._verify
        return ssl.create_default_context(cafile=str(_SA_DIR / "ca.crt"))

    async def _list(self, client: httpx.AsyncClient, label: str) -> list[dict[str, Any]]:
        response = await client.get(
            f"/api/v1/namespaces/{self.namespace}/configmaps", params={"labelSelector": label}, headers=self._auth()
        )
        response.raise_for_status()
        return list(response.json().get("items") or [])

    async def load(self) -> Catalog:
        async with httpx.AsyncClient(
            base_url=self.api_url, transport=self._transport, verify=self._tls(), timeout=15.0
        ) as client:
            items = await self._list(client, AGENT_LABEL) + await self._list(client, SKILL_LABEL)
        return load_configmaps(items)
