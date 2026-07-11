---
name: loop-workflow
description: Use when running /kuru:loop-workflow, or when asked to drive ready Kurukuru slices through build→verify→review→ship. Prefer the portable board runner (python3 -m board); Claude Code dynamic Workflow is an optional Claude-only path. Covers target-mutex pipelines, engine-aligned routing, deferred commit, and guardrails.
---

# Driving the board (multi-slice pipelines)

`/kuru:loop-workflow` clears the board the same way `/kuru:loop` does — the mechanical
`build → verify → review → ship` cycle (review runs when the workspace has it on — the `kuru
init` default; a review rejection rebuilds like a verify rejection) — but it drives each slice
as its **own pipeline** and runs slices **on different gate targets in parallel** (same-target
slices serialize).

## Prefer the board runner

**Default recommendation:** shell out to the portable Python control plane when available:

```bash
PYTHONPATH=/path/to/kurukuru python3 -m board run --backend claude -y
# also: --backend grok | mock | cmd --backend-cmd '…'
# plan only: python3 -m board plan
# history:  python3 -m board status
# interactive board: scripts/board-tui.sh --repo . --backend claude
```

Policy lives in `board/` + `scripts/kuru.py` (engine). The hierarchical UI is the Ratatui
binary (`scripts/board-tui.sh` / `kuru-board-tui`), which spawns `board run --ui plain` and
tails `.kuru/runs/*/events.ndjson`. See `tui/README.md` and `impl/BOARD_RUNNER_PLAN.md`.

The **Claude Code dynamic Workflow** path below remains valid as a **Claude-only / legacy**
option (JS `Workflow` tool + `agent()`). Prefer board when both are available — board is
agent-agnostic and engine-aligned on routing edge cases.

## Claude-only path: dynamic workflow

When using Claude Code’s `Workflow` tool instead of board, the same policy is implemented as a
JS orchestration script rather than by orchestrating subagents inside this conversation.

## Why a dynamic workflow (and not in-session orchestration)

The win is **clean contexts between phases**. In a long in-session loop, every builder and
verifier result piles into one context window; after a dozen slices the orchestrator is
reasoning in a saturated context and quality decays. A dynamic workflow runs each `agent()`
in its **own fresh context**, out of process — the script keeps only small structured results
in JS variables, never the agents' transcripts. So a 30-slice board costs the orchestrator
almost no context, and each build/verify/ship starts from zero every time. This is why the
workflow **replaces `runner.py`**: it gives the per-step context isolation `runner.py` reached
for, but natively and resumably (within the session).

## The shape of the command

`/kuru:loop-workflow` does **not** drive the slices itself. It:

1. Runs the **preconditions** and reads the board (`kuru next --all`), showing a short plan.
2. **Authors a JavaScript workflow script** tailored to the current board (see below) and
   **launches it directly with the `Workflow` tool**. Don't print the script and ask the user to
   confirm first — the `Workflow` tool's own approval card already offers *Yes, run it / View raw
   script / No*, so a manual prompt just duplicates it. The runtime then executes it in the
   background; `/workflows` shows live progress.
3. **After the workflow returns, commits once** — see "The deferred commit" below.

Authoring + launching + the final commit all happen from this (main) session. The script itself
only holds the loop.

## Two hard constraints that shape the script

1. **The script has no shell or filesystem access.** It only *coordinates* agents. Every
   `kuru.py` interaction happens **inside an `agent()`**, never in the script body — including
   the final commit, which the *launching session* does after the run, not the script.
   - **The script must never reference `process`, `process.env`, `KURU_PY`, `CLAUDE_PLUGIN_ROOT`,
     or any path to `kuru.py`.** There is no `process` global in the workflow runtime — touching
     it throws `process is not available` and kills the run. The script does **not** locate
     `kuru.py`; the **agents** run `/kuru:build|verify|ship|status`, and *those commands* resolve
     the path themselves. If you find yourself trying to compute a kuru path in script body, stop
     — that work belongs inside an `agent()` prompt as a `/kuru:*` command. (Same class of rule as
     `Date.now()`/`Math.random()` being banned: the script body is a thin, side-effect-free
     coordinator.)
2. **Workflow agents drive kuru through the `/kuru:*` commands, not `kuru.py` directly.** So:
   - read the board → an agent runs **`/kuru:status`** and returns a structured snapshot;
   - build → an agent runs **`/kuru:build <id>`**;
   - verify → an agent runs **`/kuru:verify <id>`**;
   - review *(when review is on)* → an agent runs **`/kuru:review <id>`**;
   - ship → an agent runs **`/kuru:ship <id> --no-commit`** (see "The deferred commit").
   This keeps the rule that **only `kuru.py` mutates the ledger** intact — the commands wrap it
   — without the script needing a path to `kuru.py`.
3. **No agents use `isolation: 'worktree'`.** All agents run in the same project tree so they
   read and write the **same** `ledger.json`. Worktrees break this: each worktree copies the
   ledger, so agent writes don't synchronize. Always omit `isolation` from agent options
   (defaults to shared tree). The launcher commits once afterward on the quiescent main tree.

   > If your runtime *can* run `kuru.py` from an agent, the planning agent can use
   > `kuru next --all --json` for an authoritative machine snapshot (it includes each slice's
   > `status`, `depends_on`, **and `target`**) instead of parsing `/kuru:status`. Prefer that
   > when available; fall back to `/kuru:status` (whose `ls` table shows a target column)
   > otherwise.

## The deferred commit (why ship can run immediately)

`set-status done` normally auto-commits the whole working tree. On a shared tree with many
slices moving at once, a mid-run commit would sweep another slice's in-flight edits into the
wrong commit. So the workflow **decouples the `done` transition from the commit**: every ship
runs `/kuru:ship <id> --no-commit`, which flips the slice to `done` in the ledger (lock-safe, no
tree contention) but makes **no git commit**. Then, **after the whole run**, the launching
session makes **one commit** for everything shipped, on a now-quiescent tree.

The trade you are accepting (deliberately): **one commit per run, not per slice** — you lose
per-slice revert granularity, and during the run `done`-in-the-ledger does not yet mean
committed-in-git. The final commit must therefore run on **every** exit path (success,
retry-cap, blocked), so the ledger and git reconverge no matter how the run ends.

## The loop is per-slice pipelines, serialized per gate target

Each slice is driven through its **own** `build → verify → review → ship` pipeline (review runs
when the workspace has it on) — a fresh `agent()` per stage. The slices do **not** march through
global phases. What decides parallelism is the
**gate target** (the `config.json` target a slice builds under; `"default"` for a single-target
repo):

- **Same target → serialized.** A target runs **at most one** slice's pipeline at a time. This
  is the no-worktrees lesson made into a rule: the slices share **one working tree** (worktrees
  can't be used — they'd fork the in-tree `ledger.json`), so two same-target slices running at
  once would (a) clobber each other's edits during `build`, and (b) let one slice's in-flight
  build contaminate the other's `verify`, which re-runs the gates and drives the running app
  against that target's tree. Serializing the whole pipeline — not just barriering one phase —
  removes both: while slice A is anywhere in build→verify→review→ship on target T, no other slice
  on T starts.
- **Different target → parallel.** Targets are disjoint subtrees with their own `dir` and gates,
  so their pipelines can't contaminate each other. They run concurrently, bounded only by the
  runtime's agent-concurrency cap.
- **Dependency-ordered.** A slice's pipeline starts only once **all** its `depends_on` slices are
  `done` — deps may live on any target. So a dependent begins the instant its last dependency
  ships, not a phase later. (The DAG must be acyclic — a kuru invariant.)

This is strictly better than the old phase-barriered rounds it replaces: serializing same-target
slices end-to-end removes the contamination the barrier existed to prevent, **and** an
independent-target slice can ship while another is still building — no "dependent waits a whole
extra round" tax.

Concretely the script runs a small **target-mutex scheduler**: repeatedly start every slice that
is live, has its deps `done`, and whose target is currently free; then await the next pipeline to
finish (which frees its target and may unlock dependents) and repeat, until nothing is running and
nothing new can start.

**A try is one `build → verify → review` cycle, and the budget bounds cycles — not just verify
rejections.** `maxTries` (default 2) is how many build→verify(→review) cycles a slice gets in this
run. A try is counted at the **build** that starts it, so **any** failed cycle consumes one and
loops the slice back to a fresh build: a verify that `rejected`s, a **review** that `rejected`s
(when review is on), a build that goes `blocked` (the builder gave up / gates stayed red), or an
agent that threw. Mid-run `blocked` is normalized (`blocked` → `in_progress`) then rebuilt.

**Engine-aligned routing (board + `kuru.py` win over older script notes):**

| ledger status | next action |
|---------------|-------------|
| `ready` / `in_progress` / `rejected` | **build** |
| `built` / **`verifying`** | **verify** (re-verify — do **not** rebuild; a stuck no-verdict verify is capped separately) |
| `verified` | **review** if policy on, else **ship** |
| `reviewed` | **ship** |

`STATUS_ACTION["verifying"] = "verify"` in `scripts/kuru.py`. **Do not** normalize
`verifying → rejected` and rebuild. Cap repeated no-verdict verifies (board: `max_no_verdict`).
The reference JS script below historically treated `verifying` as NEEDS_BUILD — that diverges from
the engine; **prefer board**, or when authoring Workflow JS follow the table above.

**Review is a stage of the same try** — a review rejection (`verified` → `rejected`) rebuilds like
a verify rejection and consumes the next try; it is NOT a separate budget. When review is **off**,
the cycle is just `build → verify` and a verified slice ships directly.

**Pre-build contract check (advisory).** Before a slice's **first build this run** (status
`ready`/`in_progress`, not `rejected`), its pipeline runs the **contract critic**
(`/kuru:check-contract <id>`) — catching a contract no build could satisfy (an AC nothing builds,
or one unverifiable in this env) before a build→verify loop is wasted. `CONTRACT OK` → build;
`CONTRACT FLAGGED` → a **repair** stage routes it back through the planner (`draft` → rewrite from
the flags → `ready`) and re-checks, capped by `MAX_TRIES` (a contract that won't converge is
`capped`, exactly like an exhausted try budget). The critic is advisory — it changes no status
and the planner does the repair; this is all **inside the slice's own pipeline**, so it serializes
under the same target mutex.

> **Throughput note:** parallelism scales with how many **distinct targets** are actionable —
> a single-project (one-target) board runs fully sequentially by design, which is correct (you
> can't safely parallelize one tree without worktrees). A polyglot/monorepo board runs one
> pipeline per app concurrently. `/workflows` shows each slice's pipeline as its own progress
> group, so a parallel run stays trackable.

## Guardrails (carried over — do not drop)

- **Builder ≠ verifier.** Each build and each verify is a separate `agent()` with its own
  context — structurally guaranteed. Never have one agent both build and verify a slice.
- **A phase agent reports the LEDGER status, not its own narration.** Every build/verify/ship
  agent must read `kuru show <id>` after acting and return *that* status. The classic failure: a
  verifier runs its integration scripts, they pass, it declares "verified" in prose — but never
  runs `set-status`, so the ledger still says `verifying`. If the agent then reports `verified`
  from its belief, the pipeline routes the slice to ship, the engine refuses (it isn't actually
  `verified`), and the slice goes nowhere. The fix is the read-back: the verify prompt makes
  recording the verdict the agent's load-bearing output and makes it report the status the ledger
  shows.
- **Route every slice by its *current* status** — build for
  `ready`/`in_progress`/`rejected`/`blocked`/`verifying` (start or retry a build→verify→review
  cycle), verify for `built`, **review for `verified`** (when review is on; else ship), ship for
  `reviewed`. Only a **ship the engine refused** (leaves the slice `verified`/`reviewed` after a
  ship), a **review that recorded no verdict** (leaves `verified`), or a **truly unexpected status**
  is `stuck` — a no-progress stop. A failed build, verify, or review is **not** `stuck`; it's a
  failed try that retries (see the cap below).
- **A try is a full `build → verify → review` cycle; cap tries, per run.** Count a try at the
  **build** that starts each cycle, so `maxTries` (default 2) bounds total build→verify(→review)
  cycles — not just verify rejections. Every failed cycle (verify `rejected`, **review `rejected`**,
  build `blocked`, `verifying` with no verdict, or an agent exception) loops the slice back to a
  fresh build and spends one try; once
  the budget is gone, **stop driving that slice** and report it `capped`. Do not auto-flip it to
  `blocked` — leaving it where it is and reporting it is enough; a re-run gets a fresh budget.
- **A `blocked` build is retried, but a slice blocked at start is left for a human.** During a
  run, a build that goes `blocked` is a failed try: the pipeline normalizes it (`blocked` →
  `in_progress`) and rebuilds with a fresh builder, up to `maxTries` — a genuinely unbuildable
  slice therefore burns its full budget, then `capped`, then surfaces. A slice that was **already
  `blocked` before the run** (a human's deliberate park) is *not* auto-retried: it's dropped from
  scheduling and reported as `blockedAtStart`. Either way, any slice a dependent needs that can't
  ship (`capped`/`stuck`/blocked-at-start/absent) stops that dependent, which is reported `stuck`;
  **independent slices still finish**, and everything left is surfaced for a human.
- **Only `kuru.py` mutates state** — and only via the `/kuru:*` commands the agents run. Never
  have an agent hand-edit `ledger.json` / `gate-results.json`. A refused transition is a real
  signal.
- **Dependencies must be acyclic** (a kuru invariant). A cycle would never become runnable and
  its slices are reported `stuck`. `doctor` and the slicing methodology keep the graph acyclic.

## Preconditions (the command checks these before authoring anything)

The judgment-heavy phases stay human. Refuse to author/launch unless **all** hold; otherwise
STOP and name the command to run:

1. `kuru doctor` reports healthy (a hard ✗ blocks; target-dir *warnings* don't).
2. `.kuru/charter.md` exists and is filled in → else `/kuru:charter`.
3. At least one spec under `.kuru/spec/` → else `/kuru:spec`.
4. **Whole-board mode:** no `draft` slices remain (`kuru ls --status draft` is empty) → else
   `/kuru:slice`. **Scoped mode** (`/kuru:loop-workflow SL-0002` or `SL-0001,SL-0002,SL-0011`):
   only the named slices and their deps must be contracted — run `kuru next --slice <id> --json`
   for each named id (a `slice` action → still draft, STOP; `blocked` → STOP; `waiting_on_deps`
   whose blocking dep is neither `done` nor itself named → STOP, tell the user to add it or ship
   it first).

## Reference script

Author a script in this shape, parameterized to the current board. Pass options through the
`Workflow` tool's `args` (e.g. `{ maxTries: 2, slices: ["SL-0001", "SL-0002"], review: true }`;
omit `slices` for the whole board). **Set `args.review` from the workspace policy** — read it
from `kuru next --all --json` (`.review`) during the preconditions and pass it through, so the
run honors this workspace's `kuru set-review` setting (default on). Keep the structure; adapt
prompts/labels to the slices you're driving.

```javascript
export const meta = {
  name: 'kuru-loop-workflow',
  description: 'Drive ready Kurukuru slices through PER-SLICE build -> verify -> review -> ship pipelines (review when the workspace has it on), dependency-ordered. Slices on the SAME gate target serialize (one shared tree, no worktrees); slices on DIFFERENT targets run in parallel. Ship defers the commit (the launcher commits once after the run).',
  phases: [
    { title: 'Plan', detail: 'read the board: each slice status, depends_on, and gate target' },
    { title: 'Pipelines', detail: 'one build->verify->review->ship pipeline per slice (review when on); same target serialized, different targets parallel' },
  ],
}

const MAX_TRIES = (args && args.maxTries) || 2   // build->verify->review tries per slice before capping
// Scope: args.slices is an array of ids — absent/empty = whole board. The target-keyed
// concurrency rule applies either way: named slices on different targets parallelize, named
// slices on the same target serialize. Case-insensitive.
const SCOPE = args && args.slices && args.slices.length
  ? new Set(args.slices.map((x) => String(x).toUpperCase()))
  : null

// Code review policy for THIS workspace (kuru's meta.review). ON (the `kuru init` default) ->
// a verified slice must pass /kuru:review before it can ship, and a review rejection loops it
// back to build (the next try). OFF -> a verified slice ships straight to done. The launcher
// reads the flag from `kuru next --all --json` (.review) and passes it as args.review; default
// ON if unspecified.
const REVIEW = args && args.review !== undefined ? !!args.review : true

// **CRITICAL**: Agents do NOT use isolation: 'worktree'. All agents run in the same tree to
// read/write the shared ledger.json. The launcher commits once after the run on the main tree.

// What the planning agent returns after reading /kuru:status (read ONCE, up front). The `target`
// is the concurrency key — a slice's config.json gate target, or "default" for a single-target
// repo (kuru's /kuru:status `ls` table shows a target column; next --all --json carries it).
const PLAN = {
  type: 'object', additionalProperties: false,
  required: ['slices', 'doneIds', 'blockedIds', 'draftIds'],
  properties: {
    slices: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['id', 'status', 'dependsOn', 'target'],
        properties: {
          id: { type: 'string' },
          status: { type: 'string' },                     // ready|in_progress|rejected|built|verifying|verified|reviewed
          dependsOn: { type: 'array', items: { type: 'string' } },
          target: { type: 'string' },                     // gate target; "default" if single-target
        },
      },
    },
    doneIds:    { type: 'array', items: { type: 'string' } }, // already done before this run
    blockedIds: { type: 'array', items: { type: 'string' } },
    draftIds:   { type: 'array', items: { type: 'string' } },
  },
}

// What each command-agent returns: the slice's status AFTER running its /kuru command, read
// BACK from `kuru show <id>` (never inferred from the agent's own narration).
const RESULT = {
  type: 'object', additionalProperties: false,
  required: ['id', 'status'],
  properties: { id: { type: 'string' }, status: { type: 'string' }, note: { type: 'string' } },
}

// What the pre-build contract critic returns (advisory — it changes no status): 'ok' if the
// frozen contract is satisfiable + verifiable in this env, else 'flagged' with the flags.
const CHECK = {
  type: 'object', additionalProperties: false,
  required: ['id', 'verdict'],
  properties: { id: { type: 'string' }, verdict: { type: 'string' }, note: { type: 'string' } }, // verdict: ok|flagged
}

// 1. Plan — one fresh agent reads the board + dependency graph + targets, once.
// NOTE: Do NOT add isolation: 'worktree' — this agent must read from the shared ledger.json.
const plan = await agent(
  `Run /kuru:status and read its output. Return every slice that is NOT done and NOT draft as
{ id, status, dependsOn, target } — status is the slice's current state (ready / in_progress /
rejected / built / verifying / verified / reviewed); dependsOn is its list of dependency slice
ids; target is the config.json gate target it builds under (the target column in the ls table,
or "default" if the repo is single-target). Also return doneIds (already-done slices),
blockedIds, and draftIds. Report exactly what the board shows; do NOT start any work.`,
  { label: 'plan', phase: 'Plan', schema: PLAN }
)

// Routing for the NON-build stages. Build states (ready/in_progress/rejected/blocked/verifying)
// are handled by NEEDS_BUILD in drivePipeline — a `verifying` (no verdict) is a failed try that
// rebuilds, not a re-verify — so they are intentionally absent here. With REVIEW on, a verified
// slice routes to `review` first (a rejection there rebuilds = next try); reviewed always ships.
const ACTION = {
  built: 'verify',
  verified: REVIEW ? 'review' : 'ship', reviewed: 'ship',
}
const done = new Set(plan.doneIds)
// Drive slices that aren't done/draft. A slice that was ALREADY `blocked` before this run is a
// human's deliberate park — leave it (reported as blockedAtStart), don't auto-retry it. Only a
// build that goes `blocked` DURING this run is retried (see drivePipeline). status 'blocked'
// here means blocked-at-start.
let slices = (SCOPE ? plan.slices.filter((s) => SCOPE.has(s.id)) : plan.slices)
  .filter((s) => s.status !== 'blocked')
const byId = Object.fromEntries(slices.map((s) => [s.id, s]))
const targetOf = (s) => (s.target || 'default')

// Scoped mode: surface any requested id that isn't an actionable, non-done slice, with WHY.
const requestedUnavailable = SCOPE
  ? [...SCOPE].filter((id) => !byId[id]).map((id) => ({
      id,
      why: done.has(id) ? 'already done'
         : plan.draftIds.includes(id) ? 'draft — needs /kuru:slice'
         : plan.blockedIds.includes(id) ? 'blocked'
         : 'unknown id',
    }))
  : []

// 2. Mutable per-slice run state (the script tracks status from each agent's reported result).
const status = {}            // id -> current status
const repairs = {}           // id -> per-run contract-repair tally
const checked = new Set()    // passed the pre-build contract check this run (CONTRACT OK)
const blocked = new Set()    // could not be made buildable to retry (reset itself failed)
const capped = new Set()     // exhausted the build->verify try budget OR the contract-repair budget
const stuck = new Set()      // a ship the engine refused, or a truly unexpected status
for (const s of slices) { status[s.id] = s.status; repairs[s.id] = 0 }

const isLive = (s) => !done.has(s.id) && !blocked.has(s.id) && !capped.has(s.id) && !stuck.has(s.id)
const depsDone = (s) => (s.dependsOn || []).every((d) => done.has(d))
// A dependency that can never ship in this run kills its dependents: blocked/capped/stuck, or —
// whole board (draft/blocked dep) or scoped (dep not named) — absent from `byId` and not done.
const depDead = (s) => (s.dependsOn || []).some(
  (d) => blocked.has(d) || capped.has(d) || stuck.has(d) || (!done.has(d) && !byId[d]))

const prompt = {
  check: (id) => `Run \`/kuru:check-contract ${id}\` — a fresh contract critic judges whether ${id}'s FROZEN contract is satisfiable (something builds each AC — this slice or an earlier done slice) and verifiable in this env. It is ADVISORY: it changes NO status and edits NO files but \`.kuru/slices/${id}/contract-review.md\`. After it returns, read that report and return { id, verdict: "ok" if it says CONTRACT OK else "flagged", note: the essence of the flags }.`,
  repair: (id) => `${id}'s contract was FLAGGED as un-satisfiable/un-verifiable. Run \`kuru set-status ${id} draft\`, then dispatch a fresh kuru-planner to rewrite contract.yml/slice.md from the flags in \`.kuru/slices/${id}/contract-review.md\` so every acceptance criterion is satisfiable and verifiable in this environment (do NOT drop scope to dodge a flag — fix the wording or the slice boundary). Then run \`kuru set-status ${id} ready\`. Finally run \`kuru show ${id}\` and report the status the LEDGER shows.`,
  build: (id) => `First run \`kuru show ${id}\`. If it shows \`blocked\` (a previous build this run gave up) run \`kuru set-status ${id} in_progress --by builder --note "retry after failed build"\`; if it shows \`verifying\` (a previous verify recorded no verdict) run \`kuru set-status ${id} rejected --by verifier --note "no verdict — retrying build->verify"\`; otherwise leave the status alone. Then run \`/kuru:build ${id}\` and let it finish (it dispatches a FRESH builder and runs the gates). Then run \`kuru show ${id}\` and report the status the LEDGER shows.`,
  verify: (id) => `Run \`/kuru:verify ${id}\` and let it finish (a fresh, INDEPENDENT verifier re-runs the gates and checks the contract). The verdict is only real once recorded: the verifier MUST end by running \`kuru set-status ${id} verified|rejected --by verifier\` — a "PASS" only in prose or in verification.md is NOT a verdict and leaves the slice in \`verifying\`. After it returns, run \`kuru show ${id}\` and report the status the LEDGER actually shows (verified / rejected / verifying / blocked) — never a status inferred from narration.`,
  review: (id) => `Run \`/kuru:review ${id}\` — a fresh reviewer code-reviews ${id}'s diff (the quality axis; verify already settled the contract). The verdict is only real once recorded: the reviewer MUST end by running \`kuru set-status ${id} reviewed --by reviewer\` if it's clean, or \`kuru set-status ${id} rejected --by reviewer --note "<what to fix>"\` if it finds real problems (a rejection routes the slice back to build — the next try). After it returns, run \`kuru show ${id}\` and report the status the LEDGER shows (reviewed / rejected / verified) — never inferred from narration.`,
  ship: (id) => `Run \`/kuru:ship ${id} --no-commit\` — flip the slice to done WITHOUT committing (the launcher commits after the run). Then run \`kuru show ${id}\` and report the status the LEDGER shows.`,
}

// Statuses that mean "this slice needs a (re)build" — the start of a build->verify TRY. `ready`
// is the first try; `in_progress`/`rejected`/`blocked`/`verifying` are a failed prior try looping
// back (the build prompt self-normalizes blocked->in_progress and verifying->rejected first).
const NEEDS_BUILD = new Set(['ready', 'in_progress', 'rejected', 'blocked', 'verifying'])

// Drive ONE slice through its build -> verify -> review -> ship pipeline (review only when REVIEW
// is on; else build -> verify -> ship). A TRY is one full build->verify->review cycle; it is
// counted at the build that starts it, so `MAX_TRIES` bounds total cycles. ANY failed cycle — a
// build that goes `blocked`, a verify that `rejected`s, a REVIEW that `rejected`s, a verify that
// records no verdict (`verifying`), or an agent that throws — loops back to a fresh build and
// consumes one try; the slice is `capped` only once the try budget is spent. Every agent shares
// the one tree (NO worktrees). The scheduler holds this slice's target mutex for the whole
// pipeline, so no same-target build is in flight to contaminate this verify.
const drivePipeline = async (s) => {
  let tries = 0
  while (true) {
    const st = status[s.id]
    if (NEEDS_BUILD.has(st)) {
      // Pre-build contract check (advisory) — before the FIRST build of this slice this run, and
      // only when entering clean (ready/in_progress, not a retry). A FLAGGED contract is repaired
      // by the planner (draft->ready) and re-checked, capped by MAX_TRIES.
      if ((st === 'ready' || st === 'in_progress') && !checked.has(s.id)) {
        let flagged = false
        while (true) {
          let cr
          try { cr = await agent(prompt.check(s.id), { label: `check:${s.id}`, phase: s.id, schema: CHECK }) }
          catch { capped.add(s.id); return }
          if (!cr || cr.verdict === 'ok') { checked.add(s.id); break }   // safe to build
          if (++repairs[s.id] > MAX_TRIES) { capped.add(s.id); return }   // contract won't converge -> stop
          let rr
          try { rr = await agent(prompt.repair(s.id), { label: `repair:${s.id}`, phase: s.id, schema: RESULT }) }
          catch { capped.add(s.id); return }
          const rs = rr && rr.status
          if (!rs || rs === 'blocked') { blocked.add(s.id); return }      // couldn't reach a buildable state
          status[s.id] = rs; flagged = true                              // expect 'ready' -> re-check
        }
        if (flagged) continue                                            // re-evaluate after repair
      }
      // Start a build->verify try (the build prompt normalizes blocked/verifying first). Spend
      // one try; if the budget is already gone, stop and surface the slice.
      if (tries >= MAX_TRIES) { capped.add(s.id); return }
      tries++
      let r
      try { r = await agent(prompt.build(s.id), { label: `build:${s.id}`, phase: s.id, schema: RESULT }) }
      catch { status[s.id] = 'blocked'; continue }                       // failed try -> retry (capped by tries)
      const ns = r && r.status
      status[s.id] = ns || 'blocked'                                     // no read-back -> failed try -> retry
      if (ns === 'done') { done.add(s.id); return }                      // (not expected from build, but safe)
      continue                                                          // built -> verify next; else retry
    }
    // Not a build state -> verify or ship.
    const action = ACTION[st]
    if (!action) { stuck.add(s.id); return }                            // truly unexpected status -> stop
    let r
    try { r = await agent(prompt[action](s.id), { label: `${action}:${s.id}`, phase: s.id, schema: RESULT }) }
    catch { status[s.id] = 'blocked'; continue }                        // agent threw -> failed try -> rebuild
    const ns = r && r.status
    status[s.id] = ns || 'blocked'                                      // no read-back -> failed try -> rebuild
    if (ns === 'done') { done.add(s.id); return }
    // A ship the engine refused leaves the slice verified/reviewed with no progress -> stop.
    if (action === 'ship' && (ns === 'verified' || ns === 'reviewed')) { stuck.add(s.id); return }
    // A review that recorded no verdict leaves the slice `verified` (a pass would be `reviewed`,
    // a rejection `rejected`) -> no progress -> stop (else ACTION[verified] re-runs review forever).
    if (action === 'review' && ns === 'verified') { stuck.add(s.id); return }
    // else loop: verify->verified advances to review (or ship if REVIEW off); review->reviewed
    // advances to ship; review->rejected and verify->rejected/verifying/blocked route back through
    // NEEDS_BUILD above as the next try.
  }
}

// 3. Target-mutex scheduler: start every runnable slice whose target is free, await the next
//    pipeline to finish (freeing its target + unlocking dependents), repeat until quiescent.
const running = new Map()    // id -> pipeline promise
const busy = new Set()       // targets currently occupied by a running pipeline
const start = (s) => {
  busy.add(targetOf(s))
  running.set(s.id, drivePipeline(s).then(() => { busy.delete(targetOf(s)); running.delete(s.id) }))
}
while (true) {
  const seen = new Set()     // don't start two pipelines on the same target in one tick
  for (const s of slices) {
    if (!isLive(s) || running.has(s.id) || !depsDone(s) || depDead(s)) continue
    const t = targetOf(s)
    if (busy.has(t) || seen.has(t)) continue
    seen.add(t); start(s)
  }
  if (running.size === 0) break               // nothing running and nothing newly runnable -> done
  await Promise.race([...running.values()])   // wait for the next pipeline, then re-evaluate
}

// 4. Classify outcomes for the launcher.
return {
  shipped: slices.filter((s) => done.has(s.id)).map((s) => s.id),
  capped:  [...capped],
  stuck:   slices.filter((s) => !done.has(s.id) && !capped.has(s.id)).map((s) => ({
    id: s.id,
    reason: blocked.has(s.id) ? 'blocked'
          : stuck.has(s.id) ? `left at ${status[s.id]} (no progress / verdict not recorded)`
          : depDead(s) ? 'a dependency cannot ship in this run'
          : `left at ${status[s.id]}`,
  })),
  blockedAtStart: plan.blockedIds,
  draftAtStart: plan.draftIds,
  requestedUnavailable,   // scoped mode: named ids that weren't actionable (with why); [] otherwise
}
```

When the `Workflow` tool returns, the launching session **commits once** (the run's shipped
slices are all `done` in the ledger but uncommitted in git), then reports what shipped, what was
`capped`/`stuck`/`blocked` (and the command to run for a human), any scoped-mode
`requestedUnavailable` ids, and updates `.kuru/progress.md`. See `commands/loop-workflow.md` for
the exact post-run commit + reporting.

## Arguments

Parse up to two tokens from `$ARGUMENTS`, in any order, and pass them as the workflow's `args`:

- a **slice scope** — one id or a **comma-separated list** (`SL-####`, case-insensitive, **no
  spaces in the list**) → `args.slices` (an array); drive only the named slices. The same
  target-keyed concurrency applies: named slices on **different** gate targets run in parallel,
  named slices on the **same** target serialize. A one-element list is the degenerate
  single-slice case. Omit the scope to drive the whole board.
- a bare **integer** → `args.maxTries` (default 2): per-run cap on **build→verify→review tries**
  (one try = one full build→verify→review cycle, or build→verify when review is off; any failed
  cycle — build blocked, verify rejected, **review rejected**, or no verdict — consumes a try and
  retries with a fresh builder, capping when the budget is spent).

In scoped mode you do **not** have to assert the named slices touch disjoint files — the script
serializes same-target slices for you and only parallelizes across targets (which are disjoint
subtrees by definition). The one thing you must ensure: a dependency of a named slice that isn't
itself named must already be `done`, or that slice can't ship — the script reports it
(`a dependency cannot ship in this run`) rather than silently pulling it in.

Tries are **per run**: every slice's try tally starts at 0 each launch; never read the lifetime
`rejections` from `show`. Re-launching resets the budget.
