#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AIHPI_DIR="$(dirname "$SCRIPT_DIR")"
UI_DIR="ui/litellm-dashboard"
LOGIN_PAGE="$UI_DIR/src/app/login/LoginPage.tsx"

# antd emits its own scoped --ant-color-primary that beats any :root rule, and
# it derives hover/active/bg/border from the token. So the colour has to go
# through ConfigProvider, not CSS.
echo "Applying HPI theme colors..."
ANTD_PROVIDER="$UI_DIR/src/contexts/AntdGlobalProvider.tsx"
HPI_TOKEN='theme={{ cssVar: true, token: { colorPrimary: "#dd6108", colorInfo: "#dd6108", colorLink: "#dd6108" } }}'
if grep -q "colorPrimary" "$ANTD_PROVIDER"; then
  echo "  already applied, skipping"
elif grep -q 'theme={{ cssVar: true }}' "$ANTD_PROVIDER"; then
  python3 - "$ANTD_PROVIDER" "$HPI_TOKEN" <<'PY'
import sys
path, token = sys.argv[1], sys.argv[2]
src = open(path).read()
open(path, "w").write(src.replace("theme={{ cssVar: true }}", token, 1))
PY
else
  echo "ERROR: 'theme={{ cssVar: true }}' not found in $ANTD_PROVIDER" >&2
  echo "Upstream changed the antd provider; update aihpi/branding/apply.sh" >&2
  exit 1
fi

# Tremor components read their brand colour from @theme vars in globals.css.
# Tailwind v4 bakes utilities from those values, so they must be rewritten in
# place rather than overridden by a later rule.
echo "Applying HPI brand palette to Tremor theme..."
python3 - "$UI_DIR/src/app/globals.css" "$SCRIPT_DIR/hpi-theme.css" <<'PY'
import re, sys

css_path, palette_path = sys.argv[1], sys.argv[2]

palette = dict(
    re.findall(r"^\s*(--[\w-]+)\s*:\s*([^;]+);", open(palette_path).read(), re.M)
)
if not palette:
    sys.exit("no declarations parsed from palette file")

css = open(css_path).read()
replaced = []
for var, value in palette.items():
    pattern = re.compile(rf"(^\s*{re.escape(var)}\s*:\s*)[^;]+;", re.M)
    css, n = pattern.subn(rf"\g<1>{value};", css)
    if n:
        replaced.append(var)

missing = sorted(set(palette) - set(replaced))
open(css_path, "w").write(css)
print(f"  rewrote {len(replaced)} vars")
if missing:
    print(f"  NOTE: not present upstream, skipped: {', '.join(missing)}")
PY

echo "Applying dashboard legal banner and footer..."
cp "$SCRIPT_DIR/layout.tsx" "$UI_DIR/src/app/(dashboard)/layout.tsx"

# /get_image sniffs the file header, so a PNG under the .jpg name is fine.
echo "Replacing default nav logo with HPI logo..."
cp "$UI_DIR/public/assets/aisc.png" litellm/proxy/logo.jpg

echo "Replacing login page wordmark with HPI logo..."
if grep -q 'alt="KI Service Zentrum"' "$LOGIN_PAGE"; then
  echo "  already applied, skipping"
elif grep -q '<Title level={2}>🚅 LiteLLM</Title>' "$LOGIN_PAGE"; then
  python3 - "$LOGIN_PAGE" <<'PY'
import sys
path = sys.argv[1]
old = "<Title level={2}>\U0001F685 LiteLLM</Title>"
new = (
    '<img src="/ui/assets/aisc.png" alt="KI Service Zentrum" '
    'className="mx-auto h-14 w-auto object-contain" />'
)
src = open(path).read()
assert old in src, "login wordmark not found"
open(path, "w").write(src.replace(old, new))
PY
else
  echo "ERROR: login wordmark not found in $LOGIN_PAGE" >&2
  echo "Upstream changed the login page; update aihpi/branding/apply.sh" >&2
  exit 1
fi

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
