#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AIHPI_DIR="$(dirname "$SCRIPT_DIR")"
UI_DIR="ui/litellm-dashboard"
LOGIN_PAGE="$UI_DIR/src/app/login/LoginPage.tsx"

echo "Applying HPI theme colors..."
if grep -q "ant-color-primary" "$UI_DIR/src/app/globals.css"; then
  echo "  already applied, skipping"
else
  cat "$SCRIPT_DIR/hpi-theme.css" >> "$UI_DIR/src/app/globals.css"
fi

echo "Applying dashboard legal footer..."
cp "$SCRIPT_DIR/layout.tsx" "$UI_DIR/src/app/(dashboard)/layout.tsx"

echo "Rebranding SSO login to Authentik..."
if grep -q "Login with Authentik" "$LOGIN_PAGE"; then
  echo "  already applied, skipping"
elif grep -q "Login with SSO" "$LOGIN_PAGE"; then
  sed -i.bak 's/Login with SSO/Login with Authentik/g; s/configure SSO to log in with SSO/configure Authentik SSO to log in with Authentik/g' "$LOGIN_PAGE"
  rm -f "$LOGIN_PAGE.bak"
else
  echo "ERROR: neither 'Login with SSO' nor 'Login with Authentik' found in $LOGIN_PAGE" >&2
  echo "Upstream changed the login page; update aihpi/branding/apply.sh" >&2
  exit 1
fi

echo "Applying Authentik SSO backend patches..."
cp "$AIHPI_DIR/authentik/auth_utils.py" litellm/proxy/auth/auth_utils.py
cp "$AIHPI_DIR/authentik/login_utils.py" litellm/proxy/auth/login_utils.py
cp "$AIHPI_DIR/authentik/ui_discovery_endpoints.py" litellm/proxy/discovery_endpoints/ui_discovery_endpoints.py
cp "$AIHPI_DIR/authentik/_health_endpoints.py" litellm/proxy/health_endpoints/_health_endpoints.py
cp "$AIHPI_DIR/authentik/ui_sso.py" litellm/proxy/management_endpoints/ui_sso.py
cp "$AIHPI_DIR/authentik/sso__init__.py" litellm/proxy/management_endpoints/sso/__init__.py
cp "$AIHPI_DIR/authentik/proxy_server.py" litellm/proxy/proxy_server.py

# The installed wheel only ships the litellm package, so the provider has to
# live inside it to be importable as litellm.aihpi at runtime.
echo "Installing aihpi provider into litellm package..."
mkdir -p litellm/aihpi
cp "$AIHPI_DIR/__init__.py" "$AIHPI_DIR/provider.py" litellm/aihpi/

echo "All patches applied"
