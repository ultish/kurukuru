#!/usr/bin/env bash
# selftest.sh — regression test for the kuru engine's guarantees.
#
# Reproduces impl/BUILD_PLAN.md §7 SL-1 / SL-2 / SL-6 (plus the verifying-state and
# --stack behaviors) in throwaway temp dirs. Exits non-zero on the first failure,
# so the harness can self-check (`scripts/selftest.sh`).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CLAUDE_PLUGIN_ROOT="$ROOT"
KURU="python3 $ROOT/scripts/kuru.py"

pass=0
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { pass=$((pass+1)); echo "  ok: $*"; }

# expect_ok "<desc>" <cmd...>        — command must exit 0
expect_ok()   { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else fail "$d (expected exit 0)"; fi; }
# expect_fail "<desc>" "<substr>" <cmd...> — command must exit non-zero AND stderr contain substr
expect_fail() {
  local d="$1" sub="$2"; shift 2
  local out; out="$("$@" 2>&1)"; local rc=$?
  if [ $rc -eq 0 ]; then fail "$d (expected non-zero exit)"; fi
  case "$out" in *"$sub"*) ok "$d" ;; *) fail "$d (missing '$sub' in: $out)" ;; esac
}

newrepo() { local d; d="$(mktemp -d)"; cd "$d" || exit 1; git init -q 2>/dev/null || true; echo "$d"; }
trivial_gates() { printf '{"project":"t","gates":{"unit":{"cmd":"true","required":true,"timeout":60}}}\n' > .kuru/config.json; }

echo "== SL-1: init + new-slice scaffold =="
newrepo >/dev/null
expect_ok   "init scaffolds .kuru" $KURU init
for f in config.json ledger.json charter.md progress.md README.md init.sh; do
  [ -f ".kuru/$f" ] && ok "init wrote $f" || fail "init missing $f"
done
[ -x ".kuru/init.sh" ] && ok "init.sh is executable" || fail "init.sh not executable"
[ -f ".kuru/engine" ] && grep -q "kuru.py" .kuru/engine && ok "init records engine path (.kuru/engine)" || fail "engine path not recorded"
expect_ok   "doctor healthy" $KURU doctor
expect_ok   "new-slice creates SL-0001" $KURU new-slice "demo"
for f in slice.md contract.yml build-log.md verification.md; do
  [ -f ".kuru/slices/SL-0001/$f" ] && ok "slice artifact $f" || fail "missing slice artifact $f"
done

echo "== SL-4 (engine slice of it): --stack presets =="
newrepo >/dev/null
expect_ok "init --stack python" $KURU init --stack python
grep -q "pytest" .kuru/config.json && grep -q "ruff" .kuru/config.json && grep -q "mypy" .kuru/config.json \
  && ok "python preset has pytest/ruff/mypy" || fail "python preset gates wrong"
newrepo >/dev/null
expect_ok "init --stack go" $KURU init --stack go
grep -q "go test" .kuru/config.json && grep -q "go vet" .kuru/config.json && grep -q "go build" .kuru/config.json \
  && ok "go preset has test/vet/build" || fail "go preset gates wrong"
newrepo >/dev/null
expect_fail "unknown --stack errors" "missing template" $KURU init --stack nope

echo "== set-stack: reconfigure gates from a build-tool preset =="
newrepo >/dev/null
$KURU init >/dev/null            # default node config
grep -q "npm run" .kuru/config.json && ok "init default is node/npm" || fail "default not node"
expect_ok "set-stack gradle" $KURU set-stack gradle
grep -q "gradlew" .kuru/config.json && ok "config now gradle (./gradlew)" || fail "set-stack gradle didn't apply"
expect_ok "set-stack maven" $KURU set-stack maven
grep -q "mvn " .kuru/config.json && ok "config now maven (mvn)" || fail "set-stack maven didn't apply"
expect_ok "set-stack pnpm"  $KURU set-stack pnpm
grep -q "pnpm " .kuru/config.json && ok "config now pnpm" || fail "set-stack pnpm didn't apply"
expect_ok "set-stack cargo" $KURU set-stack cargo
grep -q "cargo " .kuru/config.json && ok "config now cargo" || fail "set-stack cargo didn't apply"
expect_ok "doctor healthy after set-stack" $KURU doctor
expect_fail "set-stack unknown preset errors" "missing template" $KURU set-stack bogus

echo "== init --profile: reusable environment profile (guidance, not gospel) =="
newrepo >/dev/null
prof="$(mktemp)"
printf '{"stack":"gradle","config":{"gates":{"unit":{"cmd":"./gradlew test","required":true,"timeout":60}}},"environment":{"language":"Kotlin/JDK21"}}\n' > "$prof"
expect_ok "init --profile (config is guidance only)" $KURU init --profile "$prof"
# init seeds config.json from the profile's STACK preset (gradle), NOT from its
# `config` block verbatim — that block is guidance for /kuru:charter to apply later.
grep -q "gradlew" .kuru/config.json && ok "config seeded from profile stack preset" || fail "stack preset not seeded"
grep -q "gradlew test" .kuru/config.json && fail "profile config applied verbatim (should be guidance only)" || ok "profile config NOT applied verbatim (charter's job)"
[ -f .kuru/profile.json ] && grep -q "Kotlin/JDK21" .kuru/profile.json && ok "profile stashed to .kuru/profile.json for charter" || fail "profile.json missing"
python3 -c "import json;assert json.load(open('.kuru/config.json'))['project']" && ok "seeded config got a project name" || fail "no project in config"
newrepo >/dev/null
printf '{"stack":"pnpm"}\n' > "$prof"
expect_ok "init --profile (stack only)" $KURU init --profile "$prof"
grep -q "pnpm " .kuru/config.json && ok "stack-only profile picks the preset" || fail "stack-only profile failed"
# profile with ONLY a config block (no stack) falls back to the node default seed.
newrepo >/dev/null
printf '{"config":{"gates":{"unit":{"cmd":"./gradlew test","required":true,"timeout":60}}}}\n' > "$prof"
expect_ok "init --profile (config only, no stack)" $KURU init --profile "$prof"
grep -q "npm " .kuru/config.json && ok "config-only profile falls back to node default seed" || fail "config-only profile didn't fall back to node"

echo "== SL-2: status + gate enforcement =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
expect_fail "illegal draft->done refused" "illegal transition" $KURU set-status SL-0001 done
$KURU set-status SL-0001 ready >/dev/null
$KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null
expect_fail "built->verified skips verifying, refused" "illegal transition" $KURU set-status SL-0001 verified --by verifier
$KURU set-status SL-0001 verifying --by verifier >/dev/null
expect_fail "verified with no gate run refused" "no gate run" $KURU set-status SL-0001 verified --by verifier

# failing gate blocks verify
newrepo >/dev/null
$KURU init >/dev/null
printf '{"project":"t","gates":{"unit":{"cmd":"false","required":true,"timeout":60}}}\n' > .kuru/config.json
$KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1
expect_fail "failing gate blocks verify" "gate" $KURU set-status SL-0001 verified --by verifier

# builder cannot self-certify even with green gates
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by builder >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1
expect_fail "builder cannot set verified" "builder may not" $KURU set-status SL-0001 verified --by builder

echo "== review: a failed code review rejects (verified->rejected), never ->in_progress =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1; $KURU set-status SL-0001 verified --by verifier >/dev/null
# the broken path the commands used to describe must stay illegal
expect_fail "verified->in_progress refused (review must reject)" "illegal transition" $KURU set-status SL-0001 in_progress --by reviewer
# the correct send-back: reviewer rejects
expect_ok "verified->rejected by reviewer" $KURU set-status SL-0001 rejected --by reviewer --note "fix X"
# a reviewer rejection is counted toward the retry cap (show --json rejections)
$KURU show SL-0001 --json | python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin)['rejections']>=1 else 1)" \
  && ok "reviewer rejection counts toward retry cap" || fail "rejection not counted"
# from rejected, next dispatches a build
$KURU next --json | grep -q '"next_action": "build"' && ok "rejected -> next says build" || fail "rejected next wrong"

echo "== reviewed: a reviewed-but-unshipped slice is visible to next (action=review->done) =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1; $KURU set-status SL-0001 verified --by verifier >/dev/null
$KURU set-status SL-0001 reviewed --by reviewer >/dev/null
nx="$($KURU next --json)"
echo "$nx" | grep -q '"id": "SL-0001"' && echo "$nx" | grep -q '"next_action": "review"' \
  && ok "reviewed slice surfaces via next (action review)" || fail "reviewed not surfaced: $nx"

echo "== deps: --json + dependency chains =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates
$KURU new-slice "skeleton" >/dev/null
$KURU new-slice "needs 1" --depends-on SL-0001 >/dev/null
$KURU set-status SL-0001 ready >/dev/null
$KURU set-status SL-0002 ready >/dev/null
# next --json must pick SL-0001 (SL-0002 is dep-blocked)
nx="$($KURU next --json)"
echo "$nx" | grep -q '"id": "SL-0001"' && echo "$nx" | grep -q '"next_action": "build"' \
  && ok "next --json picks the unblocked slice" || fail "next --json wrong: $nx"
expect_fail "ready->in_progress refused while dep unmet" "unmet dependencies" $KURU set-status SL-0002 in_progress
# drive SL-0001 to done, then SL-0002 unblocks
$KURU set-status SL-0001 in_progress >/dev/null; $KURU set-status SL-0001 built --by builder >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU set-status SL-0001 verified --by verifier >/dev/null
$KURU set-status SL-0001 reviewed --by reviewer >/dev/null; $KURU set-status SL-0001 done >/dev/null
nx="$($KURU next --json)"
echo "$nx" | grep -q '"id": "SL-0002"' && ok "next --json unblocks dependent after dep done" || fail "still blocked: $nx"
expect_ok "ready->in_progress now allowed" $KURU set-status SL-0002 in_progress
# doctor catches an unknown dependency
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates
$KURU new-slice "bad dep" --depends-on SL-9999 >/dev/null
expect_fail "doctor flags unknown dependency" "unknown slice" $KURU doctor
# ls --json emits an array
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "a" >/dev/null
$KURU ls --json | python3 -c "import json,sys; a=json.load(sys.stdin); assert isinstance(a,list) and a[0]['id']=='SL-0001'" \
  && ok "ls --json is a parseable array" || fail "ls --json bad"

echo "== SL-6: full draft->done lifecycle runs clean =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "ship it" >/dev/null
expect_ok "ready"      $KURU set-status SL-0001 ready
expect_ok "in_progress" $KURU set-status SL-0001 in_progress
expect_ok "built"      $KURU set-status SL-0001 built --by builder
expect_ok "gate green" $KURU gate SL-0001
[ -f .kuru/slices/SL-0001/gate-unit.log ] && ok "gate writes a tailable log file" || fail "no gate log written"
python3 -c "import json,sys; r=json.load(open('.kuru/slices/SL-0001/gate-results.json')); sys.exit(0 if r['gates'][0].get('log') else 1)" \
  && ok "gate-results.json records the log path" || fail "gate result missing log path"
expect_ok "verifying"  $KURU set-status SL-0001 verifying --by verifier
expect_ok "verified"   $KURU set-status SL-0001 verified --by verifier
expect_ok "reviewed"   $KURU set-status SL-0001 reviewed --by reviewer
expect_ok "done"       $KURU set-status SL-0001 done
$KURU ls --status done | grep -q SL-0001 && ok "board ends all-done" || fail "slice not done"

echo
echo "ALL PASS ($pass checks)"
