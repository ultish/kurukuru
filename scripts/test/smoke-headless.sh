#!/usr/bin/env bash
# smoke-headless.sh — prove that an external process can drive the kuru plugin
# through `claude -p`: load the plugin with --plugin-dir into a fresh headless
# session, invoke a /kuru:* slash command, and confirm it resolved and actually
# ran kuru.py inside that session. This is the bridge the external runner relies on.
set -uo pipefail

# This file lives in scripts/test/; repo root is two levels up.
HARNESS="$(cd "$(dirname "$0")/../.." && pwd)"

# locate the claude CLI (not always on a non-interactive PATH)
CLAUDE="$(command -v claude || true)"
for p in "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" "/opt/homebrew/bin/claude" "/usr/local/bin/claude"; do
  [ -z "$CLAUDE" ] && [ -x "$p" ] && CLAUDE="$p"
done
[ -z "$CLAUDE" ] && { echo "SKIP: claude CLI not found"; exit 2; }
echo "claude: $CLAUDE"

# optional timeout wrapper (GNU coreutils 'timeout' or macOS 'gtimeout')
TO=""
command -v timeout  >/dev/null 2>&1 && TO="timeout 240"
[ -z "$TO" ] && command -v gtimeout >/dev/null 2>&1 && TO="gtimeout 240"

# throwaway repo with an initialized workspace + one slice
REPO="$(mktemp -d)"; cd "$REPO" || exit 1; git init -q 2>/dev/null || true
CLAUDE_PLUGIN_ROOT="$HARNESS" python3 "$HARNESS/scripts/kuru.py" init >/dev/null
printf '{"project":"smoke","gates":{"unit":{"cmd":"true","required":true,"timeout":60}}}\n' > .kuru/config.json
CLAUDE_PLUGIN_ROOT="$HARNESS" python3 "$HARNESS/scripts/kuru.py" new-slice "smoke slice" >/dev/null
echo "repo:   $REPO"

echo "Running headless: claude -p '/kuru:status' --plugin-dir '$HARNESS' ..."
OUT="$($TO "$CLAUDE" -p "/kuru:status" \
        --plugin-dir "$HARNESS" \
        --permission-mode bypassPermissions \
        --output-format text 2>&1)"
rc=$?
echo "----- claude output (exit $rc) -----"
printf '%s\n' "$OUT"
echo "------------------------------------"

# The command resolved iff the model actually saw kuru.py output: the slice id or
# title must appear. (Generic words like "draft"/"dashboard" are NOT accepted —
# the model could say those without the command having resolved.)
if printf '%s' "$OUT" | grep -qiE "SL-0001|smoke slice"; then
  echo "SMOKE PASS: /kuru:status resolved via --plugin-dir and ran kuru.py in the headless session."
  exit 0
fi
echo "SMOKE FAIL: no sign the plugin command resolved. Check that --plugin-dir loaded kuru and the command name is right."
exit 1
