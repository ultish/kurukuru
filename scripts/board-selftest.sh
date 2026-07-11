#!/usr/bin/env bash
# Phase 0 board runner selftest — plan, events, mutex default, runs/ isolation.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KURU=(python3 "$ROOT/scripts/kuru.py")
BOARD=(python3 -m board)
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  ok: $*"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $*"; }

tmp="$(mktemp -d "${TMPDIR:-/tmp}/board-selftest.XXXXXX")"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

newrepo() {
  local d
  d="$(mktemp -d "$tmp/repo.XXXXXX")"
  git -C "$d" init -q
  git -C "$d" config user.email "board-test@example.com"
  git -C "$d" config user.name "board-test"
  git -C "$d" commit --allow-empty -qm "init"
  echo "$d"
}

seed_workspace() {
  local repo="$1"
  cd "$repo"
  "${KURU[@]}" init >/dev/null
  python3 - <<'PY'
import json
from pathlib import Path
p = Path(".kuru/config.json")
cfg = json.loads(p.read_text())
cfg["gates"] = {
    "build": {"cmd": "true", "required": True, "timeout": 60},
    "test": {"cmd": "true", "required": True, "timeout": 60},
}
p.write_text(json.dumps(cfg, indent=2) + "\n")
PY
  cat > .kuru/charter.md <<'MD'
# Charter
## Problem
Test workspace for board plan.
## Users
Developers.
## Non-goals
Production.
## Success
Board plan runs.
## Required tooling / conventions
none
MD
  mkdir -p .kuru/spec
  cat > .kuru/spec/spec-1.md <<'MD'
# Spec 1
Minimal spec so board preconditions pass.
MD
}

echo "== board package imports =="
python3 -c "import board; from board.models import mutex_key; assert mutex_key(None)=='default'" \
  && ok "board imports + mutex_key(None)==default" || fail "board import/mutex_key"

echo "== board plan: single-app (null target → default lane) =="
repo="$(newrepo)"
seed_workspace "$repo"
cd "$repo"
"${KURU[@]}" new-slice "A" >/dev/null
"${KURU[@]}" new-slice "B" --depends-on SL-0001 >/dev/null
"${KURU[@]}" new-slice "C" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${KURU[@]}" set-status SL-0002 ready >/dev/null
"${KURU[@]}" set-status SL-0003 ready >/dev/null

out="$("${BOARD[@]}" plan --repo "$repo" --plugin-dir "$ROOT" --emit-events 2>/tmp/board-plan-err.$$ || true)"
# plan may print warnings to stderr; capture exit separately
if "${BOARD[@]}" plan --repo "$repo" --plugin-dir "$ROOT" --emit-events >/tmp/board-plan-out.$$ 2>/tmp/board-plan-err.$$; then
  out="$(cat /tmp/board-plan-out.$$)"
else
  out="$(cat /tmp/board-plan-out.$$ 2>/dev/null || true)"
  fail "plan exited non-zero: $(cat /tmp/board-plan-err.$$ 2>/dev/null | head -5)"
fi

echo "$out" | grep -q 'target:default' \
  && ok "plan shows target:default for single-app" || fail "no default lane: $out"
echo "$out" | grep -q 'SL-0001' && echo "$out" | grep -q 'SL-0003' \
  && ok "plan lists independent ready slices" || fail "missing actionable: $out"
echo "$out" | grep -q 'waiting (deps' \
  && ok "plan shows dep waiting" || fail "no waiting line: $out"
echo "$out" | grep -qE 'SERIAL|serialized|share target' \
  && ok "plan notes same-target serialization" || fail "no serial note: $out"

ev="$(ls -d .kuru/runs/r_* 2>/dev/null | head -1 || true)"
[ -n "${ev:-}" ] && [ -f "$ev/events.ndjson" ] \
  && ok "emit-events wrote .kuru/runs/<id>/events.ndjson" || fail "no events file"
if [ -n "${ev:-}" ] && [ -f "$ev/events.ndjson" ]; then
  grep -q '"type": "run.planned"' "$ev/events.ndjson" \
    && ok "events contain run.planned" || fail "missing run.planned"
  python3 -c "
import json
from pathlib import Path
lines = Path('$ev/events.ndjson').read_text().strip().splitlines()
ev = json.loads(lines[-1])
assert ev['type']=='run.planned'
assert 'default' in ev.get('mutex_lanes', {})
assert 'SL-0001' in ev['mutex_lanes']['default']
" && ok "run.planned mutex_lanes.default includes slices" || fail "mutex_lanes payload wrong"
fi

echo x > tracked.txt
git add tracked.txt
git add -A
if git status --porcelain | grep -q 'runs/'; then
  fail "git status still sees runs/ (gitignore broken)"
else
  ok "git add -A does not stage .kuru/runs/"
fi

echo "== board plan: multi-target lanes =="
repo2="$(newrepo)"
seed_workspace "$repo2"
cd "$repo2"
python3 - <<'PY'
import json
from pathlib import Path
cfg = {
  "project": "t",
  "targets": {
    "api": {"dir": ".", "gates": {"build": {"cmd": "true", "required": True, "timeout": 60}}},
    "web": {"dir": ".", "gates": {"build": {"cmd": "true", "required": True, "timeout": 60}}},
  },
}
Path(".kuru/config.json").write_text(json.dumps(cfg, indent=2) + "\n")
PY
"${KURU[@]}" new-slice "API work" --target api >/dev/null
"${KURU[@]}" new-slice "Web work" --target web >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${KURU[@]}" set-status SL-0002 ready >/dev/null
out="$("${BOARD[@]}" plan --repo "$repo2" --plugin-dir "$ROOT" --json 2>/dev/null)"
echo "$out" | python3 -c "
import json,sys
d=json.load(sys.stdin)
lanes=set(d.get('mutex_lanes',{}))
assert lanes=={'api','web'}, lanes
assert len(d['actionable'])==2
" && ok "multi-target plan has api+web mutex lanes" || fail "multi-target lanes wrong: $out"
text="$("${BOARD[@]}" plan --repo "$repo2" --plugin-dir "$ROOT" 2>/dev/null)"
echo "$text" | grep -q 'parallelism: 2' \
  && ok "plan reports 2 mutex targets can run at once" || fail "no parallelism note: $text"

echo "== board plan without workspace fails cleanly =="
empty="$(mktemp -d "$tmp/empty.XXXXXX")"
if "${BOARD[@]}" plan --repo "$empty" --plugin-dir "$ROOT" >/dev/null 2>&1; then
  fail "plan should refuse empty repo"
else
  ok "plan refuses repo without .kuru"
fi

echo
echo "board selftest: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
