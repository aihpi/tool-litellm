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

baseline_commit=$(git log -1 --format=%H -- "$SCRIPT_DIR/baseline.sha256")
out=$(mktemp)
while IFS=$'\t' read -r ours upstream_path; do
  case "$ours" in \#*|"") continue ;; esac
  if [ ! -f "$upstream_path" ]; then
    echo "ERROR: $upstream_path does not exist" >&2
    exit 1
  fi
  new_sha=$(shasum -a 256 "$upstream_path" | awk '{print $1}')
  old_sha=$(git show "$baseline_commit:aihpi/baseline.sha256" 2>/dev/null | awk -F'\t' -v n="$ours" '$3 == n {print $1}')
  if [ -n "$old_sha" ] && [ "$new_sha" != "$old_sha" ] && git diff --quiet "$baseline_commit" -- "$SCRIPT_DIR/$ours"; then
    echo "ERROR: upstream changed $upstream_path but $ours is untouched since the baseline was last recorded ($baseline_commit)." >&2
    echo "Baselining now would hide a stale copy from the build guard. Merge upstream's" >&2
    echo "change into $ours first (git merge-file against the old baseline blob), then rerun." >&2
    exit 1
  fi
  printf '%s\t%s\t%s\n' \
    "$new_sha" \
    "$(git hash-object "$upstream_path")" \
    "$ours" >> "$out"
  echo "baselined $upstream_path"
done < "$SCRIPT_DIR/manifest.txt"

mv "$out" "$SCRIPT_DIR/baseline.sha256"
echo "Wrote $SCRIPT_DIR/baseline.sha256"
