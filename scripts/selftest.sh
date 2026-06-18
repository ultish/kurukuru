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

newrepo() {
  local d; d="$(mktemp -d)"; cd "$d" || exit 1
  if git init -q 2>/dev/null; then
    # a local identity so the auto-commit-on-done can actually commit in CI
    git config user.email kuru@test.local; git config user.name "kuru test"
  fi
  echo "$d"
}
trivial_gates() { printf '{"project":"t","gates":{"unit":{"cmd":"true","required":true,"timeout":60}}}\n' > .kuru/config.json; }
# drive a fresh slice all the way to `verified` (gates green); leaves cwd in the repo
drive_to_verified() {
  $KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
  $KURU set-status SL-0001 built --by builder >/dev/null
  $KURU set-status SL-0001 verifying --by verifier >/dev/null
  $KURU gate SL-0001 >/dev/null 2>&1
  $KURU set-status SL-0001 verified --by verifier >/dev/null
}

echo "== SL-1: init + new-slice scaffold =="
newrepo >/dev/null
expect_ok   "init scaffolds .kuru" $KURU init
for f in config.json ledger.json charter.md progress.md README.md init.sh .gitignore; do
  [ -f ".kuru/$f" ] && ok "init wrote $f" || fail "init missing $f"
done
grep -q "^engine$" .kuru/.gitignore && ok ".gitignore excludes machine-local engine path" || fail ".gitignore missing engine"
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
prof="$(mktemp -d)/gradle-kube.json"
printf '{"stack":"gradle","config":{"gates":{"unit":{"cmd":"./gradlew test","required":true,"timeout":60}}},"environment":{"language":"Kotlin/JDK21"}}\n' > "$prof"
expect_ok "init --profile (config is guidance only)" $KURU init --profile "$prof"
# init seeds config.json from the profile's STACK preset (gradle), NOT from its
# `config` block verbatim — that block is guidance for /kuru:charter to apply later.
grep -q "gradlew" .kuru/config.json && ok "config seeded from profile stack preset" || fail "stack preset not seeded"
grep -q "gradlew test" .kuru/config.json && fail "profile config applied verbatim (should be guidance only)" || ok "profile config NOT applied verbatim (charter's job)"
# a single profile is stashed under .kuru/profiles/<stem>.json for the charter
[ -f ".kuru/profiles/gradle-kube.json" ] && grep -q "Kotlin/JDK21" ".kuru/profiles/gradle-kube.json" \
  && ok "profile stashed under .kuru/profiles/ for charter" || fail "profile not stashed under .kuru/profiles/"
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
# REPEATABLE --profile: a catalog of single-stack profiles for a polyglot repo. Both
# are stashed for the charter to match to apps; with >1 profile init seeds the node
# default (the charter, not init, decides which apply and to which dir).
newrepo >/dev/null
pg="$(mktemp -d)/gradle-svc.json"; pw="$(mktemp -d)/pnpm-web.json"
printf '{"stack":"gradle","environment":{"language":"Kotlin"}}\n' > "$pg"
printf '{"stack":"pnpm","environment":{"language":"TypeScript"}}\n' > "$pw"
expect_ok "init with multiple --profile (catalog)" $KURU init --profile "$pg" --profile "$pw"
[ -f .kuru/profiles/gradle-svc.json ] && [ -f .kuru/profiles/pnpm-web.json ] \
  && ok "every profile stashed under .kuru/profiles/" || fail "not all profiles stashed"
grep -q "npm " .kuru/config.json && ok "multi-profile init seeds the node default (charter composes targets)" || fail "multi-profile seed wrong"
expect_ok "doctor healthy after a multi-profile init" $KURU doctor

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

echo "== opt-in review: a verified slice ships straight to done (action=ship) =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1; $KURU set-status SL-0001 verified --by verifier >/dev/null
nx="$($KURU next --json)"
echo "$nx" | grep -q '"id": "SL-0001"' && echo "$nx" | grep -q '"next_action": "ship"' \
  && ok "verified slice surfaces via next (action ship)" || fail "verified not shippable: $nx"
expect_ok "verified->done allowed directly (review opt-in)" $KURU set-status SL-0001 done

echo "== auto-commit: marking a slice done commits the working tree =="
newrepo >/dev/null; repo="$(pwd)"
$KURU init >/dev/null; trivial_gates; $KURU new-slice "auto commit me" >/dev/null
echo "feature code" > feature.txt
drive_to_verified
before="$(git -C "$repo" rev-list --count HEAD 2>/dev/null || echo 0)"
out="$($KURU set-status SL-0001 done 2>&1)"
echo "$out" | grep -q "committed" && ok "set-status done reports a commit" || fail "no commit reported: $out"
after="$(git -C "$repo" rev-list --count HEAD)"
[ "$after" -gt "$before" ] && ok "a new commit exists after done ($before -> $after)" || fail "commit count did not grow"
git -C "$repo" log -1 --pretty=%s | grep -q "ship SL-0001" && ok "commit subject names the slice" || fail "commit subject wrong"
# the feature file and the .kuru ledger transition are captured in that commit
git -C "$repo" show --stat HEAD | grep -q "feature.txt" && ok "slice code is in the commit" || fail "feature.txt not committed"
[ -z "$(git -C "$repo" status --porcelain)" ] && ok "working tree clean after auto-commit" || fail "tree dirty after commit"

echo "== auto-commit: non-git dir degrades gracefully (no crash) =="
d="$(mktemp -d)"; cd "$d"
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
drive_to_verified
expect_ok "set-status done succeeds without a git repo" $KURU set-status SL-0001 done

echo "== targets: per-app gate sets run in their own dir =="
newrepo >/dev/null
$KURU init >/dev/null
mkdir -p services/api apps/web
# api gate passes only if run in services/api; web gate only in apps/web
printf '{"project":"mono","targets":{"api":{"dir":"services/api","gates":{"build":{"cmd":"test -f here-api","required":true,"timeout":60}}},"web":{"dir":"apps/web","gates":{"lint":{"cmd":"test -f here-web","required":true,"timeout":60}}}}}\n' > .kuru/config.json
touch services/api/here-api apps/web/here-web
$KURU new-slice "api thing" --target api >/dev/null
$KURU new-slice "web thing" --target web >/dev/null
expect_ok "doctor healthy with multiple targets" $KURU doctor
$KURU ls | grep -q "TARGET" && ok "ls shows a TARGET column when slices are targeted" || fail "no TARGET column"
$KURU next --json | grep -q '"target": "api"' && ok "next --json carries the slice target" || fail "next missing target"
# SL-0001 (api): its build gate is `test -f here-api`, which only passes in services/api
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null
g="$($KURU gate SL-0001 2>&1)"
echo "$g" | grep -q "target: api" && ok "gate names the resolved target" || fail "gate target label missing: $g"
echo "$g" | grep -q "services/api" && ok "gate runs in the target's dir" || fail "gate cwd wrong: $g"
$KURU show SL-0001 --json | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d['gate']['target']=='api' and d['gate']['passed'] else 1)" \
  && ok "api gate passed in its own dir (and recorded target)" || fail "api gate did not pass in services/api"
# a web slice's gate (test -f here-web) would FAIL if run in the repo root; it passes only in apps/web
$KURU set-status SL-0002 ready >/dev/null; $KURU set-status SL-0002 in_progress >/dev/null
$KURU set-status SL-0002 built --by builder >/dev/null; $KURU gate SL-0002 >/dev/null 2>&1
$KURU show SL-0002 --json | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d['gate']['passed'] else 1)" \
  && ok "web gate passed in apps/web (proves per-target scoping)" || fail "web gate failed — scoping wrong"

echo "== targets: validation + set-target + back-compat =="
newrepo >/dev/null
$KURU init >/dev/null
mkdir -p services/api apps/web
printf '{"project":"mono","targets":{"api":{"dir":"services/api","gates":{"build":{"cmd":"true","required":true,"timeout":60}}},"web":{"dir":"apps/web","gates":{"lint":{"cmd":"true","required":true,"timeout":60}}}}}\n' > .kuru/config.json
expect_fail "new-slice rejects an unknown target" "unknown target" $KURU new-slice "x" --target nope
$KURU new-slice "untargeted" >/dev/null   # no --target while multiple targets exist
expect_fail "doctor flags an untargeted slice when several targets exist" "no target" $KURU doctor
expect_fail "gate refuses a slice with no resolvable target" "no target" $KURU gate SL-0001
expect_ok "set-target assigns a valid target" $KURU set-target SL-0001 web
expect_fail "set-target rejects an unknown target" "unknown target" $KURU set-target SL-0001 bogus
expect_ok "doctor healthy once the slice is targeted" $KURU doctor
# top-level gates alongside targets is flagged as ignored
printf '{"project":"mono","gates":{"unit":{"cmd":"true"}},"targets":{"api":{"dir":".","gates":{"build":{"cmd":"true","required":true,"timeout":60}}}}}\n' > .kuru/config.json
expect_fail "doctor flags top-level gates shadowed by targets" "ignored" $KURU doctor
# back-compat: a flat (no-targets) config still works as a single 'default' target
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null
expect_ok "flat config still gates (single default target)" $KURU gate SL-0001
$KURU show SL-0001 --json | python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin)['gate']['target']=='default' else 1)" \
  && ok "flat config resolves to the 'default' target" || fail "flat config target wrong"
# set-stack --target seeds one target without clobbering the other
newrepo >/dev/null
$KURU init >/dev/null
$KURU set-stack node --target web >/dev/null
$KURU set-stack go --target api >/dev/null
python3 -c "import json,sys; t=json.load(open('.kuru/config.json'))['targets']; sys.exit(0 if set(t)=={'web','api'} else 1)" \
  && ok "set-stack --target keeps both targets (no clobber)" || fail "set-stack --target clobbered a target"

echo "== reviewed: a reviewed-but-unshipped slice is visible to next (action=ship) =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1; $KURU set-status SL-0001 verified --by verifier >/dev/null
$KURU set-status SL-0001 reviewed --by reviewer >/dev/null
nx="$($KURU next --json)"
echo "$nx" | grep -q '"id": "SL-0001"' && echo "$nx" | grep -q '"next_action": "ship"' \
  && ok "reviewed slice surfaces via next (action ship)" || fail "reviewed not surfaced: $nx"

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

echo "== dropped: retire a slice; resurrect via draft =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates
$KURU new-slice "wrong scope" >/dev/null
$KURU new-slice "needs 1" --depends-on SL-0001 >/dev/null
$KURU set-status SL-0001 ready >/dev/null
$KURU set-status SL-0002 ready >/dev/null
expect_ok "ready->dropped" $KURU set-status SL-0001 dropped --note "re-writing"
$KURU next --json | grep -q '"next_action": "none"' && ok "next ignores dropped slices" || fail "next acted on a dropped slice"
expect_fail "doctor flags dependency on dropped slice" "dropped" $KURU doctor
expect_fail "dropped->ready refused (resurrect via draft)" "illegal transition" $KURU set-status SL-0001 ready
expect_ok "dropped->draft resurrects (same id, deps stay valid)" $KURU set-status SL-0001 draft
expect_ok "doctor healthy after resurrect" $KURU doctor

echo "== gate freshness: a stale gate run cannot unlock verified =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1
$KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU set-status SL-0001 rejected --by verifier >/dev/null
$KURU set-status SL-0001 in_progress >/dev/null
sleep 1   # the rebuild timestamp must be strictly after the old gate run
$KURU set-status SL-0001 built --by builder >/dev/null
$KURU set-status SL-0001 verifying --by verifier >/dev/null
expect_fail "stale gate run refused after rebuild" "stale" $KURU set-status SL-0001 verified --by verifier
$KURU gate SL-0001 >/dev/null 2>&1
expect_ok "fresh gate run unlocks verified" $KURU set-status SL-0001 verified --by verifier

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
expect_fail "shipped (done) work cannot be dropped" "illegal transition" $KURU set-status SL-0001 dropped
$KURU ls --status done | grep -q SL-0001 && ok "board ends all-done" || fail "slice not done"

echo
echo "ALL PASS ($pass checks)"
