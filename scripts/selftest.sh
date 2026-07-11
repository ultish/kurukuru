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
for f in config.json ledger.json charter.md progress.md BOARD_HANDOFF.md README.md init.sh .gitignore; do
  [ -f ".kuru/$f" ] && ok "init wrote $f" || fail "init missing $f"
done
grep -q "Board handoff" .kuru/BOARD_HANDOFF.md && ok "BOARD_HANDOFF.md has orient content" || fail "BOARD_HANDOFF.md empty/wrong"
grep -q "^engine$" .kuru/.gitignore && ok ".gitignore excludes machine-local engine path" || fail ".gitignore missing engine"
grep -qE '^runs/?$' .kuru/.gitignore && ok ".gitignore excludes board runs/" || fail ".gitignore missing runs/"

# init links existing AGENTS.md / CLAUDE.md (does not create them)
newrepo >/dev/null
printf '# Agents\n\nProject rules.\n' > AGENTS.md
printf '# Claude\n\nProject memory.\n' > CLAUDE.md
$KURU init >/dev/null
grep -q "kurukuru:begin" AGENTS.md && grep -q "BOARD_HANDOFF.md" AGENTS.md \
  && ok "init links Kurukuru into existing AGENTS.md" || fail "AGENTS.md not linked"
grep -q "kurukuru:begin" CLAUDE.md && ok "init links Kurukuru into existing CLAUDE.md" || fail "CLAUDE.md not linked"
$KURU init --force >/dev/null
n=$(grep -c "kurukuru:begin" AGENTS.md || true)
[ "$n" = "1" ] && ok "init does not duplicate AGENTS.md link" || fail "AGENTS.md link duplicated ($n)"
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
# CATALOG --profile <DIR>: a directory of single-stack profiles for a polyglot repo.
# Every *.json is stashed for the charter to match to apps; with >1 profile init seeds
# the node default (the charter, not init, decides which apply and to which dir).
newrepo >/dev/null
cat="$(mktemp -d)"
printf '{"stack":"gradle","environment":{"language":"Kotlin"}}\n' > "$cat/gradle-svc.json"
printf '{"stack":"pnpm","environment":{"language":"TypeScript"}}\n' > "$cat/pnpm-web.json"
expect_ok "init --profile <dir> (catalog)" $KURU init --profile "$cat"
[ -f .kuru/profiles/gradle-svc.json ] && [ -f .kuru/profiles/pnpm-web.json ] \
  && ok "every *.json in the catalog dir stashed under .kuru/profiles/" || fail "not all profiles stashed"
grep -q "npm " .kuru/config.json && ok "multi-profile catalog seeds the node default (charter composes targets)" || fail "multi-profile seed wrong"
expect_ok "doctor healthy after a catalog init" $KURU doctor
# an empty catalog dir (no *.json) is a clear error, not a silent no-op.
newrepo >/dev/null
expect_fail "init --profile <empty dir> errors" "no *.json profiles" $KURU init --profile "$(mktemp -d)"
# a non-existent location is rejected too.
newrepo >/dev/null
expect_fail "init --profile <bogus path> errors" "not a directory" $KURU init --profile /no/such/catalog

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

echo "== review policy: on by default (verified->review), set-review off -> ship straight =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
# init seeds meta.review on
python3 -c "import json,sys; sys.exit(0 if json.load(open('.kuru/ledger.json'))['meta'].get('review') is True else 1)" \
  && ok "kuru init seeds review ON by default" || fail "init did not seed review on"
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1; $KURU set-status SL-0001 verified --by verifier >/dev/null
# review ON: next routes a verified slice to REVIEW (not ship), and reports the policy
nx="$($KURU next --json)"
echo "$nx" | grep -q '"next_action": "review"' && echo "$nx" | grep -q '"review": true' \
  && ok "review on: verified slice surfaces via next (action review)" || fail "verified action not review: $nx"
# toggling review OFF makes a verified slice ship straight to done
$KURU set-review off >/dev/null
nx="$($KURU next --json)"
echo "$nx" | grep -q '"next_action": "ship"' && echo "$nx" | grep -q '"review": false' \
  && ok "set-review off: verified slice action becomes ship" || fail "verified action not ship after off: $nx"
# the verified->done transition stays legal regardless of policy (policy only routes `next`)
expect_ok "verified->done allowed directly (policy routes next, not the transition)" $KURU set-status SL-0001 done
# --no-review at init seeds it off
newrepo >/dev/null
$KURU init --no-review >/dev/null
python3 -c "import json,sys; sys.exit(0 if json.load(open('.kuru/ledger.json'))['meta'].get('review') is False else 1)" \
  && ok "kuru init --no-review seeds review OFF" || fail "init --no-review did not seed review off"

echo "== ship: default auto-commits; --no-commit defers the commit to the caller =="
# Default ship: done auto-commits the working tree.
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
drive_to_verified
git add -A >/dev/null && git commit -q -m base    # baseline so HEAD exists and tree is clean
echo dirty > artifact.txt                          # an uncommitted change in the tree
before="$(git rev-parse HEAD)"
$KURU set-status SL-0001 done >/dev/null
[ "$before" != "$(git rev-parse HEAD)" ] && ok "default ship: auto-commit advanced HEAD" || fail "default ship did not commit"
[ -z "$(git status --porcelain)" ] && ok "default ship: working tree clean after commit" || fail "default ship left tree dirty"

# --no-commit ship: flips the ledger to done but makes no commit; the caller commits later.
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates; $KURU new-slice "x" >/dev/null
drive_to_verified
git add -A >/dev/null && git commit -q -m base
echo dirty > artifact.txt
before="$(git rev-parse HEAD)"
$KURU set-status SL-0001 done --no-commit >/dev/null
[ "$before" = "$(git rev-parse HEAD)" ] && ok "--no-commit: HEAD did not advance" || fail "--no-commit still committed"
git status --porcelain | grep -q artifact.txt && ok "--no-commit: tree left dirty for the caller to commit" || fail "--no-commit unexpectedly committed the tree"
$KURU ls --status done | grep -q SL-0001 && ok "--no-commit: slice is still done in the ledger" || fail "--no-commit did not set done"

echo "== next --slice: single-slice focus, independent of the board's ranking =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates
$KURU new-slice "A" >/dev/null            # SL-0001
$KURU new-slice "B" --depends-on SL-0001 >/dev/null   # SL-0002 (blocked on A)
$KURU new-slice "C" >/dev/null            # SL-0003 (independent, also ready)
$KURU set-status SL-0001 ready >/dev/null
$KURU set-status SL-0002 ready >/dev/null   # contracted, but dep SL-0001 not done
$KURU set-status SL-0003 ready >/dev/null
# a named slice waiting on an unfinished dep yields no action (deps enforced):
$KURU next --slice SL-0002 --json | grep -q '"reason": "waiting_on_deps"' \
  && ok "next --slice on dep-unmet slice -> none/waiting_on_deps" || fail "deps not enforced for --slice"
# drive A to verified; meanwhile C stays a fresh ready sibling
$KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null
$KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1
$KURU set-status SL-0001 verified --by verifier >/dev/null
# the board's next prefers BUILDING the fresh ready C over reviewing/shipping verified A:
$KURU next --json | grep -q '"id": "SL-0003"' \
  && ok "board next prefers a ready sibling over a verified slice" || fail "board ranking changed"
# but next --slice keeps focus on A; with review on (init default) its action is review:
nx="$($KURU next --slice SL-0001 --json)"
echo "$nx" | grep -q '"id": "SL-0001"' && echo "$nx" | grep -q '"next_action": "review"' \
  && ok "next --slice SL-0001 stays on A (action review), ignoring the board" || fail "next --slice drifted: $nx"
# terminal/blocked slices report none with a clear reason:
$KURU set-status SL-0001 done >/dev/null
$KURU next --slice SL-0001 --json | grep -q '"reason": "done"' \
  && ok "next --slice on a done slice -> none/done" || fail "done reason wrong"
$KURU set-status SL-0003 blocked --note x >/dev/null
$KURU next --slice SL-0003 --json | grep -q '"reason": "blocked"' \
  && ok "next --slice on a blocked slice -> none/blocked" || fail "blocked reason wrong"
expect_fail "next --slice on a missing id errors" "no slice" $KURU next --slice SL-9999 --json

echo "== reuse-stats: rolls up builders' REUSE-LOOKUP lines =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates
$KURU new-slice "reused one" >/dev/null   # SL-0001 — built, records a reuse
$KURU new-slice "silent one" >/dev/null   # SL-0002 — built, emits no line
$KURU new-slice "not built"  >/dev/null   # SL-0003 — never built (excluded)
for s in SL-0001 SL-0002; do
  $KURU set-status $s ready >/dev/null
  $KURU set-status $s in_progress --by builder >/dev/null
  $KURU set-status $s built --by builder >/dev/null
done
printf '\nREUSE-LOOKUP {"used":true,"queries":2,"candidates":3,"reused":true,"semantic":false,"detail":"extended X"}\n' >> .kuru/slices/SL-0001/build-log.md
rs="$($KURU reuse-stats --json)"
echo "$rs" | grep -q '"built": 2'          && ok "reuse-stats counts built slices, excludes unbuilt" || fail "built count wrong: $rs"
echo "$rs" | grep -q '"reported": 1'        && ok "reuse-stats counts recorded lookups"              || fail "reported wrong: $rs"
echo "$rs" | grep -q '"missing_report": 1'  && ok "reuse-stats flags a built slice with no line"     || fail "missing_report wrong: $rs"
echo "$rs" | grep -q '"led_to_reuse": 1'    && ok "reuse-stats counts reuse outcomes"                || fail "led_to_reuse wrong: $rs"
# a malformed REUSE-LOOKUP line must never crash the rollup
printf '\nREUSE-LOOKUP {not valid json\n' >> .kuru/slices/SL-0002/build-log.md
expect_ok "reuse-stats tolerates a malformed REUSE-LOOKUP line" $KURU reuse-stats

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
# set-stack --target converting a single-app config must resolve the flat gates explicitly
newrepo >/dev/null
$KURU init >/dev/null   # flat top-level gates (node default)
expect_fail "set-stack --target refuses to silently drop a single-app gates" "single-app top-level" \
  $KURU set-stack node --target web
expect_fail "set-stack --target rejects both resolve flags at once" "only one of" \
  $KURU set-stack node --target web --discard-flat-gates --migrate-flat-gates-to old
expect_fail "set-stack --migrate-flat-gates-to cannot collide with --target" "collides" \
  $KURU set-stack node --target web --migrate-flat-gates-to web
# discard: drop the init default, seed the new target; second target needs no flag (no flat left)
$KURU set-stack node --target web --discard-flat-gates >/dev/null
$KURU set-stack go --target api >/dev/null
python3 -c "import json,sys; c=json.load(open('.kuru/config.json')); sys.exit(0 if set(c['targets'])=={'web','api'} and 'gates' not in c else 1)" \
  && ok "set-stack --target (discard) keeps both targets, no orphan top-level gates" || fail "set-stack --target discard wrong"
# migrate: keep the existing single-app gates as its own named target (dir '.')
newrepo >/dev/null
$KURU init >/dev/null
$KURU set-stack go --target api --migrate-flat-gates-to web >/dev/null
python3 -c "import json,sys; c=json.load(open('.kuru/config.json')); t=c['targets']; sys.exit(0 if set(t)=={'web','api'} and 'gates' not in c and t['web']['dir']=='.' and t['web']['gates'] else 1)" \
  && ok "set-stack --migrate-flat-gates-to keeps the old config as a target" || fail "set-stack migrate wrong"

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

echo "== reuse gate: init --reuse-check seeds a dupehound gate; --waive overrides a failure =="
# default init seeds NO reuse gate (opt-in only)
newrepo >/dev/null
$KURU init >/dev/null
python3 -c "import json,sys; c=json.load(open('.kuru/config.json')); sys.exit(0 if 'reuse' not in c.get('gates',{}) and 'reuse' not in c.get('repo_gates',{}) else 1)" \
  && ok "default init seeds no reuse gate" || fail "default init added a reuse gate"
# --reuse-check warn -> advisory (required:false) in repo_gates, never blocks
newrepo >/dev/null
$KURU init --reuse-check warn >/dev/null
python3 -c "import json,sys; r=json.load(open('.kuru/config.json'))['repo_gates'].get('reuse'); sys.exit(0 if r and r['cmd']=='dupehound check' and r['required'] is False else 1)" \
  && ok "init --reuse-check warn seeds an advisory (required:false) dupehound gate in repo_gates" || fail "warn reuse gate wrong"
# --reuse-check block -> required, in repo_gates (NOT the single-app top-level gates)
newrepo >/dev/null
$KURU init --reuse-check block >/dev/null
python3 -c "import json,sys; c=json.load(open('.kuru/config.json')); r=c['repo_gates'].get('reuse'); sys.exit(0 if r and r['required'] is True and 'reuse' not in c.get('gates',{}) else 1)" \
  && ok "init --reuse-check block seeds a required dupehound gate in repo_gates" || fail "block reuse gate wrong"
# repo_gates is legal alongside targets, and runs at the repo root for the slice
newrepo >/dev/null
$KURU init >/dev/null
printf '{"project":"mono","repo_gates":{"reuse":{"cmd":"true","required":true,"timeout":60}},"targets":{"api":{"dir":".","gates":{"build":{"cmd":"true","required":true,"timeout":60}}}}}\n' > .kuru/config.json
expect_ok "doctor accepts repo_gates alongside targets" $KURU doctor
$KURU new-slice "x" --target api >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1
python3 -c "import json,sys; g=json.load(open('.kuru/slices/SL-0001/gate-results.json'))['gates']; n={x['name']:x.get('scope') for x in g}; sys.exit(0 if n.get('reuse')=='repo' and n.get('build')=='api' else 1)" \
  && ok "gate runs repo_gates (scope repo) + target gates (scope api)" || fail "repo_gates not merged into the gate run"
# a repo_gate name colliding with a target gate name is rejected by doctor
printf '{"project":"mono","repo_gates":{"build":{"cmd":"true"}},"targets":{"api":{"dir":".","gates":{"build":{"cmd":"true","required":true,"timeout":60}}}}}\n' > .kuru/config.json
expect_fail "doctor flags a repo_gate name colliding with a target gate" "rename one" $KURU doctor
# a failing REQUIRED gate blocks verify; --waive lets it through with a recorded reason
newrepo >/dev/null
$KURU init >/dev/null
printf '{"project":"t","gates":{"reuse":{"cmd":"false","required":true,"timeout":60}}}\n' > .kuru/config.json
$KURU new-slice "x" >/dev/null
$KURU set-status SL-0001 ready >/dev/null; $KURU set-status SL-0001 in_progress >/dev/null
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1
expect_fail "failing required gate blocks verify" "FAILED" $KURU set-status SL-0001 verified --by verifier
expect_ok "gate --waive on a failing required gate exits 0" $KURU gate SL-0001 --waive 'reuse=false positive: shared switch shape'
python3 -c "import json,sys; r=json.load(open('.kuru/slices/SL-0001/gate-results.json')); g=r['gates'][0]; sys.exit(0 if r['passed'] and g['waived'] and 'false positive' in (g['waive_reason'] or '') else 1)" \
  && ok "--waive records the reason and flips overall to pass" || fail "waiver not recorded / overall not passed"
expect_ok "verified unlocks after a waived gate" $KURU set-status SL-0001 verified --by verifier
# the waiver is per-run, not persisted: re-running gate without --waive fails verify again
$KURU set-status SL-0001 rejected --by verifier >/dev/null
$KURU set-status SL-0001 in_progress >/dev/null; sleep 1
$KURU set-status SL-0001 built --by builder >/dev/null; $KURU set-status SL-0001 verifying --by verifier >/dev/null
$KURU gate SL-0001 >/dev/null 2>&1
expect_fail "re-running gate without --waive fails again (waiver not persisted)" "FAILED" $KURU set-status SL-0001 verified --by verifier

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

echo "== doctor: a not-yet-created target dir warns, doesn't fail =="
newrepo >/dev/null
$KURU init >/dev/null
# a target whose dir no slice has created yet (a `dir` is only honored inside `targets`)
printf '{"project":"t","targets":{"web":{"dir":"app/web","gates":{"unit":{"cmd":"true","required":true,"timeout":60}}}}}\n' > .kuru/config.json
out="$($KURU doctor 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "doctor still exits 0 with a missing target dir" || fail "doctor failed on a missing dir (rc=$rc): $out"
case "$out" in *"does not exist yet"*) ok "doctor warns about the missing dir" ;; *) fail "no warning emitted: $out" ;; esac
# but a real problem (a target with no gates) is still a hard failure
printf '{"project":"t","targets":{"web":{"dir":".","gates":{}}}}\n' > .kuru/config.json
expect_fail "doctor still hard-fails on no gates" "no gates configured" $KURU doctor

echo "== env: resolved per-target environment feeds build/verify =="
newrepo >/dev/null
$KURU init >/dev/null
mkdir -p .kuru/profiles
printf '{"stack":"gradle","environment":{"deploy_env":"Kubernetes","verification_access":"exec into the pod; mongo in-cluster only"},"conventions":{"x":1}}\n' > .kuru/profiles/api-env.json
printf '{"project":"t","targets":{"api":{"dir":".","profile":"api-env","gates":{"build":{"cmd":"true","required":true,"timeout":60}}}}}\n' > .kuru/config.json
$KURU new-slice "x" --target api >/dev/null
expect_ok "doctor OK when a target points at a profile that has an environment" $KURU doctor
$KURU env SL-0001 --json > /tmp/kuru-env-$$.json
python3 -c "import json,sys; e=json.load(open('/tmp/kuru-env-$$.json')); sys.exit(0 if e['profile']=='api-env' and e['target']=='api' and 'in-cluster' in e['environment']['verification_access'] else 1)" \
  && ok "env --json resolves slice -> target -> profile environment" || fail "env did not resolve the resolved profile"
# a profile pointer to a MISSING file is a hard doctor failure
printf '{"project":"t","targets":{"api":{"dir":".","profile":"ghost","gates":{"build":{"cmd":"true","required":true,"timeout":60}}}}}\n' > .kuru/config.json
expect_fail "doctor hard-fails on a profile pointer to a missing file" "is missing" $KURU doctor
# a target with NO profile is a warning, not a failure
printf '{"project":"t","targets":{"api":{"dir":".","gates":{"build":{"cmd":"true","required":true,"timeout":60}}}}}\n' > .kuru/config.json
out="$($KURU doctor 2>&1)"; rc=$?
[ $rc -eq 0 ] && ok "doctor exits 0 with a profile-less target" || fail "doctor failed on a profile-less target (rc=$rc): $out"
case "$out" in *"has no \`profile\`"*) ok "doctor warns about the profile-less target" ;; *) fail "no profile warning emitted: $out" ;; esac
# single-app: a top-level `profile` pointer resolves too, and set-stack preserves a target's pointer
printf '{"project":"t","profile":"api-env","gates":{"build":{"cmd":"true","required":true,"timeout":60}}}\n' > .kuru/config.json
$KURU new-slice "y" >/dev/null
$KURU env SL-0002 --json > /tmp/kuru-env2-$$.json
python3 -c "import json,sys; e=json.load(open('/tmp/kuru-env2-$$.json')); sys.exit(0 if e['profile']=='api-env' else 1)" \
  && ok "single-app top-level profile pointer resolves via env" || fail "top-level profile pointer not resolved"
printf '{"project":"t","targets":{"api":{"dir":".","profile":"api-env","gates":{"build":{"cmd":"true"}}}}}\n' > .kuru/config.json
$KURU set-stack gradle --target api >/dev/null 2>&1
python3 -c "import json,sys; t=json.load(open('.kuru/config.json'))['targets']['api']; sys.exit(0 if t.get('profile')=='api-env' else 1)" \
  && ok "set-stack --target preserves an existing profile pointer" || fail "set-stack dropped the profile pointer"

echo "== next --all: the parallel actionable batch =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates
$KURU new-slice "A" >/dev/null            # SL-0001
$KURU new-slice "B" --depends-on SL-0001 >/dev/null   # SL-0002 (dep on A)
$KURU new-slice "C" >/dev/null            # SL-0003
$KURU set-status SL-0001 ready >/dev/null
$KURU set-status SL-0003 ready >/dev/null
$KURU set-status SL-0002 ready >/dev/null   # ready but dep SL-0001 not done -> waiting
allj="$($KURU next --all --json)"
echo "$allj" | python3 -c "import json,sys; d=json.load(sys.stdin); a={x['id'] for x in d['actionable']}; sys.exit(0 if a=={'SL-0001','SL-0003'} else 1)" \
  && ok "next --all lists both independent ready slices (not just the first)" || fail "actionable set wrong: $allj"
echo "$allj" | python3 -c "import json,sys; d=json.load(sys.stdin); w=d['waiting']; sys.exit(0 if any(x['id']=='SL-0002' and x['unmet']==['SL-0001'] for x in w) else 1)" \
  && ok "next --all reports a dep-blocked ready slice under waiting" || fail "waiting set wrong: $allj"
echo "$allj" | python3 -c "import json,sys; d=json.load(sys.stdin); w=d['waiting']; sys.exit(0 if any(x['id']=='SL-0002' and 'target' in x for x in w) else 1)" \
  && ok "next --all waiting entries include target field" || fail "waiting missing target: $allj"
# finish A; B should now become actionable, A leaves the actionable set
drive_to_verified >/dev/null 2>&1   # drives SL-0001 to verified
$KURU set-status SL-0001 done >/dev/null
allj="$($KURU next --all --json)"
echo "$allj" | python3 -c "import json,sys; d=json.load(sys.stdin); a={x['id'] for x in d['actionable']}; sys.exit(0 if 'SL-0002' in a and 'SL-0001' not in a else 1)" \
  && ok "next --all unlocks a dependent once its dep is done" || fail "dependent not unlocked: $allj"

echo "== kuru commit: deferred batch commit + runs/ not swept =="
newrepo >/dev/null
$KURU init >/dev/null
echo "deferred" > note.txt
$KURU commit -m "kuru: test deferred commit" >/dev/null
git log -1 --oneline | grep -q "deferred commit" \
  && ok "kuru commit creates a commit" || fail "kuru commit did not commit"
mkdir -p .kuru/runs/r_test && echo secret > .kuru/runs/r_test/events.ndjson
echo dirty2 > note2.txt
$KURU commit -m "kuru: test runs ignored" >/dev/null
git ls-tree -r HEAD --name-only | grep -q '^\.kuru/runs/' \
  && fail "kuru commit swept .kuru/runs/ into history" \
  || ok "kuru commit does not include .kuru/runs/"

echo "== ledger lock: concurrent set-status writes all survive =="
newrepo >/dev/null
$KURU init >/dev/null; trivial_gates
for i in 1 2 3 4 5 6; do $KURU new-slice "S$i" >/dev/null; done
if python3 - <<PY
import subprocess, sys, json
K = "$ROOT/scripts/kuru.py"
ids = [f"SL-{n:04d}" for n in range(1,7)]
procs = [subprocess.Popen([sys.executable, K, "set-status", i, "ready"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in ids]
for p in procs: p.wait()
d = json.load(open(".kuru/ledger.json"))
ready = sum(1 for s in d["slices"] if s["status"]=="ready")
sys.exit(0 if ready==6 and len(d["slices"])==6 else 1)
PY
then ok "6 concurrent set-status writes all landed (lock held)"; else fail "ledger race: a concurrent write was lost"; fi

echo "== loop-workflow: reference per-slice-pipeline logic (mock agents) =="
# The /kuru:loop-workflow reference script (a JS template embedded in
# skills/loop-workflow/SKILL.md) is run by the Claude Code Workflow runtime, not by
# kuru.py — so the engine selftest can't reach it directly. Here we EXTRACT that script,
# wrap it, and drive it against mock agent()/parallel() to assert the per-slice pipeline
# scheduler: clean flow, dependency ordering, retry-under-cap, cap exhaustion, scoped-mode
# dead deps, blocked propagation, pre-existing built/verified states, and the target-keyed
# concurrency rule (same target serialized, different targets parallel).
# Skipped (not failed) where `node` is unavailable — the engine stays stdlib-Python-only.
if command -v node >/dev/null 2>&1; then
  wf="$(mktemp -d)/wf.mjs"
  python3 - "$ROOT" "$wf" <<'PY'
import re, sys
root, out = sys.argv[1], sys.argv[2]
src = open(root + '/skills/loop-workflow/SKILL.md').read()
body = re.search(r'```javascript\n(.*?)\n```', src, re.S).group(1)
body = re.sub(r'export const meta = \{.*?\n\}\n', '', body, count=1, flags=re.S)
wrapped = "async function run(args, agent, parallel){\n" + body + "\n}\n"
harness = r'''
const parallel = async (thunks) =>
  Promise.all(thunks.map((t) => Promise.resolve().then(t).catch(() => null)))
function makeAgent(scn) {
  const calls = [], vcount = {}, ccount = {}, bcount = {}, rcount = {}
  const agent = async (prompt) => {
    if (prompt.includes('/kuru:status')) return scn.plan
    // Pre-build contract critic (advisory): returns a verdict, never a status. A slice with
    // contractFlagged:n is flagged n times then OK (models n repairs converging);
    // contractAlwaysFlagged stays flagged (models a contract that can't be made satisfiable).
    if (prompt.includes('/kuru:check-contract')) {
      const id = prompt.match(/SL-[\w-]+/)[0]
      calls.push('check:' + id)
      const sc = (scn.scripts && scn.scripts[id]) || {}
      ccount[id] = (ccount[id] || 0) + 1
      const flagged = sc.contractAlwaysFlagged || (sc.contractFlagged && ccount[id] <= sc.contractFlagged)
      return { id, verdict: flagged ? 'flagged' : 'ok' }
    }
    // Contract repair: planner rewrites the flagged contract and re-freezes -> ready.
    if (prompt.includes('rewrite contract.yml')) {
      const id = prompt.match(/SL-[\w-]+/)[0]
      calls.push('repair:' + id)
      return { id, status: 'ready' }
    }
    const m = prompt.match(/\/kuru:(build|verify|review|ship)\s+(SL-[\w-]+)/)
    const cmd = m[1], id = m[2]
    calls.push(cmd + ':' + id)
    const sc = (scn.scripts && scn.scripts[id]) || {}
    if (cmd === 'build') {
      bcount[id] = (bcount[id] || 0) + 1
      // buildBlocked: every build blocks (models an unbuildable slice — retried then capped).
      // buildBlockTimes:n: the first n builds block, then it builds (a fresh-builder retry
      // recovers within the try budget).
      if (sc.buildBlocked) return { id, status: 'blocked' }
      if (sc.buildBlockTimes && bcount[id] <= sc.buildBlockTimes) return { id, status: 'blocked' }
      return { id, status: 'built' }
    }
    if (cmd === 'verify') {
      vcount[id] = (vcount[id] || 0) + 1
      if (sc.verifyBlocked) return { id, status: 'blocked' }
      if (sc.alwaysReject) return { id, status: 'rejected' }
      if (sc.rejectTimes && vcount[id] <= sc.rejectTimes) return { id, status: 'rejected' }
      return { id, status: 'verified' }
    }
    if (cmd === 'review') {
      rcount[id] = (rcount[id] || 0) + 1
      // reviewNoVerdict: the reviewer never recorded a verdict -> slice stays `verified`
      // (models the stuck/no-progress guard). alwaysReviewReject / reviewRejectTimes:n mirror
      // verify's reject knobs: a review rejection routes back to build as the next try.
      if (sc.reviewNoVerdict) return { id, status: 'verified' }
      if (sc.alwaysReviewReject) return { id, status: 'rejected' }
      if (sc.reviewRejectTimes && rcount[id] <= sc.reviewRejectTimes) return { id, status: 'rejected' }
      return { id, status: 'reviewed' }
    }
    // shipRefused models the engine refusing a ship (slice not actually verified/reviewed): the
    // ledger status is unchanged (modeled as `verified` — the ship guard treats verified OR
    // reviewed as no-progress). The pipeline must detect no-progress and stop after ONE attempt —
    // never re-spin (this is the regression guard for the old 10x ship loop).
    if (cmd === 'ship') return { id, status: sc.shipRefused ? 'verified' : 'done' }
  }
  return { agent, calls }
}
let fails = 0
const eq = (name, got, want) => {
  if (JSON.stringify(got) === JSON.stringify(want)) console.log('  ok: pipeline ' + name)
  else { fails++; console.error('  FAIL: pipeline ' + name +
    '\n        got ' + JSON.stringify(got) + '\n        want ' + JSON.stringify(want)) }
}
const S = (a) => [...a].sort()
const plan = (slices, extra = {}) => ({ slices, doneIds: [], blockedIds: [], draftIds: [], ...extra })
;(async () => {
  { // A: three independent slices flow clean -> all ship in round 1
    const { agent } = makeAgent({ plan: plan([
      { id: 'SL-01', status: 'ready', dependsOn: [] },
      { id: 'SL-02', status: 'ready', dependsOn: [] },
      { id: 'SL-03', status: 'ready', dependsOn: [] }]) })
    const o = await run({}, agent, parallel)
    eq('A: independent slices all ship', S(o.shipped), ['SL-01','SL-02','SL-03'])
    eq('A: nothing stuck/capped', [o.capped.length, o.stuck.length], [0,0])
  }
  { // B: dependency chain B->A; A must build+ship before B builds
    const { agent, calls } = makeAgent({ plan: plan([
      { id: 'SL-A', status: 'ready', dependsOn: [] },
      { id: 'SL-B', status: 'ready', dependsOn: ['SL-A'] }]) })
    const o = await run({}, agent, parallel)
    eq('B: chain both ship', S(o.shipped), ['SL-A','SL-B'])
    eq('B: A ships before B builds (dependency order)',
       calls.indexOf('ship:SL-A') < calls.indexOf('build:SL-B'), true)
  }
  { // C: one verify-reject under cap(2) -> retries and ships; build runs twice
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'ready', dependsOn: [] }]),
      scripts: { 'SL-A': { rejectTimes: 1 } } })
    const o = await run({ maxTries: 2 }, agent, parallel)
    eq('C: ships after a retry', o.shipped, ['SL-A'])
    eq('C: rebuilt once (build ran twice)', calls.filter((c) => c === 'build:SL-A').length, 2)
  }
  { // D: verify always rejects -> capped, never ships
    const { agent } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'ready', dependsOn: [] }]),
      scripts: { 'SL-A': { alwaysReject: true } } })
    const o = await run({ maxTries: 2 }, agent, parallel)
    eq('D: exhausted retries -> capped', o.capped, ['SL-A'])
    eq('D: capped slice did not ship', o.shipped, [])
  }
  { // E: scoped to [B] only, B depends on out-of-scope A -> B stuck
    const { agent } = makeAgent({ plan: plan([
      { id: 'SL-A', status: 'ready', dependsOn: [] },
      { id: 'SL-B', status: 'ready', dependsOn: ['SL-A'] }]) })
    const o = await run({ slices: ['SL-B'] }, agent, parallel)
    eq('E: scoped slice with dead dep is stuck',
       o.stuck, [{ id: 'SL-B', reason: 'a dependency cannot ship in this run' }])
    eq('E: nothing shipped', o.shipped, [])
  }
  { // F: a build that keeps blocking is RETRIED up to maxTries (a try = one build->verify
    //    cycle, counted at the build), then capped — NOT stopped after the first block. Its
    //    dependent B then can't ship and is stuck.
    const { agent, calls } = makeAgent({ plan: plan([
      { id: 'SL-A', status: 'ready', dependsOn: [] },
      { id: 'SL-B', status: 'ready', dependsOn: ['SL-A'] }]),
      scripts: { 'SL-A': { buildBlocked: true } } })
    const o = await run({ maxTries: 2 }, agent, parallel)
    eq('F: an always-blocking build is capped (retried, not stuck)', o.capped, ['SL-A'])
    eq('F: the blocked build was retried up to maxTries (build ran twice)',
       calls.filter((c) => c === 'build:SL-A').length, 2)
    eq('F: dependent of a capped slice is stuck',
       o.stuck.find((x) => x.id === 'SL-B').reason, 'a dependency cannot ship in this run')
  }
  { // F2: a build that blocks once then succeeds still ships — a fresh-builder retry recovers
    //    within the try budget (the fix: build failures consume a try and retry, not a hard stop).
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'ready', dependsOn: [] }]),
      scripts: { 'SL-A': { buildBlockTimes: 1 } } })
    const o = await run({ maxTries: 3 }, agent, parallel)
    eq('F2: build-block-then-recover ships', o.shipped, ['SL-A'])
    eq('F2: built twice (one blocked try + one good try)',
       calls.filter((c) => c === 'build:SL-A').length, 2)
  }
  { // G: pre-existing states - 'built' enters at verify, 'verified' enters at review (review on)
    const { agent, calls } = makeAgent({ plan: plan([
      { id: 'SL-A', status: 'built', dependsOn: [] },
      { id: 'SL-B', status: 'verified', dependsOn: [] }]) })
    const o = await run({}, agent, parallel)
    eq('G: pre-built/pre-verified both ship', S(o.shipped), ['SL-A','SL-B'])
    eq('G: no spurious re-build of a built/verified slice',
       calls.some((c) => c === 'build:SL-A' || c === 'build:SL-B'), false)
    eq('G: pre-verified slice goes through review before ship (review on)',
       calls.indexOf('review:SL-B') < calls.indexOf('ship:SL-B'), true)
  }
  { // H: two slices on the SAME gate target serialize -> A's whole pipeline before B starts
    const { agent, calls } = makeAgent({ plan: plan([
      { id: 'SL-A', status: 'ready', dependsOn: [], target: 'web' },
      { id: 'SL-B', status: 'ready', dependsOn: [], target: 'web' }]) })
    const o = await run({}, agent, parallel)
    eq('H: same-target slices both ship', S(o.shipped), ['SL-A','SL-B'])
    eq('H: same-target serialized (A ships before B even starts building)',
       calls.indexOf('ship:SL-A') < calls.indexOf('build:SL-B'), true)
  }
  { // I: two slices on DIFFERENT targets run in parallel -> their pipelines interleave
    const { agent, calls } = makeAgent({ plan: plan([
      { id: 'SL-A', status: 'ready', dependsOn: [], target: 'web' },
      { id: 'SL-B', status: 'ready', dependsOn: [], target: 'api' }]) })
    const o = await run({}, agent, parallel)
    eq('I: different-target slices both ship', S(o.shipped), ['SL-A','SL-B'])
    eq('I: different-target parallel (B builds before A ships — interleaved)',
       calls.indexOf('build:SL-B') < calls.indexOf('ship:SL-A'), true)
  }
  { // J: a ship the engine refuses (slice not actually verified) must NOT loop.
    //    Regression guard for the old bug where ship re-ran ~10x on an unshippable slice.
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'verified', dependsOn: [] }]),
      scripts: { 'SL-A': { shipRefused: true } } })
    const o = await run({}, agent, parallel)
    eq('J: refused ship attempted exactly once (no loop)',
       calls.filter((c) => c === 'ship:SL-A').length, 1)
    eq('J: unshippable slice did not ship', o.shipped, [])
    eq('J: unshippable slice reported stuck (not spun)', o.stuck.map((x) => x.id), ['SL-A'])
  }
  { // K: clean contract check fires BEFORE the first build, exactly once, then builds.
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'ready', dependsOn: [] }]) })
    const o = await run({}, agent, parallel)
    eq('K: clean contract ships', o.shipped, ['SL-A'])
    eq('K: check ran once, before build', [calls.filter((c) => c === 'check:SL-A').length,
       calls.indexOf('check:SL-A') < calls.indexOf('build:SL-A')], [1, true])
  }
  { // L: a flagged contract is repaired then builds — check, repair, check(ok), build, ship.
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'ready', dependsOn: [] }]),
      scripts: { 'SL-A': { contractFlagged: 1 } } })
    const o = await run({ maxTries: 2 }, agent, parallel)
    eq('L: flagged-then-fixed contract ships', o.shipped, ['SL-A'])
    eq('L: repaired once, never built before the contract was OK', [
       calls.filter((c) => c === 'repair:SL-A').length,
       calls.indexOf('build:SL-A') > calls.lastIndexOf('check:SL-A')], [1, true])
  }
  { // M: a contract that never converges is capped — never built, never shipped.
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'ready', dependsOn: [] }]),
      scripts: { 'SL-A': { contractAlwaysFlagged: true } } })
    const o = await run({ maxTries: 2 }, agent, parallel)
    eq('M: un-satisfiable contract is capped', o.capped, ['SL-A'])
    eq('M: capped contract never reached build', calls.some((c) => c === 'build:SL-A'), false)
  }
  { // N: review ON (default) — a review rejection loops back to build (the next try) and ships within cap(2).
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'ready', dependsOn: [] }]),
      scripts: { 'SL-A': { reviewRejectTimes: 1 } } })
    const o = await run({ maxTries: 2 }, agent, parallel)
    eq('N: a review rejection retries then ships', o.shipped, ['SL-A'])
    eq('N: review rejection rebuilds — build+verify+review is one try (build & review each ran twice)',
       [calls.filter((c) => c === 'build:SL-A').length, calls.filter((c) => c === 'review:SL-A').length], [2, 2])
  }
  { // O: a review that always rejects burns the whole try budget -> capped, never ships.
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'ready', dependsOn: [] }]),
      scripts: { 'SL-A': { alwaysReviewReject: true } } })
    const o = await run({ maxTries: 2 }, agent, parallel)
    eq('O: always-review-reject is capped', o.capped, ['SL-A'])
    eq('O: capped-by-review did not ship', o.shipped, [])
    eq('O: build ran maxTries times (each failing try includes its review)',
       calls.filter((c) => c === 'build:SL-A').length, 2)
  }
  { // P: a review that records NO verdict leaves the slice `verified` -> stuck (no progress), never loops.
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'verified', dependsOn: [] }]),
      scripts: { 'SL-A': { reviewNoVerdict: true } } })
    const o = await run({}, agent, parallel)
    eq('P: no-verdict review is stuck', o.stuck.map((x) => x.id), ['SL-A'])
    eq('P: no-verdict review did not ship and did not spin (review attempted once)',
       [o.shipped, calls.filter((c) => c === 'review:SL-A').length], [[], 1])
  }
  { // Q: review OFF (args.review=false) — a verified slice ships straight to done, no review stage.
    const { agent, calls } = makeAgent({ plan: plan([{ id: 'SL-A', status: 'verified', dependsOn: [] }]) })
    const o = await run({ review: false }, agent, parallel)
    eq('Q: review off -> verified ships straight to done', o.shipped, ['SL-A'])
    eq('Q: review off -> no review stage ran', calls.some((c) => c === 'review:SL-A'), false)
  }
  process.exit(fails ? 1 : 0)
})()
'''
open(out, 'w').write(wrapped + harness)
PY
  if hout="$(node "$wf" 2>&1)"; then
    echo "$hout"
    pass=$((pass + $(printf '%s\n' "$hout" | grep -c '^  ok:' || true)))
  else
    echo "$hout" >&2
    fail "loop-workflow reference per-slice-pipeline logic (see FAIL lines above)"
  fi
else
  echo "  -- skipped: node not found (reference script is JS run by the Workflow runtime)"
fi

echo
echo "ALL PASS ($pass checks)"
