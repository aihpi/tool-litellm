#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AIHPI_DIR="$(dirname "$SCRIPT_DIR")"
UI_DIR="ui/litellm-dashboard"
LOGIN_PAGE="$UI_DIR/src/app/login/LoginPage.tsx"

# Upstream dropped @tremor/react in #37394, so the --color-tremor-brand* vars
# are gone. shadcn's --primary is their successor: it drives every primary
# button and accent. Its dark value is a light grey with dark text, so the
# foreground has to flip to near-white along with it.
echo "Applying HPI brand colour to the shadcn theme..."
python3 - "$UI_DIR/src/app/globals.css" <<'PY'
import re
import sys

HPI_ORANGE = "#dd6108"
ON_ORANGE = "oklch(0.985 0.002 247.839)"

path = sys.argv[1]
css = open(path).read()

if HPI_ORANGE in css:
    print("  already applied, skipping")
    raise SystemExit

for var, value in (("--primary", HPI_ORANGE), ("--primary-foreground", ON_ORANGE)):
    css, n = re.subn(rf"(^\s*{re.escape(var)}\s*:\s*)[^;]+;", rf"\g<1>{value};", css, flags=re.M)
    if n != 2:
        raise SystemExit(
            f"ERROR: expected {var} twice (:root and .dark) in {path}, found {n}; "
            "upstream changed the theme tokens, update aihpi/branding/apply.sh"
        )

open(path, "w").write(css)
print("  applied")
PY

# /get_image sniffs the file header, so a PNG under the .jpg name is fine.
echo "Replacing default nav logo with HPI logo..."
cp "$UI_DIR/public/assets/aisc.png" litellm/proxy/logo.jpg

# Both logos live in the full-width top banner (LegalBanner), so drop the
# sidebar's logo to avoid showing the branding twice.
echo "Removing sidebar logo..."
python3 - "$UI_DIR/src/components/leftnav.tsx" <<'PY'
import re, sys

path = sys.argv[1]
src = open(path).read()

if 'aria-label="LiteLLM home"' not in src:
    print("  already applied, skipping")
    raise SystemExit

pattern = re.compile(
    r'\n\s*<Link href=\{migratedHref\(""\)\}[^>]*aria-label="LiteLLM home">'
    r'\s*<img\s+src=\{logoSrc\}.*?/>\s*</Link>',
    re.S,
)
src, n = pattern.subn("", src, count=1)
if n != 1:
    raise SystemExit(
        f"ERROR: sidebar logo Link not found in {path}; "
        "upstream changed it, update aihpi/branding/apply.sh"
    )

# logoSrc was only used by the <img> just removed; drop it so the unused
# binding cannot trip lint.
src = re.sub(r"\n\s*const logoSrc = logoUrl \|\| `\$\{baseUrl\}/get_image`;", "", src, count=1)

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
else
  python3 - "$LOGIN_PAGE" <<'PY'
import sys

path = sys.argv[1]
old = '<h2 className="text-3xl font-semibold text-foreground">\U0001F685 LiteLLM</h2>'
new = (
    '<h2 className="text-3xl font-semibold text-foreground">AI Model Hub</h2>\n'
    '                <p className="text-sm text-muted-foreground">'
    'by KI-Servicezentrum Berlin-Brandenburg</p>\n'
    '                <div className="mt-3 flex items-center justify-center gap-4">\n'
    '                  <img src="/ui/assets/aisc.png" alt="KI Service Zentrum"'
    ' className="h-12 w-auto object-contain" />\n'
    '                  <img src="/ui/assets/BMFTR.png" alt="BMFTR"'
    ' className="h-16 w-auto object-contain" />\n'
    '                </div>'
)
src = open(path).read()
n = src.count(old)
if n == 0:
    raise SystemExit(
        f"ERROR: login wordmark not found in {path}; "
        "upstream changed the login page, update aihpi/branding/apply.sh"
    )
open(path, "w").write(src.replace(old, new))
print(f"  applied to {n} headings")
PY
fi

echo "Rebranding SSO login to Authentik..."
if grep -q "Login with Authentik" "$LOGIN_PAGE"; then
  echo "  already applied, skipping"
elif grep -q "Login with SSO" "$LOGIN_PAGE"; then
  python3 - "$LOGIN_PAGE" <<'PY'
import sys
path = sys.argv[1]
src = open(path).read()
src = src.replace("configure SSO to log in with SSO", "configure Authentik SSO to log in with Authentik")
open(path, "w").write(src.replace("Login with SSO", "Login with Authentik"))
PY
else
  echo "ERROR: neither 'Login with SSO' nor 'Login with Authentik' found in $LOGIN_PAGE" >&2
  echo "Upstream changed the login page; update aihpi/branding/apply.sh" >&2
  exit 1
fi

# The SSO notice sits exactly where the legal links belong, so one edit does
# both: it drops upstream's AUTO_REDIRECT_UI_LOGIN_TO_SSO hint and puts Imprint
# and Privacy there instead. The "Default Credentials" box is not touched here:
# upstream gates it on LITELLM_HIDE_DEFAULT_CREDENTIALS_HINT=true, which the
# deployment sets.
echo "Swapping login SSO notice for legal links, dropping the LiteLLM subtitle..."
python3 - "$LOGIN_PAGE" <<'PY'
import sys

path = sys.argv[1]
src = open(path).read()

if "pages/imprint/" in src:
    print("  already applied, skipping")
    raise SystemExit

notice = "{uiConfig?.sso_configured && <SsoEnabledNotice />}"
links = (
    '<div className="mt-4 flex items-center justify-center gap-4 text-xs text-muted-foreground">\n'
    '              <a href="https://aisc.hpi.de/portal/cfp/pages/imprint/"'
    ' target="_blank" rel="noopener noreferrer">\n'
    "                Imprint\n"
    "              </a>\n"
    '              <a href="https://aisc.hpi.de/portal/cfp/pages/privacy/"'
    ' target="_blank" rel="noopener noreferrer">\n'
    "                Privacy\n"
    "              </a>\n"
    "            </div>"
)
if src.count(notice) != 1:
    raise SystemExit(
        f"ERROR: sso_configured notice not found in {path}; "
        "upstream changed it, update aihpi/branding/apply.sh"
    )
src = src.replace(notice, links, 1)

subtitle = '<p className="text-sm text-muted-foreground">Access your LiteLLM Admin UI.</p>\n'
if subtitle not in src:
    raise SystemExit(
        f"ERROR: login subtitle not found in {path}; "
        "upstream changed it, update aihpi/branding/apply.sh"
    )
src = src.replace(subtitle, "", 1)

open(path, "w").write(src)
print("  applied")
PY

echo "Adding HPI logos to the loading screen..."
LOADING_SCREEN="$UI_DIR/src/components/common_components/LoadingScreen.tsx"
python3 - "$LOADING_SCREEN" <<'PY'
import sys

path = sys.argv[1]
src = open(path).read()

if "aisc.png" in src:
    print("  already applied, skipping")
    raise SystemExit

old = '<div className="text-lg font-medium py-2 pr-4 border-r border-r-gray-200">\U0001F685 LiteLLM</div>'
if old not in src:
    raise SystemExit(
        f"ERROR: loading screen wordmark not found in {path}; "
        "upstream changed it, update aihpi/branding/apply.sh"
    )

new = (
    '<div className="flex items-center gap-4 py-2 pr-4 border-r border-r-gray-200">\n'
    '        <img src={getUiAssetPath("/assets/aisc.png")} alt="KI Service Zentrum"'
    ' className="h-10 w-auto object-contain" />\n'
    '        <img src={getUiAssetPath("/assets/BMFTR.png")} alt="BMFTR"'
    ' className="h-12 w-auto object-contain" />\n'
    '      </div>'
)
src = src.replace(old, new, 1)
src = 'import { getUiAssetPath } from "@/utils/uiAssetPath";\n' + src
open(path, "w").write(src)
print("  applied")
PY

# Every user hits this on their first key creation. Upstream's wording cites the
# policy ("Allowed roles=[...]") rather than saying what to do about it. Patched
# here rather than in the UI because the dashboard just renders the API's text,
# so one edit fixes both surfaces.
echo "Rewording the personal-key rejection..."
python3 - litellm/proxy/management_endpoints/key_management_endpoints.py <<'PY'
import sys

path = sys.argv[1]
src = open(path).read()

if "Please select a team" in src:
    print("  already applied, skipping")
    raise SystemExit

old = (
    'detail=f"Personal key creation has been restricted by admin. '
    "Allowed roles={personal_key_generation['allowed_user_roles']}. "
    'Your role={user_api_key_dict.user_role}",'
)
new = (
    'detail=f"Please select a team. Keys must belong to a team - '
    'personal keys are disabled for your role ({user_api_key_dict.user_role}).",'
)

if old not in src:
    raise SystemExit(
        f"ERROR: personal-key rejection message not found in {path}; "
        "upstream changed it, update aihpi/branding/apply.sh"
    )

open(path, "w").write(src.replace(old, new, 1))
print("  applied")
PY

echo "Routing the UI's SSO detection through Authentik..."
python3 - <<'PY'
import sys

path = "litellm/proxy/discovery_endpoints/ui_discovery_endpoints.py"
src = open(path).read()

# Aliasing on import leaves the call site untouched, so this file has one anchor
# to keep in sync with upstream instead of two.
old = "from litellm.proxy.auth.auth_utils import has_user_setup_sso"
new = (
    "from litellm.proxy.management_endpoints.sso.custom_authentik_sso import "
    "_has_ui_sso_setup as has_user_setup_sso"
)

if new in src:
    print("  already applied, skipping")
    sys.exit(0)

if old not in src:
    raise SystemExit(
        f"ERROR: {old!r} not found in {path}; "
        "upstream changed the UI config endpoint, update aihpi/branding/apply.sh"
    )

open(path, "w").write(src.replace(old, new, 1))
print("  applied")
PY

echo "Adding prompt/input overrides to /health/test_connection..."
python3 - <<'PY'
import sys

path = "litellm/proxy/health_endpoints/_health_endpoints.py"
src = open(path).read()

params_anchor = "    litellm_params: dict = fastapi.Body(\n"
params = """    test_prompt: str | None = fastapi.Body(
        None,
        description="Optional prompt override for the health check",
    ),
    test_input: list[str] | None = fastapi.Body(
        None,
        description="Optional input override for the health check",
    ),
"""

old_call = '                prompt="test from litellm",\n                input=["test from litellm"],\n'
new_call = '                prompt=test_prompt or "test from litellm",\n                input=test_input or ["test from litellm"],\n'

if params in src and new_call in src:
    print("  already applied, skipping")
    sys.exit(0)

for needle in (params_anchor, old_call):
    if src.count(needle) != 1:
        raise SystemExit(
            f"ERROR: expected exactly one occurrence of {needle!r} in {path}; "
            "upstream changed test_connection, update aihpi/branding/apply.sh"
        )

src = src.replace(params_anchor, params + params_anchor, 1).replace(old_call, new_call, 1)
open(path, "w").write(src)
print("  applied")
PY

echo "Applying whole-file copies..."
python3 - "$AIHPI_DIR" <<'PY'
import hashlib
import pathlib
import shutil
import sys

patch_dir = pathlib.Path(sys.argv[1])

baseline = {}
for line in (patch_dir / "baseline.sha256").read_text().splitlines():
    if line.strip():
        digest, _blob, name = line.split("\t")
        baseline[name] = digest

manifest = []
for line in (patch_dir / "manifest.txt").read_text().splitlines():
    if line.strip() and not line.startswith("#"):
        ours, upstream = line.split("\t")
        manifest.append((ours, pathlib.Path(upstream)))


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


stale = []
for ours, upstream in manifest:
    src = patch_dir / ours
    if not upstream.exists():
        stale.append((ours, upstream, "upstream file no longer exists"))
        continue
    if sha256(upstream) == sha256(src):
        continue  # already applied
    if sha256(upstream) != baseline.get(ours):
        stale.append((ours, upstream, "upstream changed since this copy was taken"))

if stale:
    print("\nERROR: whole-file copies are out of date.\n", file=sys.stderr)
    for ours, upstream, why in stale:
        print(f"  {upstream}\n    {why}", file=sys.stderr)
    print(
        "\nCopying them anyway would silently discard upstream's changes to those\n"
        "files. To resolve, for each file above:\n"
        "  1. diff aihpi/<copy> against the file in the tree\n"
        "  2. re-apply the fork additions on top of upstream's new version\n"
        "  3. from a pristine tree, run: bash aihpi/rebaseline.sh\n",
        file=sys.stderr,
    )
    raise SystemExit(1)

for ours, upstream in manifest:
    shutil.copyfile(patch_dir / ours, upstream)
print(f"  applied {len(manifest)} files, all matching their recorded baseline")
PY

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
cp "$AIHPI_DIR/__init__.py" "$AIHPI_DIR/provider.py" "$AIHPI_DIR/routes.py" litellm/aihpi/

echo "All patches applied"
