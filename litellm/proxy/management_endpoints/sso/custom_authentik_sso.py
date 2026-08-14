"""
Helpers for Authentik-backed OIDC discovery in LiteLLM's Admin UI SSO flow.
"""

import os
from typing import Any, Dict, List

from fastapi import status
from fastapi_sso.sso.base import DiscoveryDocument

from litellm._logging import verbose_proxy_logger
from litellm.llms.custom_httpx.http_handler import (
    get_async_httpx_client,
    httpxSpecialProvider,
)
from litellm.proxy._types import ProxyErrorTypes, ProxyException

_AUTHENTIK_DISCOVERY_DOCUMENT_CACHE: Dict[str, DiscoveryDocument] = {}


def _has_ui_sso_setup() -> bool:
    from litellm.proxy.auth.auth_utils import _has_user_setup_sso

    return _has_user_setup_sso() or os.getenv("AUTHENTIK_CLIENT_ID") is not None


def _has_free_sso_user_limit() -> bool:
    """
    True when the deployment uses a built-in SSO provider that should surface the
    legacy free-tier "5 users" meter. Authentik participates in UI SSO without
    inheriting that premium-reporting fallback.
    """
    return any(
        os.getenv(client_id) is not None
        for client_id in ("MICROSOFT_CLIENT_ID", "GOOGLE_CLIENT_ID", "GENERIC_CLIENT_ID")
    )


def normalize_authentik_discovery_url(authentik_issuer: str) -> str:
    issuer = authentik_issuer.strip()
    if issuer.endswith("/.well-known/openid-configuration"):
        return issuer
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


def get_authentik_scope() -> List[str]:
    raw_scope = os.getenv("AUTHENTIK_SCOPE", "openid email profile")
    return [scope for scope in raw_scope.split(" ") if scope]


async def get_authentik_discovery_document(
    authentik_issuer: str,
) -> DiscoveryDocument:
    discovery_url = normalize_authentik_discovery_url(authentik_issuer)
    cached = _AUTHENTIK_DISCOVERY_DOCUMENT_CACHE.get(discovery_url)
    if cached is not None:
        return cached

    client = get_async_httpx_client(llm_provider=httpxSpecialProvider.SSO_HANDLER)
    try:
        response = await client.get(discovery_url)
    except Exception as exc:
        raise ProxyException(
            message=f"Failed to fetch AUTHENTIK_ISSUER discovery document: {exc}",
            type=ProxyErrorTypes.auth_error,
            param="AUTHENTIK_ISSUER",
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc

    if response.status_code != 200:
        raise ProxyException(
            message=(
                "Failed to fetch AUTHENTIK_ISSUER discovery document. "
                f"status={response.status_code} body={response.text[:500]}"
            ),
            type=ProxyErrorTypes.auth_error,
            param="AUTHENTIK_ISSUER",
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    payload: Any = response.json()
    if not isinstance(payload, dict):
        raise ProxyException(
            message="AUTHENTIK_ISSUER discovery document did not return a JSON object",
            type=ProxyErrorTypes.auth_error,
            param="AUTHENTIK_ISSUER",
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    missing_fields = [
        field for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint") if not payload.get(field)
    ]
    if missing_fields:
        raise ProxyException(
            message=("AUTHENTIK_ISSUER discovery document is missing required fields: " + ", ".join(missing_fields)),
            type=ProxyErrorTypes.auth_error,
            param="AUTHENTIK_ISSUER",
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    discovery_document = DiscoveryDocument(
        authorization_endpoint=payload["authorization_endpoint"],
        token_endpoint=payload["token_endpoint"],
        userinfo_endpoint=payload["userinfo_endpoint"],
    )
    verbose_proxy_logger.debug("Loaded Authentik discovery document from %s", discovery_url)
    _AUTHENTIK_DISCOVERY_DOCUMENT_CACHE[discovery_url] = discovery_document
    return discovery_document
