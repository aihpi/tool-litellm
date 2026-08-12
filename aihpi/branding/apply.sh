#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UI_DIR="ui/litellm-dashboard"

echo "Applying HPI branding..."

cat "$SCRIPT_DIR/hpi-theme.css" >> "$UI_DIR/src/app/globals.css"

cp "$SCRIPT_DIR/LoginPage.tsx" "$UI_DIR/src/app/login/LoginPage.tsx"
cp "$SCRIPT_DIR/layout.tsx" "$UI_DIR/src/app/(dashboard)/layout.tsx"

echo "Branding applied"
