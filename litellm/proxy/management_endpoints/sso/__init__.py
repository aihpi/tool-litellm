"""
SSO (Single Sign-On) related modules for LiteLLM Proxy.

This package contains custom SSO implementations and utilities.
"""

from litellm.proxy.management_endpoints.sso.custom_microsoft_sso import (
    CustomMicrosoftSSO,
)
from litellm.proxy.management_endpoints.sso.custom_authentik_sso import (
    get_authentik_discovery_document,
    get_authentik_scope,
    normalize_authentik_discovery_url,
)

__all__ = [
    "CustomMicrosoftSSO",
    "get_authentik_discovery_document",
    "get_authentik_scope",
    "normalize_authentik_discovery_url",
]
