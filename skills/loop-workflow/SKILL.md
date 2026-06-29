---
name: loop-workflow
description: Use when running /kuru:loop-workflow, or when asked to drive ready Kurukuru slices through build→verify→ship using a Claude Code dynamic workflow. Explains how to author, present, and launch the workflow script, the PER-SLICE pipelines keyed by gate target (same target serialized, different targets parallel — the no-worktrees lesson), how slices route on the state machine (including retry/back-to-build edges), the deferred single commit, and the guardrails.
---

# Driving the board with a dynamic workflow

`/kuru:loop-workflow` clears the board the same way `/kuru:loop` does — the mechanical
`build → verify → ship` cycle — but it drives each slice as its **own pipeline** and runs
slices **on different gate targets in parallel** (same-target slices serialize), as a **Claude
Code dynamic workflow** rather than by orchestrating subagents inside this conversation.

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

Each slice is driven through its **own** `build → verify → ship` pipeline — a fresh `agent()`
per stage. The slices do **not** march through global phases. What decides parallelism is the
**gate target** (the `config.json` target a slice builds under; `"default"` for a single-target
repo):

- **Same target → serialized.** A target runs **at most one** slice's pipeline at a time. This
  is the no-worktrees lesson made into a rule: the slices share **one working tree** (worktrees
  can't be used — they'd fork the in-tree `ledger.json`), so two same-target slices running at
  once would (a) clobber each other's edits during `build`, and (b) let one slice's in-flight
  build contaminate the other's `verify`, which re-runs the gates and drives the running app
  against that target's tree. Serializing the whole pipeline — not just barriering one phase —
  removes both: while slice A is anywhere in build→verify→ship on target T, no other slice on T
  starts.
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
nothing new can start. A `rejected` verdict loops the slice back through build **within its own
pipeline** (capped by its per-run tally); `blocked`/`capped`/`stuck` stop that slice and surface
it. State routing is the state machine: `ready`/`in_progress`/`rejected` → build,
`built`/`verifying` → verify, `verified`/`reviewed` → ship.

**Pre-build contract check (advisory).** Before a slice's **first build this run** (status
`ready`/`in_progress`, not `rejected`), its pipeline runs the **contract critic**
(`/kuru:check-contract <id>`) — catching a contract no build could satisfy (an AC nothing builds,
or one unverifiable in this env) before a build→verify loop is wasted. `CONTRACT OK` → build;
`CONTRACT FLAGGED` → a **repair** stage routes it back through the planner (`draft` → rewrite from
the flags → `ready`) and re-checks, capped by `MAX_RETRIES` (a contract that won't converge is
`capped`, exactly like an exhausted reject budget). The critic is advisory — it changes no status
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
- **Route every slice by its *current* status through the `ACTION` table** — `build` for
  `ready`/`in_progress`/`rejected`, `verify` for `built`/`verifying`, `ship` for
  `verified`/`reviewed`. A stage that returns a status routing back to **itself** (e.g. a verify
  that leaves the slice `verifying` — no verdict recorded — or a ship the engine refused) is **not
  progress**: stop that slice and report it `stuck` rather than re-running the same stage. The
  reference `drivePipeline` does exactly this (`ACTION[newStatus] === action` ⇒ stuck).
- **Cap the send-back cycle, per run.** The script keeps a per-slice reject tally (starts at 0).
  When a verify returns `rejected`, increment; once it hits `maxRejectRetries` (default 2),
  **stop driving that slice** (report it `capped`). Do not auto-flip it to `blocked` (there is no
  `/kuru:*` verb for that and agents can't run `kuru.py`) — leaving it `rejected` and reporting it
  is enough; a re-run gets a fresh budget.
- **`blocked` stops a slice and its dependents, not the whole board.** If a build/verify leaves a
  slice `blocked` (or it was blocked at start), it's dropped from scheduling, and any slice that
  depends on it never starts (its dep can't ship) and is reported `stuck`. **Independent slices
  still finish** — this is completing the safe work, not routing around a block — and every
  blocked/stuck/capped slice is surfaced at the end for a human.
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
3. At least one PRD under `.kuru/prd/` → else `/kuru:prd`.
4. **Whole-board mode:** no `draft` slices remain (`kuru ls --status draft` is empty) → else
   `/kuru:slice`. **Scoped mode** (`/kuru:loop-workflow SL-0002` or `SL-0001,SL-0002,SL-0011`):
   only the named slices and their deps must be contracted — run `kuru next --slice <id> --json`
   for each named id (a `slice` action → still draft, STOP; `blocked` → STOP; `waiting_on_deps`
   whose blocking dep is neither `done` nor itself named → STOP, tell the user to add it or ship
   it first).

## Reference script

Author a script in this shape, parameterized to the current board. Pass options through the
`Workflow` tool's `args` (e.g. `{ maxRejectRetries: 2, slices: ["SL-0001", "SL-0002"] }`; omit
`slices` for the whole board). Keep the structure; adapt prompts/labels to the slices you're
driving.

```javascript
export const meta = {
  name: 'kuru-loop-workflow',
  description: 'Drive ready Kurukuru slices through PER-SLICE build -> verify -> ship pipelines, dependency-ordered. Slices on the SAME gate target serialize (one shared tree, no worktrees); slices on DIFFERENT targets run in parallel. Ship defers the commit (the launcher commits once after the run).',
  phases: [
    { title: 'Plan', detail: 'read the board: each slice status, depends_on, and gate target' },
    { title: 'Pipelines', detail: 'one build->verify->ship pipeline per slice; same target serialized, different targets parallel' },
  ],
}

const MAX_RETRIES = (args && args.maxRejectRetries) || 2
// Scope: args.slices is an array of ids — absent/empty = whole board. The target-keyed
// concurrency rule applies either way: named slices on different targets parallelize, named
// slices on the same target serialize. Case-insensitive.
const SCOPE = args && args.slices && args.slices.length
  ? new Set(args.slices.map((x) => String(x).toUpperCase()))
  : null

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

const ACTION = {
  ready: 'build', in_progress: 'build', rejected: 'build',
  built: 'verify', verifying: 'verify',
  verified: 'ship', reviewed: 'ship',
}
const done = new Set(plan.doneIds)
let slices = SCOPE ? plan.slices.filter((s) => SCOPE.has(s.id)) : plan.slices
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
const rejects = {}           // id -> per-run reject tally
const repairs = {}           // id -> per-run contract-repair tally
const checked = new Set()    // passed the pre-build contract check this run (CONTRACT OK)
const blocked = new Set()    // hit blocked this run
const capped = new Set()     // exhausted the reject OR contract-repair budget
const stuck = new Set()      // a stage made no forward progress (e.g. verdict not recorded)
for (const s of slices) { status[s.id] = s.status; rejects[s.id] = 0; repairs[s.id] = 0 }

const isLive = (s) => !done.has(s.id) && !blocked.has(s.id) && !capped.has(s.id) && !stuck.has(s.id)
const depsDone = (s) => (s.dependsOn || []).every((d) => done.has(d))
// A dependency that can never ship in this run kills its dependents: blocked/capped/stuck, or —
// whole board (draft/blocked dep) or scoped (dep not named) — absent from `byId` and not done.
const depDead = (s) => (s.dependsOn || []).some(
  (d) => blocked.has(d) || capped.has(d) || stuck.has(d) || (!done.has(d) && !byId[d]))

const prompt = {
  check: (id) => `Run \`/kuru:check-contract ${id}\` — a fresh contract critic judges whether ${id}'s FROZEN contract is satisfiable (something builds each AC — this slice or an earlier done slice) and verifiable in this env. It is ADVISORY: it changes NO status and edits NO files but \`.kuru/slices/${id}/contract-review.md\`. After it returns, read that report and return { id, verdict: "ok" if it says CONTRACT OK else "flagged", note: the essence of the flags }.`,
  repair: (id) => `${id}'s contract was FLAGGED as un-satisfiable/un-verifiable. Run \`kuru set-status ${id} draft\`, then dispatch a fresh kuru-planner to rewrite contract.yml/slice.md from the flags in \`.kuru/slices/${id}/contract-review.md\` so every acceptance criterion is satisfiable and verifiable in this environment (do NOT drop scope to dodge a flag — fix the wording or the slice boundary). Then run \`kuru set-status ${id} ready\`. Finally run \`kuru show ${id}\` and report the status the LEDGER shows.`,
  build: (id) => `Run \`/kuru:build ${id}\` and let it finish (it dispatches the builder and runs the gates). Then run \`kuru show ${id}\` and report the status the LEDGER shows.`,
  verify: (id) => `Run \`/kuru:verify ${id}\` and let it finish (a fresh, INDEPENDENT verifier re-runs the gates and checks the contract). The verdict is only real once recorded: the verifier MUST end by running \`kuru set-status ${id} verified|rejected --by verifier\` — a "PASS" only in prose or in verification.md is NOT a verdict and leaves the slice in \`verifying\`. After it returns, run \`kuru show ${id}\` and report the status the LEDGER actually shows (verified / rejected / verifying / blocked) — never a status inferred from narration.`,
  ship: (id) => `Run \`/kuru:ship ${id} --no-commit\` — flip the slice to done WITHOUT committing (the launcher commits after the run). Then run \`kuru show ${id}\` and report the status the LEDGER shows.`,
}

// Drive ONE slice through its build -> verify -> ship pipeline, looping rejects up to the cap.
// Every agent shares the one tree (NO worktrees). The scheduler holds this slice's target mutex
// for the whole pipeline, so no same-target build is in flight to contaminate this verify.
const drivePipeline = async (s) => {
  while (true) {
    const action = ACTION[status[s.id]]
    if (!action) { stuck.add(s.id); return }                 // unexpected status -> stop, report
    // Pre-build contract check (advisory) — before the FIRST build of this slice this run.
    // Skip on 'rejected' (its contract already passed; only the build failed). A FLAGGED
    // contract is repaired by the planner (draft->ready) and re-checked, capped by MAX_RETRIES.
    if (action === 'build' && (status[s.id] === 'ready' || status[s.id] === 'in_progress') && !checked.has(s.id)) {
      while (true) {
        let cr
        try { cr = await agent(prompt.check(s.id), { label: `check:${s.id}`, phase: s.id, schema: CHECK }) }
        catch { blocked.add(s.id); return }
        if (!cr || cr.verdict === 'ok') { checked.add(s.id); break }   // safe to build
        if (++repairs[s.id] > MAX_RETRIES) { capped.add(s.id); return } // contract won't converge -> stop
        let rr
        try { rr = await agent(prompt.repair(s.id), { label: `repair:${s.id}`, phase: s.id, schema: RESULT }) }
        catch { blocked.add(s.id); return }
        const rs = rr && rr.status
        if (!rs || rs === 'blocked' || rs === 'error') { blocked.add(s.id); return }
        status[s.id] = rs                                             // expect 'ready' -> re-check
      }
    }
    let r
    try {
      r = await agent(prompt[action](s.id), { label: `${action}:${s.id}`, phase: s.id, schema: RESULT })
    } catch { blocked.add(s.id); return }
    const ns = r && r.status
    if (!ns || ns === 'error') { blocked.add(s.id); return }
    status[s.id] = ns
    if (ns === 'blocked') { blocked.add(s.id); return }
    if (ns === 'done') { done.add(s.id); return }
    if (ns === 'rejected') {                                 // verify sent it back
      if (++rejects[s.id] >= MAX_RETRIES) { capped.add(s.id); return }
      continue                                               // ACTION['rejected'] === 'build' -> rebuild
    }
    if (ACTION[ns] === action) { stuck.add(s.id); return }   // stage didn't advance (e.g. verdict not recorded) -> stop
    // advanced to the next stage (ready->built, built->verified, ...) -> loop
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
- a bare **integer** → `args.maxRejectRetries` (default 2): per-run rejection cap.

In scoped mode you do **not** have to assert the named slices touch disjoint files — the script
serializes same-target slices for you and only parallelizes across targets (which are disjoint
subtrees by definition). The one thing you must ensure: a dependency of a named slice that isn't
itself named must already be `done`, or that slice can't ship — the script reports it
(`a dependency cannot ship in this run`) rather than silently pulling it in.

Retries are **per run**: start every slice's tally at 0 each launch; never read the lifetime
`rejections` from `show`. Re-launching resets the budget.
