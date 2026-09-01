import pytest

from litellm.proxy._types import LitellmUserRoles
from litellm.proxy.management_endpoints.sso.custom_authentik_sso import (
    determine_authentik_role_from_claims,
    normalize_sso_groups,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (["a", "b"], ["a", "b"]),
        ("a, b ,", ["a", "b"]),
        ("solo", ["solo"]),
        (7, ["7"]),
        (None, []),
    ],
)
def test_normalize_sso_groups_handles_every_shape_an_idp_sends(raw, expected):
    assert normalize_sso_groups(raw) == expected


@pytest.mark.parametrize("groups", [["SCI-ADMINS"], "SCI-ADMINS", "other, SCI-ADMINS", ["other", "SCI-ADMINS"]])
def test_admin_group_grants_proxy_admin_whatever_the_claim_shape(groups):
    """Regression: get_nested_value(default=[]) discarded non-list claims, so a
    comma-separated or scalar groups claim silently downgraded admins."""
    assert determine_authentik_role_from_claims({"groups": groups}) == LitellmUserRoles.PROXY_ADMIN


@pytest.mark.parametrize("claims", [{"groups": ["other"]}, {"groups": "other"}, {"groups": None}, {}])
def test_everyone_else_is_an_internal_user(claims):
    assert determine_authentik_role_from_claims(claims) == LitellmUserRoles.INTERNAL_USER


def test_group_claim_and_admin_group_are_configurable(monkeypatch):
    monkeypatch.setenv("AUTHENTIK_GROUPS_ATTRIBUTE", "realm.roles")
    monkeypatch.setenv("AUTHENTIK_ADMIN_GROUP", "ops")
    assert determine_authentik_role_from_claims({"realm": {"roles": ["ops"]}}) == LitellmUserRoles.PROXY_ADMIN
    assert determine_authentik_role_from_claims({"realm": {"roles": ["dev"]}}) == LitellmUserRoles.INTERNAL_USER
