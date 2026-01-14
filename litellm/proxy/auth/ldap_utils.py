import json
import os
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from litellm.proxy.ui_crud_endpoints.proxy_setting_endpoints import UISettings
from litellm.proxy.utils import PrismaClient


class LDAPConfigError(ValueError):
    pass


async def get_ldap_settings(prisma_client: Optional[PrismaClient]) -> Dict[str, Any]:
    if prisma_client is None:
        return UISettings().model_dump()

    ui_settings: Dict[str, Any] = {}
    db_record = await prisma_client.db.litellm_uisettings.find_unique(
        where={"id": "ui_settings"}
    )
    if db_record and db_record.ui_settings:
        ui_settings_json = db_record.ui_settings
        if isinstance(ui_settings_json, str):
            ui_settings = json.loads(ui_settings_json)
        else:
            ui_settings = dict(ui_settings_json)

    return UISettings(**ui_settings).model_dump()


def _build_server(settings: Dict[str, Any]) -> Any:
    from ldap3 import Server

    ldap_host = settings.get("ldap_host")
    if not ldap_host:
        raise LDAPConfigError("LDAP host is not configured.")

    ldap_port = int(settings.get("ldap_port") or 389)
    ldap_use_tls = bool(settings.get("ldap_use_tls"))

    return Server(ldap_host, port=ldap_port, use_ssl=ldap_use_tls)


def _get_bind_credentials() -> Tuple[str, str]:
    bind_dn = os.getenv("LDAP_BIND_DN")
    bind_password = os.getenv("LDAP_BIND_PASSWORD")

    if not bind_dn or not bind_password:
        raise LDAPConfigError("LDAP bind credentials are not configured.")

    return bind_dn, bind_password


def _get_ldap_user_filter(settings: Dict[str, Any], username: str) -> str:
    from ldap3.utils.conv import escape_filter_chars

    ldap_user_filter = settings.get("ldap_user_filter") or "(&(objectClass=user)(mail={username}))"
    return ldap_user_filter.replace("{username}", escape_filter_chars(username))


def _get_user_attribute(entry: Any, attr: str) -> Optional[str]:
    if hasattr(entry, attr):
        value = getattr(entry, attr).value
        if value:
            return str(value)
    return None


def _has_admin_group(settings: Dict[str, Any], member_of: Any) -> bool:
    admin_group_dn = settings.get("ldap_admin_group_dn")
    if not admin_group_dn:
        return False

    admin_group_dn_lower = admin_group_dn.lower()
    try:
        member_values = member_of.values  # type: ignore[attr-defined]
    except Exception:
        member_values = member_of or []

    for entry in member_values:
        if isinstance(entry, str) and entry.lower() == admin_group_dn_lower:
            return True
    return False


def authenticate_ldap_credentials(
    settings: Dict[str, Any],
    username: str,
    password: str,
) -> Tuple[str, bool]:
    from ldap3 import Connection, SUBTREE
    from ldap3.core.exceptions import LDAPException

    ldap_base_dn = settings.get("ldap_base_dn")
    if not ldap_base_dn:
        raise LDAPConfigError("LDAP base DN is not configured.")

    server = _build_server(settings)
    bind_dn, bind_password = _get_bind_credentials()

    try:
        with Connection(server, user=bind_dn, password=bind_password, auto_bind=True) as conn:
            search_filter = _get_ldap_user_filter(settings, username)
            conn.search(
                search_base=ldap_base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=["mail", "memberOf"],
            )
            if not conn.entries:
                raise HTTPException(status_code=401, detail="Invalid LDAP credentials.")

            entry = conn.entries[0]
            user_dn = entry.entry_dn
            user_email = _get_user_attribute(entry, "mail") or username
            member_of = getattr(entry, "memberOf", [])

        with Connection(server, user=user_dn, password=password, auto_bind=True):
            pass

        is_admin = _has_admin_group(settings, member_of)
        return user_email, is_admin
    except LDAPException as exc:
        raise HTTPException(status_code=500, detail=f"LDAP error: {exc}") from exc
