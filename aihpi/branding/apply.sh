#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AIHPI_DIR="$(dirname "$SCRIPT_DIR")"
UI_DIR="ui/litellm-dashboard"

echo "Applying HPI branding..."
cat "$SCRIPT_DIR/hpi-theme.css" >> "$UI_DIR/src/app/globals.css"
cp "$SCRIPT_DIR/LoginPage.tsx" "$UI_DIR/src/app/login/LoginPage.tsx"
cp "$SCRIPT_DIR/layout.tsx" "$UI_DIR/src/app/(dashboard)/layout.tsx"

echo "Applying Authentik SSO patches..."
cp "$AIHPI_DIR/authentik/auth_utils.py" litellm/proxy/auth/auth_utils.py
cp "$AIHPI_DIR/authentik/login_utils.py" litellm/proxy/auth/login_utils.py
cp "$AIHPI_DIR/authentik/ui_discovery_endpoints.py" litellm/proxy/discovery_endpoints/ui_discovery_endpoints.py
cp "$AIHPI_DIR/authentik/_health_endpoints.py" litellm/proxy/health_endpoints/_health_endpoints.py
cp "$AIHPI_DIR/authentik/ui_sso.py" litellm/proxy/management_endpoints/ui_sso.py
cp "$AIHPI_DIR/authentik/sso__init__.py" litellm/proxy/management_endpoints/sso/__init__.py
cp "$AIHPI_DIR/authentik/proxy_server.py" litellm/proxy/proxy_server.py

echo "All patches applied"
