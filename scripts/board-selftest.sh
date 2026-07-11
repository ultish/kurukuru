#!/usr/bin/env bash
# Board runner selftest — Phase 0 plan + Phase 1 mock scheduler.
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
Test workspace for board.
## Users
Developers.
## Non-goals
Production.
## Success
Board runs.
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

if "${BOARD[@]}" plan --repo "$repo" --plugin-dir "$ROOT" --emit-events >/tmp/board-plan-out.$$ 2>/tmp/board-plan-err.$$; then
  out="$(cat /tmp/board-plan-out.$$)"
else
  out="$(cat /tmp/board-plan-out.$$ 2>/dev/null || true)"
  fail "plan exited non-zero: $(head -5 /tmp/board-plan-err.$$ 2>/dev/null)"
fi

echo "$out" | grep -q 'target:default' \
  && ok "plan shows target:default for single-app" || fail "no default lane"
echo "$out" | grep -q 'SL-0001' && echo "$out" | grep -q 'SL-0003' \
  && ok "plan lists independent ready slices" || fail "missing actionable"
echo "$out" | grep -q 'waiting (deps' \
  && ok "plan shows dep waiting" || fail "no waiting line"
echo "$out" | grep -qE 'SERIAL|serialized|share target' \
  && ok "plan notes same-target serialization" || fail "no serial note"

ev="$(ls -d .kuru/runs/r_* 2>/dev/null | head -1 || true)"
[ -n "${ev:-}" ] && [ -f "$ev/events.ndjson" ] \
  && ok "emit-events wrote events.ndjson" || fail "no events file"
if [ -n "${ev:-}" ] && [ -f "$ev/events.ndjson" ]; then
  grep -q '"type": "run.planned"' "$ev/events.ndjson" \
    && ok "events contain run.planned" || fail "missing run.planned"
fi

echo x > tracked.txt
git add tracked.txt
git add -A
if git status --porcelain | grep -q 'runs/'; then
  fail "git status still sees runs/"
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
assert set(d.get('mutex_lanes',{}))=={'api','web'}
assert len(d['actionable'])==2
" && ok "multi-target plan has api+web mutex lanes" || fail "multi-target lanes wrong"

echo "== Phase 1: mock run happy path =="
repo3="$(newrepo)"
seed_workspace "$repo3"
cd "$repo3"
"${KURU[@]}" new-slice "Solo" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${BOARD[@]}" run --repo "$repo3" --plugin-dir "$ROOT" -y --backend mock --no-commit >/tmp/br-happy.$$ 2>&1 \
  || { fail "happy run failed"; cat /tmp/br-happy.$$ | tail -20; }
st="$("${KURU[@]}" show SL-0001 --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
[ "$st" = "done" ] && ok "mock run ships SL-0001 to done" || fail "status=$st not done"
sum="$(ls .kuru/runs/*/summary.json | head -1)"
python3 -c "import json; s=json.load(open('$sum')); assert s['shipped']==['SL-0001'], s" \
  && ok "summary.json lists shipped" || fail "summary wrong"

echo "== Phase 1: verifying no_verdict re-verifies (no rebuild) =="
repo4="$(newrepo)"
seed_workspace "$repo4"
cd "$repo4"
echo '{"default":{"verify":"no_verdict"}}' > sc.json
"${KURU[@]}" new-slice "NV" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${BOARD[@]}" run --repo "$repo4" --plugin-dir "$ROOT" -y --mock-scenario sc.json --no-commit >/tmp/br-nv.$$ 2>&1 \
  || true  # exit 1 expected
sum="$(ls .kuru/runs/*/summary.json | head -1)"
python3 -c "
import json
s=json.load(open('$sum'))
r=s['results']['SL-0001']
assert r['outcome']=='stuck', r
assert r['build_count']==1, r
assert r['verify_count']>=2, r
assert 're-verify' in r['reason'] or 'no verify verdict' in r['reason'], r
" && ok "no_verdict: stuck after re-verify, build_count=1" || fail "no_verdict policy wrong"

echo "== Phase 1: same-target serial (A ships before B builds) =="
repo5="$(newrepo)"
seed_workspace "$repo5"
cd "$repo5"
"${KURU[@]}" new-slice "A" >/dev/null
"${KURU[@]}" new-slice "B" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${KURU[@]}" set-status SL-0002 ready >/dev/null
"${BOARD[@]}" run --repo "$repo5" --plugin-dir "$ROOT" -y --no-commit >/tmp/br-ser.$$ 2>&1 \
  || { fail "serial run failed"; tail -20 /tmp/br-ser.$$; }
# Both done; events: first build of B only after A shipped
python3 - <<'PY'
import json
from pathlib import Path
ev = sorted(Path('.kuru/runs').glob('*/events.ndjson'))[-1]
events = [json.loads(l) for l in ev.read_text().splitlines() if l.strip()]
# first stage.started build for each
def first_build(sid):
    for e in events:
        if e.get('type')=='stage.started' and e.get('stage')=='build' and e.get('id')==sid:
            return e['ts']
    return None
def ship_ts(sid):
    for e in events:
        if e.get('type')=='slice.finished' and e.get('id')==sid and e.get('outcome')=='shipped':
            return e['ts']
    return None
a_ship, b_build = ship_ts('SL-0001'), first_build('SL-0002')
# also allow B first if scheduler picked B first — then A build after B ship
b_ship, a_build = ship_ts('SL-0002'), first_build('SL-0001')
ok = False
if a_ship and b_build and a_ship <= b_build:
    ok = True
if b_ship and a_build and b_ship <= a_build:
    ok = True
# stronger: only one build stage.started before either ships
builds_before_any_ship = []
shipped=set()
for e in events:
    if e.get('type')=='slice.finished' and e.get('outcome')=='shipped':
        shipped.add(e['id'])
    if e.get('type')=='stage.started' and e.get('stage')=='build':
        if not shipped:
            builds_before_any_ship.append(e['id'])
# at most one unique slice building before first ship
assert len(set(builds_before_any_ship)) <= 1, builds_before_any_ship
print('serial ok', builds_before_any_ship)
PY
[ $? -eq 0 ] && ok "same-target serializes pipelines" || fail "same-target not serial"

echo "== Phase 1: different-target parallel =="
repo6="$(newrepo)"
seed_workspace "$repo6"
cd "$repo6"
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
"${KURU[@]}" new-slice "API" --target api >/dev/null
"${KURU[@]}" new-slice "WEB" --target web >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${KURU[@]}" set-status SL-0002 ready >/dev/null
# slow first build slightly via scenario not available — check both start before either ships
"${BOARD[@]}" run --repo "$repo6" --plugin-dir "$ROOT" -y --no-commit >/tmp/br-par.$$ 2>&1 \
  || { fail "parallel run failed"; tail -20 /tmp/br-par.$$; }
python3 - <<'PY'
import json
from pathlib import Path
ev = sorted(Path('.kuru/runs').glob('*/events.ndjson'))[-1]
events = [json.loads(l) for l in ev.read_text().splitlines() if l.strip()]
starts = [e for e in events if e.get('type')=='slice.started']
assert {e['id'] for e in starts}=={'SL-0001','SL-0002'}
# both started before either finished shipping (or interleaved starts)
first_ship = next(e for e in events if e.get('type')=='slice.finished' and e.get('outcome')=='shipped')
started_before_ship = [e['id'] for e in events if e.get('type')=='slice.started' and e['ts']<=first_ship['ts']]
assert len(set(started_before_ship))==2, started_before_ship
print('parallel ok')
PY
[ $? -eq 0 ] && ok "different targets start in parallel" || fail "not parallel"

echo "== Phase 1: max-tries cap on verify reject =="
repo7="$(newrepo)"
seed_workspace "$repo7"
cd "$repo7"
echo '{"default":{"verify":"rejected"}}' > sc.json
"${KURU[@]}" new-slice "Fail" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${BOARD[@]}" run --repo "$repo7" --plugin-dir "$ROOT" -y --mock-scenario sc.json --max-tries 2 --no-commit >/tmp/br-cap.$$ 2>&1 || true
sum="$(ls .kuru/runs/*/summary.json | head -1)"
python3 -c "
import json
s=json.load(open('$sum'))
r=s['results']['SL-0001']
assert r['outcome']=='capped', r
assert r['tries']==2, r
assert r['build_count']==2, r
" && ok "always-reject verify caps after max-tries builds" || fail "cap wrong"

echo "== Phase 1: deferred commit excludes runs/ =="
repo8="$(newrepo)"
seed_workspace "$repo8"
cd "$repo8"
"${KURU[@]}" new-slice "Cmt" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
echo extra > work.txt
"${BOARD[@]}" run --repo "$repo8" --plugin-dir "$ROOT" -y >/tmp/br-cmt.$$ 2>&1 \
  || { fail "commit run failed"; tail -20 /tmp/br-cmt.$$; }
git ls-tree -r HEAD --name-only | grep -q '^\.kuru/runs/' \
  && fail "commit includes .kuru/runs/" \
  || ok "deferred commit excludes .kuru/runs/"
git log -1 --oneline | grep -q 'board run' \
  && ok "deferred commit message mentions board run" || fail "commit message wrong"

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
