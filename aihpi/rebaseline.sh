#!/bin/bash
# Re-record the baseline hash and git blob id of upstream's versions of the
# copied files. The blob id is what lets the merge workflow 3-way merge our
# additions onto upstream's new version without a human.
#
# Run this ONLY from a pristine tree (no patches applied), after you have
# re-synced the copies in this directory against upstream's current versions.
# Running it on a patched tree would baseline our own output and disable the
# guard entirely.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

dirty=$(git status --porcelain -- litellm/ ui/ | grep -v '^??' || true)
if [ -n "$dirty" ]; then
  echo "ERROR: litellm/ or ui/ has uncommitted changes." >&2
  echo "Baselining a patched tree would disable the guard. Run:" >&2
  echo "  git checkout -- litellm/ ui/" >&2
  exit 1
fi

: > "$SCRIPT_DIR/baseline.sha256"
while IFS=$'\t' read -r ours upstream_path; do
  case "$ours" in \#*|"") continue ;; esac
  if [ ! -f "$upstream_path" ]; then
    echo "ERROR: $upstream_path does not exist" >&2
    exit 1
  fi
  printf '%s\t%s\t%s\n' \
    "$(shasum -a 256 "$upstream_path" | awk '{print $1}')" \
    "$(git hash-object "$upstream_path")" \
    "$ours" >> "$SCRIPT_DIR/baseline.sha256"
  echo "baselined $upstream_path"
done < "$SCRIPT_DIR/manifest.txt"

echo "Wrote $SCRIPT_DIR/baseline.sha256"
