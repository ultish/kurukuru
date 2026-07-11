#!/usr/bin/env bash
# Board runner selftest — Phase 0 plan + Phase 1 mock + Phase 2 Claude +
# Phase 3 board TUI + Phase 3b Grok (unit only; no live API).
set -euo pipefail

# This file lives in scripts/test/; repo root is two levels up.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
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
[ -f .kuru/BOARD_HANDOFF.md ] && grep -q "Latest board run" .kuru/BOARD_HANDOFF.md \
  && grep -q "summary.json" .kuru/BOARD_HANDOFF.md \
  && ok "board run writes BOARD_HANDOFF.md" || fail "BOARD_HANDOFF.md missing/incomplete after run"

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

echo "== Phase 2: Claude backend construct + find_claude + missing bin =="
python3 - <<'PY'
from pathlib import Path
from board.backends.claude import ClaudeBackend, ClaudeNotFoundError, find_claude
from board.prompts import stage_prompt, stage_role

assert find_claude("/nonexistent/claude-binary-xyz") is None
assert stage_prompt("build", "sl-0001") == "/kuru:build SL-0001"
assert stage_prompt("verify", "SL-0002") == "/kuru:verify SL-0002"
assert stage_prompt("review", "SL-0003") == "/kuru:review SL-0003"
assert stage_prompt("ship", "SL-0004") == "/kuru:ship SL-0004 --no-commit"
assert stage_prompt("check", "SL-0005") == "/kuru:check-contract SL-0005"
assert stage_role("build") == "builder"
assert stage_role("verify") == "verifier"

# Construct without a real binary — build_cmd must raise clearly.
be = ClaudeBackend(plugin_dir=Path("."), claude_bin=None)
try:
    be.build_cmd("/kuru:build SL-0001")
    raise SystemExit("expected ClaudeNotFoundError")
except ClaudeNotFoundError:
    pass

# run_stage with missing bin → exit 127, log written, no spawn
import tempfile
td = Path(tempfile.mkdtemp())
log = td / "build.log"
res = be.run_stage(
    stage="build",
    slice_id="SL-0001",
    prompt="/kuru:build SL-0001",
    cwd=td,
    log_path=log,
)
assert res.exit_code == 127, res
assert "not found" in res.note.lower() or "claude" in res.note.lower(), res.note
assert log.is_file() and log.stat().st_size > 0
assert res.role == "builder"
print("claude unit ok")
PY
[ $? -eq 0 ] && ok "ClaudeBackend unit: construct, prompts, missing bin" \
  || fail "ClaudeBackend unit checks"

# CLI: --backend claude with bogus --claude-bin refuses cleanly (no live API)
repo_cl="$(newrepo)"
seed_workspace "$repo_cl"
cd "$repo_cl"
"${KURU[@]}" new-slice "Claude miss" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
if "${BOARD[@]}" run --repo "$repo_cl" --plugin-dir "$ROOT" -y --backend claude \
    --claude-bin /nonexistent/claude-xyz --no-commit >/tmp/br-claude-miss.$$ 2>&1; then
  fail "claude missing bin should exit non-zero"
else
  grep -qi 'claude CLI not found\|not found' /tmp/br-claude-miss.$$ \
    && ok "CLI --backend claude missing bin: clear error" \
    || { fail "CLI claude missing: unclear error"; cat /tmp/br-claude-miss.$$ | tail -10; }
fi

# Dry-run does not require claude binary
if "${BOARD[@]}" run --repo "$repo_cl" --plugin-dir "$ROOT" -y --backend claude \
    --claude-bin /nonexistent/claude-xyz --dry-run >/tmp/br-claude-dry.$$ 2>&1; then
  ok "CLI --backend claude --dry-run works without binary"
else
  fail "dry-run with claude backend should succeed without binary"
  cat /tmp/br-claude-dry.$$ | tail -10
fi

echo "== Phase 3: make_run_ui (plain / json only; hierarchical board is Ratatui) =="
python3 - <<'PY'
from board.ui.plain import PlainUI, make_run_ui

assert isinstance(make_run_ui("plain"), PlainUI)
assert make_run_ui("json") is None
# unknown names still get a PlainUI stream (CLI validates choices)
assert isinstance(make_run_ui("whatever"), PlainUI)
print("make_run_ui ok")
PY
[ $? -eq 0 ] && ok "make_run_ui factory" || fail "make_run_ui factory"

echo "== Phase 3: mock run --ui plain still green (regression) =="
repo_r="$(newrepo)"
seed_workspace "$repo_r"
cd "$repo_r"
"${KURU[@]}" new-slice "Regress" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${BOARD[@]}" run --repo "$repo_r" --plugin-dir "$ROOT" -y --backend mock --ui plain --no-commit \
  >/tmp/br-plain-reg.$$ 2>&1 \
  && ok "plain UI mock run still works" \
  || { fail "plain UI regression"; tail -20 /tmp/br-plain-reg.$$; }

echo "== Phase 3: --ui board rejected (removed; use board-tui.sh) =="
if "${BOARD[@]}" run --help 2>&1 | grep -qE -- '--ui.*plain|choices.*plain'; then
  ok "CLI help documents plain/json ui"
else
  fail "CLI help missing --ui plain/json"
fi
# argparse should reject the old choice
if "${BOARD[@]}" run --repo "$repo_r" --plugin-dir "$ROOT" -y --backend mock --ui board --no-commit \
    >/tmp/br-board-gone.$$ 2>&1; then
  fail "--ui board should no longer be accepted"
  tail -10 /tmp/br-board-gone.$$
else
  grep -qiE 'invalid choice|board' /tmp/br-board-gone.$$ \
    && ok "--ui board rejected by argparse" \
    || { fail "unclear error for --ui board"; tail -10 /tmp/br-board-gone.$$; }
fi

echo "== Phase 3b: Grok backend construct + find_grok + stage_prompt_grok =="
python3 - <<PY
from pathlib import Path
from board.backends.grok import GrokBackend, GrokNotFoundError, find_grok
from board.prompts import (
    stage_prompt,
    stage_prompt_claude,
    stage_prompt_for,
    stage_prompt_grok,
    skill_path_for,
)

assert find_grok("/nonexistent/grok-binary-xyz") is None
# Claude slash form still the default stage_prompt
assert stage_prompt("build", "sl-0001") == "/kuru:build SL-0001"
assert stage_prompt_claude("ship", "SL-0004") == "/kuru:ship SL-0004 --no-commit"

plugin = Path("$ROOT").resolve()
kpy = plugin / "scripts" / "kuru.py"
assert kpy.is_file(), kpy
p = stage_prompt_grok("build", "sl-0001", plugin_dir=plugin, kuru_py=kpy)
assert "SL-0001" in p
assert str(kpy) in p or "kuru.py" in p
skill = skill_path_for("build", plugin)
assert skill is not None and "building-a-slice" in str(skill)
assert str(skill) in p
assert "BUILDER" in p or "builder" in p.lower()
assert "verified" in p.lower()  # must warn builder not to set verified

pv = stage_prompt_grok("verify", "SL-0002", plugin_dir=plugin, kuru_py=kpy)
assert "verifying-a-slice" in pv
assert "SL-0002" in pv

ps = stage_prompt_grok("ship", "SL-0003", plugin_dir=plugin, kuru_py=kpy)
assert "done" in ps and "--no-commit" in ps
assert "SL-0003" in ps

assert stage_prompt_for("claude", "build", "SL-0001") == "/kuru:build SL-0001"
pg = stage_prompt_for("grok", "build", "SL-0001", plugin_dir=plugin, kuru_py=kpy)
assert "building-a-slice" in pg

# Construct without a real binary — build_cmd must raise clearly.
be = GrokBackend(plugin_dir=plugin, grok_bin=None, kuru_py=kpy)
try:
    be.build_cmd("hello")
    raise SystemExit("expected GrokNotFoundError")
except GrokNotFoundError:
    pass

# Default always_approve → --always-approve in cmd (no --yolo on grok)
be2 = GrokBackend(plugin_dir=plugin, grok_bin="/usr/bin/true", kuru_py=kpy)
cmd = be2.build_cmd("hi", cwd=Path("/tmp"))
assert cmd[0] == "/usr/bin/true"
assert "-p" in cmd and "hi" in cmd
assert "--always-approve" in cmd
assert "--cwd" in cmd

be3 = GrokBackend(
    plugin_dir=plugin, grok_bin="/usr/bin/true", always_approve=False, kuru_py=kpy
)
assert "--always-approve" not in be3.build_cmd("x")

# run_stage with missing bin → exit 127, log written
import tempfile
td = Path(tempfile.mkdtemp())
log = td / "build.log"
res = be.run_stage(
    stage="build",
    slice_id="SL-0001",
    prompt="",
    cwd=td,
    log_path=log,
)
assert res.exit_code == 127, res
assert "not found" in res.note.lower() or "grok" in res.note.lower(), res.note
assert log.is_file() and log.stat().st_size > 0
assert res.role == "builder"
print("grok unit ok")
PY
[ $? -eq 0 ] && ok "GrokBackend unit: construct, prompts, missing bin, always-approve" \
  || fail "GrokBackend unit checks"

# CLI: --backend grok with bogus --grok-bin refuses cleanly (no live API)
repo_g="$(newrepo)"
seed_workspace "$repo_g"
cd "$repo_g"
"${KURU[@]}" new-slice "Grok miss" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
if "${BOARD[@]}" run --repo "$repo_g" --plugin-dir "$ROOT" -y --backend grok \
    --grok-bin /nonexistent/grok-xyz --no-commit >/tmp/br-grok-miss.$$ 2>&1; then
  fail "grok missing bin should exit non-zero"
else
  grep -qi 'grok CLI not found\|not found' /tmp/br-grok-miss.$$ \
    && ok "CLI --backend grok missing bin: clear error" \
    || { fail "CLI grok missing: unclear error"; cat /tmp/br-grok-miss.$$ | tail -10; }
fi

# Dry-run does not require grok binary
if "${BOARD[@]}" run --repo "$repo_g" --plugin-dir "$ROOT" -y --backend grok \
    --grok-bin /nonexistent/grok-xyz --dry-run >/tmp/br-grok-dry.$$ 2>&1; then
  ok "CLI --backend grok --dry-run works without binary"
else
  fail "dry-run with grok backend should succeed without binary"
  cat /tmp/br-grok-dry.$$ | tail -10
fi

# Popen path: fake grok binary that echoes and exits 0; pid should be set
fake_grok="$tmp/fake-grok"
cat > "$fake_grok" <<'SH'
#!/usr/bin/env bash
# Minimal stand-in for `grok -p …` — ignore flags, print args, exit 0.
echo "fake-grok: $*"
exit 0
SH
chmod +x "$fake_grok"
python3 - <<PY
from pathlib import Path
from board.backends.grok import GrokBackend
import tempfile
plugin = Path("$ROOT")
kpy = plugin / "scripts" / "kuru.py"
be = GrokBackend(plugin_dir=plugin, grok_bin="$fake_grok", kuru_py=kpy)
td = Path(tempfile.mkdtemp())
log = td / "build.log"
res = be.run_stage(
    stage="build",
    slice_id="SL-0001",
    prompt="test prompt for fake grok",
    cwd=td,
    log_path=log,
)
assert res.exit_code == 0, res
assert res.pid is not None and res.pid > 0, res
text = log.read_text()
assert "fake-grok" in text or "test prompt" in text, text[:500]
assert "--always-approve" in text
print("fake grok popen ok pid=", res.pid)
PY
[ $? -eq 0 ] && ok "GrokBackend Popen: pid set + log captures command" \
  || fail "GrokBackend Popen unit"

echo "== Phase 4: cancel (mock mid-stage) =="
repo_c="$(newrepo)"
seed_workspace "$repo_c"
cd "$repo_c"
echo '{"default":{"sleep_ms":800}}' > sc-cancel.json
"${KURU[@]}" new-slice "Cancel me" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
# Cancel from a side thread after stage starts (sleep_ms gives a window)
python3 - <<PY
import json, threading, time
from pathlib import Path
from board.cancel import RunControl
from board.backends.mock import MockBackend, load_mock_scenarios
from board.events import EventWriter, new_run_id
from board.ledger import Ledger, resolve_kuru_py
from board.plan import build_plan
from board.scheduler import Scheduler

repo = Path("$repo_c")
kuru_py = resolve_kuru_py(Path("$ROOT"))
ledger = Ledger(repo, kuru_py)
control = RunControl()
scenarios = load_mock_scenarios(Path("sc-cancel.json"))
backend = MockBackend(ledger, scenarios, control=control)
run_id = new_run_id()
run_dir = repo / ".kuru" / "runs" / run_id
run_dir.mkdir(parents=True, exist_ok=True)
plan = build_plan(ledger.next_all(), max_tries=2)

def cancel_soon():
    time.sleep(0.15)
    msg = control.request_cancel("SL-0001")
    print("cancel_msg:", msg)

thr = threading.Thread(target=cancel_soon, daemon=True)
thr.start()
with EventWriter(run_dir, run_id) as ev:
    sched = Scheduler(
        ledger=ledger, backend=backend, events=ev, run_dir=run_dir,
        review=plan.review, max_tries=2, control=control, skip_check=True,
    )
    result = sched.run(plan)
thr.join(timeout=5)
assert result.results["SL-0001"].outcome == "stuck", result.to_summary()
assert "cancel" in (result.results["SL-0001"].reason or ""), result.to_summary()
# Ledger left as-is (not done) — cancel must not corrupt
st = ledger.show("SL-0001")["status"]
assert st != "done", st
print("cancel ok status=", st, "reason=", result.results["SL-0001"].reason)
PY
[ $? -eq 0 ] && ok "cancel: mock stuck with reason cancelled, ledger not done" \
  || fail "cancel path"

# Pre-cancel before pipeline: immediate stuck
python3 - <<PY
from pathlib import Path
from board.cancel import RunControl
from board.backends.mock import MockBackend
from board.events import EventWriter, new_run_id
from board.ledger import Ledger, resolve_kuru_py
from board.plan import build_plan
from board.scheduler import Scheduler

repo = Path("$repo_c")
# Use a fresh slice
import subprocess
subprocess.check_call(
    ["python3", str(Path("$ROOT") / "scripts" / "kuru.py"), "new-slice", "Pre cancel"],
    cwd=repo, stdout=subprocess.DEVNULL,
)
subprocess.check_call(
    ["python3", str(Path("$ROOT") / "scripts" / "kuru.py"), "set-status", "SL-0002", "ready"],
    cwd=repo, stdout=subprocess.DEVNULL,
)
kuru_py = resolve_kuru_py(Path("$ROOT"))
ledger = Ledger(repo, kuru_py)
control = RunControl()
control.request_cancel("SL-0002")
backend = MockBackend(ledger, control=control)
run_id = new_run_id()
run_dir = repo / ".kuru" / "runs" / run_id
run_dir.mkdir(parents=True, exist_ok=True)
plan = build_plan(ledger.next_all(), scope=["SL-0002"], max_tries=2)
with EventWriter(run_dir, run_id) as ev:
    result = Scheduler(
        ledger=ledger, backend=backend, events=ev, run_dir=run_dir,
        review=plan.review, control=control,
    ).run(plan)
r = result.results.get("SL-0002") or type("R", (), {"outcome": None, "reason": None})()
# either in results or stuck list
assert any(s.get("id") == "SL-0002" for s in result.stuck) or (r and r.outcome == "stuck"), result.to_summary()
print("pre-cancel ok")
PY
[ $? -eq 0 ] && ok "cancel: pre-cancel before start → stuck" || fail "pre-cancel"

echo "== Phase 4: cmd backend construct + dry expand =="
python3 - <<'PY'
from pathlib import Path
import tempfile
from board.backends.cmd import CmdBackend

be = CmdBackend(
    "echo STAGE={stage} SLICE={slice} FILE={prompt_file} CWD={cwd}",
    kuru_py=Path("/tmp/kuru.py"),
)
expanded = be.expand(
    prompt="hello",
    prompt_file="/tmp/p.md",
    cwd="/tmp/repo",
    slice_id="sl-0001",
    stage="build",
)
assert "STAGE=build" in expanded
assert "SLICE=SL-0001" in expanded
assert "FILE=/tmp/p.md" in expanded
assert "CWD=/tmp/repo" in expanded

td = Path(tempfile.mkdtemp())
log = td / "build.log"
res = be.run_stage(
    stage="build",
    slice_id="SL-0001",
    prompt="do the thing",
    cwd=td,
    log_path=log,
)
assert res.exit_code == 0, res
assert log.is_file()
assert (td / "build.prompt.md").is_file()
assert "do the thing" in (td / "build.prompt.md").read_text()
text = log.read_text()
assert "STAGE=build" in text or "build" in text
print("cmd backend ok")
PY
[ $? -eq 0 ] && ok "CmdBackend: expand + run echo template" || fail "CmdBackend unit"

# CLI: --backend cmd without template fails cleanly
repo_cmd="$(newrepo)"
seed_workspace "$repo_cmd"
cd "$repo_cmd"
"${KURU[@]}" new-slice "Cmd" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
if "${BOARD[@]}" run --repo "$repo_cmd" --plugin-dir "$ROOT" -y --backend cmd --no-commit \
    >/tmp/br-cmd-miss.$$ 2>&1; then
  fail "cmd without --backend-cmd should fail"
else
  grep -qi 'backend-cmd\|template' /tmp/br-cmd-miss.$$ \
    && ok "CLI --backend cmd missing template: clear error" \
    || { fail "cmd missing template: unclear error"; tail -10 /tmp/br-cmd-miss.$$; }
fi

echo "== Phase 4: --check-contract default off; flag exercises mock =="
# Default happy path must still skip check (no check stage in events)
repo_ch="$(newrepo)"
seed_workspace "$repo_ch"
cd "$repo_ch"
"${KURU[@]}" new-slice "NoCheck" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${BOARD[@]}" run --repo "$repo_ch" --plugin-dir "$ROOT" -y --backend mock --no-commit \
  >/tmp/br-nocheck.$$ 2>&1 \
  || { fail "default mock run failed"; tail -20 /tmp/br-nocheck.$$; }
ev="$(ls -d .kuru/runs/r_* | tail -1)/events.ndjson"
if grep -q '"stage": "check"' "$ev"; then
  fail "default run should skip check stage"
else
  ok "default run skips contract check"
fi

# With --check-contract + check_flag_times=1: repair then ship
repo_ch2="$(newrepo)"
seed_workspace "$repo_ch2"
cd "$repo_ch2"
echo '{"default":{"check_flag_times":1}}' > sc-check.json
"${KURU[@]}" new-slice "CheckFix" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${BOARD[@]}" run --repo "$repo_ch2" --plugin-dir "$ROOT" -y --backend mock \
  --mock-scenario sc-check.json --check-contract --no-commit >/tmp/br-check.$$ 2>&1 \
  || { fail "check-contract repair path failed"; tail -30 /tmp/br-check.$$; }
st="$("${KURU[@]}" show SL-0001 --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
[ "$st" = "done" ] && ok "check-contract flag + repair ships to done" || fail "status=$st after check-contract"
ev2="$(ls -d .kuru/runs/r_* | tail -1)/events.ndjson"
grep -q '"stage": "check"' "$ev2" && grep -q '"stage": "repair"' "$ev2" \
  && ok "events include check + repair stages" || fail "missing check/repair events"

# Permanently flagged → stuck
repo_ch3="$(newrepo)"
seed_workspace "$repo_ch3"
cd "$repo_ch3"
echo '{"default":{"check":"flagged"}}' > sc-bad.json
"${KURU[@]}" new-slice "BadContract" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${BOARD[@]}" run --repo "$repo_ch3" --plugin-dir "$ROOT" -y --backend mock \
  --mock-scenario sc-bad.json --check-contract --no-commit >/tmp/br-checkbad.$$ 2>&1 || true
sum="$(ls .kuru/runs/*/summary.json | head -1)"
python3 -c "
import json
s=json.load(open('$sum'))
r=s['results']['SL-0001']
assert r['outcome']=='stuck', r
assert 'flagged' in r['reason'] or 'contract' in r['reason'], r
" && ok "check permanently flagged → stuck" || fail "flagged contract outcome wrong"

echo "== Phase 4: board status + logs =="
repo_st="$(newrepo)"
seed_workspace "$repo_st"
cd "$repo_st"
"${KURU[@]}" new-slice "Status" >/dev/null
"${KURU[@]}" set-status SL-0001 ready >/dev/null
"${BOARD[@]}" run --repo "$repo_st" --plugin-dir "$ROOT" -y --backend mock --no-commit \
  >/tmp/br-st.$$ 2>&1 || true
out="$("${BOARD[@]}" status --repo "$repo_st" 2>/dev/null)"
echo "$out" | grep -q 'shipped=1\|shipped' \
  && ok "board status lists run summary" || { fail "board status empty"; echo "$out"; }
logpath="$("${BOARD[@]}" logs --repo "$repo_st" --slice SL-0001 --stage build 2>/dev/null)"
[ -f "$logpath" ] && ok "board logs prints build.log path" || fail "board logs path missing: $logpath"

echo
echo "board selftest: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
