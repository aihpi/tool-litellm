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

echo "Showing both HPI logos in the sidebar..."
LEFTNAV="$UI_DIR/src/components/leftnav.tsx"
python3 - "$LEFTNAV" <<'PY'
import re, sys

path = sys.argv[1]
src = open(path).read()

if 'alt="BMFTR"' in src:
    print("  already applied, skipping")
    raise SystemExit

pattern = re.compile(
    r'<img\s+src=\{logoSrc\}\s+alt="LiteLLM"\s+className="[^"]*"\s*/>', re.S
)
new = (
    '<span className="flex items-center gap-2 group-data-[collapsed=true]/sidebar:gap-1">\n'
    '                <img\n'
    '                  src={logoSrc}\n'
    '                  alt="KI Service Zentrum"\n'
    '                  className="h-7 w-auto max-w-[120px] object-contain'
    ' group-data-[collapsed=true]/sidebar:w-7"\n'
    '                />\n'
    '                <img\n'
    '                  src={getUiAssetPath("/assets/BMFTR.png")}\n'
    '                  alt="BMFTR"\n'
    '                  className="h-9 w-auto max-w-[110px] object-contain'
    ' group-data-[collapsed=true]/sidebar:hidden"\n'
    '                />\n'
    '              </span>'
)
src, n = pattern.subn(new, src, count=1)
if n != 1:
    raise SystemExit(
        f"ERROR: sidebar logo <img> not found in {path}; "
        "upstream changed it, update aihpi/branding/apply.sh"
    )

if "uiAssetPath" not in src:
    src = src.replace(
        'import ', 'import { getUiAssetPath } from "@/utils/uiAssetPath";\nimport ', 1
    )
open(path, "w").write(src)
print("  applied")
PY

# Next.js auto-emits an icon <link> from src/app/favicon.ico and puts it ahead
# of the ones declared in metadata, so the browser tab keeps using it. Replace
# the file itself rather than trying to out-declare it.
echo "Replacing Next.js app favicon with HPI favicon..."
cp "$UI_DIR/public/favicon-v2.ico" "$UI_DIR/src/app/favicon.ico"

echo "Setting page title and HPI favicon..."
ROOT_LAYOUT="$UI_DIR/src/app/layout.tsx"
python3 - "$ROOT_LAYOUT" <<'PY'
import re, sys

path = sys.argv[1]
src = open(path).read()

if "AI Model Hub" in src:
    print("  already applied, skipping")
    raise SystemExit

pattern = re.compile(r"export const metadata: Metadata = \{.*?\n\};", re.S)
new = '''const iconBase = process.env.NODE_ENV === "development" ? "" : "/ui";

export const metadata: Metadata = {
  title: "AI Model Hub",
  description: "AI Model Hub Admin UI",
  icons: {
    icon: [
      { url: `${iconBase}/favicon-v2.ico` },
      { url: `${iconBase}/favicon-96x96.png`, sizes: "96x96", type: "image/png" },
    ],
    apple: `${iconBase}/favicon.png`,
  },
};'''
src, n = pattern.subn(new, src, count=1)
if n != 1:
    raise SystemExit(
        f"ERROR: metadata block not found in {path}; "
        "upstream changed it, update aihpi/branding/apply.sh"
    )
open(path, "w").write(src)
print("  applied")
PY

echo "Replacing login page wordmark with HPI title and logos..."
if grep -q 'AI Model Hub' "$LOGIN_PAGE"; then
  echo "  already applied, skipping"
elif grep -q '<Title level={2}>🚅 LiteLLM</Title>' "$LOGIN_PAGE"; then
  python3 - "$LOGIN_PAGE" <<'PY'
import sys
path = sys.argv[1]
old = "<Title level={2}>\U0001F685 LiteLLM</Title>"
new = (
    '<Title level={2} className="mb-0">AI Model Hub</Title>\n'
    '              <Text className="block text-sm mt-0">'
    'by KI-Servicezentrum Berlin-Brandenburg</Text>\n'
    '              <div className="mt-3 flex items-center justify-center gap-4">\n'
    '                <img src="/ui/assets/aisc.png" alt="KI Service Zentrum"'
    ' className="h-12 w-auto object-contain" />\n'
    '                <img src="/ui/assets/BMFTR.png" alt="BMFTR"'
    ' className="h-16 w-auto object-contain" />\n'
    '              </div>'
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

echo "Registering AIHPI provider in the UI's provider list..."
python3 - "$SCRIPT_DIR/provider_create_field.json" <<'PY'
import json, sys

entry_path = sys.argv[1]
target = "litellm/proxy/public_endpoints/provider_create_fields.json"

entry = json.load(open(entry_path))
providers = json.load(open(target))

if any(p.get("litellm_provider") == entry["litellm_provider"] for p in providers):
    print("  already present, skipping")
else:
    providers.append(entry)
    with open(target, "w") as f:
        json.dump(providers, f, indent=2)
        f.write("\n")
    print(f"  added {entry['provider_display_name']}")
PY

# The installed wheel only ships the litellm package, so the provider has to
# live inside it to be importable as litellm.aihpi at runtime.
echo "Installing aihpi provider into litellm package..."
mkdir -p litellm/aihpi
cp "$AIHPI_DIR/__init__.py" "$AIHPI_DIR/provider.py" litellm/aihpi/

echo "All patches applied"
