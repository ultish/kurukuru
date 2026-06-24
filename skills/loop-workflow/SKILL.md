---
name: loop-workflow
description: Use when running /kuru:loop-workflow, or when asked to drive ready Kurukuru slices through build→verify→ship in parallel using a Claude Code dynamic workflow. Explains how to author, present, and launch the workflow script, the phase-barriered rounds (all builds finish before any verify, so a shared-tree verify isn't contaminated), how slices route on the state machine (including retry/back-to-build edges), the deferred single commit, and the guardrails.
---

# Driving the board with a dynamic workflow

`/kuru:loop-workflow` clears the board the same way `/kuru:loop` does — the mechanical
`build → verify → ship` cycle — but it runs the **independent slices in parallel** and it
does so as a **Claude Code dynamic workflow** rather than by orchestrating subagents inside
this conversation.

## Why a dynamic workflow (and not in-session orchestration)

The win is **clean contexts between phases**. In a long in-session loop, every builder and
verifier result piles into one context window; after a dozen slices the orchestrator is
reasoning in a saturated context and quality decays. A dynamic workflow runs each `agent()`
in its **own fresh context**, out of process — the script keeps only small structured results
in JS variables, never the agents' transcripts. So a 30-slice board costs the orchestrator
almost no context, and each build/verify starts from zero every time. This is why the workflow
**replaces `runner.py`**: it gives the per-step context isolation `runner.py` reached for, but
natively and resumably (within the session).

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
2. **Workflow agents drive kuru through the `/kuru:*` commands, not `kuru.py` directly.** So:
   - read the board → an agent runs **`/kuru:status`** and returns a structured snapshot;
   - build → an agent runs **`/kuru:build <id>`**;
   - verify → an agent runs **`/kuru:verify <id>`**;
   - ship → an agent runs **`/kuru:ship <id> --no-commit`** (see next section).
   This keeps the rule that **only `kuru.py` mutates the ledger** intact — the commands wrap it
   — without the script needing a path to `kuru.py`.
3. **No agents use `isolation: 'worktree'`.** All agents run in the same project tree so they
   read and write the **same** `ledger.json`. Worktrees break this: each worktree copies the
   ledger, so agent writes don't synchronize. Always omit `isolation` from agent options
   (defaults to shared tree). The launcher commits once afterward on the quiescent main tree.

   > If your runtime *can* run `kuru.py` from an agent, the planning agent can use
   > `kuru next --all --json` for an authoritative machine snapshot instead of parsing
   > `/kuru:status`. Prefer that when available; fall back to `/kuru:status` otherwise.

## The deferred commit (why ship can run immediately)

`set-status done` normally auto-commits the whole working tree. On a shared tree with many
slices building at once, a mid-run commit would sweep another slice's half-finished edits into
the wrong commit — which, in the old design, forced ships to be drained sequentially while the
tree was quiescent.

So the workflow **decouples the `done` transition from the commit**: every ship runs
`/kuru:ship <id> --no-commit`, which flips the slice to `done` in the ledger (lock-safe, no
tree contention) but makes **no git commit**. Because ship no longer touches git, it has no
quiescence requirement — the whole ship phase is just lock-safe ledger flips. Then, **after the
whole run**, the launching session makes **one commit** for everything shipped, on a
now-quiescent tree.

The trade you are accepting (deliberately): **one commit per run, not per slice** — you lose
per-slice revert granularity, and during the run `done`-in-the-ledger does not yet mean
committed-in-git. The final commit must therefore run on **every** exit path (success,
retry-cap, blocked), so the ledger and git reconverge no matter how the run ends.

## The loop is phase-barriered rounds (NOT a per-slice pipeline)

The slices share **one working tree** (worktrees can't be used — they'd fork the in-tree
`ledger.json`; see constraint 3 under "Two hard constraints" above). On a shared tree a
per-slice pipeline is unsafe:
it would let slice A reach `verify` while B and C are still in `build`, and **`verify` re-runs
the gates and drives the running app against the whole tree** — so it observes B's and C's
half-written code. That contaminates A's evidence *even when all three touch disjoint files*,
because verify reads the whole tree, not "A's files". Phase isolation is the only thing that
removes this; file-disjointness cannot.

So the loop runs **one phase at a time, with many slices per phase**:

1. **Build** every slice whose status routes to build (deps already `done`) — one fresh
   `agent()` each, **in parallel** — then **barrier**: wait for *all* builds to finish.
2. **Verify** every slice now `built` — parallel agents — then **barrier**. Because every build
   in this round has finished, no build is mutating the tree while these verifies run.
3. **Ship** every slice now `verified` — `/kuru:ship <id> --no-commit` (lock-safe ledger flips).

**Only the build→verify ordering is safety-critical.** Ship touches no source and makes no
commit — it's a lock-serialized ledger flip — so it can't contaminate or collide with anything
and could run at *any* time, concurrent with builds or verifies. It sits in its own phase purely
for code simplicity; because a ledger flip is instant, ordering it after verify (so the round's
passing slices ship before the rejected ones rebuild) costs essentially nothing. So in a round
where 2 of 3 slices are rejected, the 1 ships and the 2 re-enter the next round's build phase —
the ship and the rebuilds don't need isolating from each other.

Repeat the round until no slice can make progress. Each round's ships mark deps `done`, which
unlocks dependents for the **next** round — so the dependency DAG is honored across rounds (a
dependent waits one round past its last dep, the price of the barrier). A `rejected` slice loops
back to the next round's build phase (its per-run reject tally caps it). The planning agent reads
the board **once** up front for the slice set, each slice's status, and the dependency graph; the
script tracks status from each agent's reported result thereafter.

State routing comes straight from the state machine: `ready`/`in_progress`/`rejected` → build,
`built`/`verifying` → verify, `verified`/`reviewed` → ship.

> **Throughput cost (accepted deliberately):** barriers idle the fast slices in a phase until the
> slowest finishes, and a dependent always takes an extra round. This is the price of correct
> evidence on a shared tree. The only way to recover full pipelining would be per-slice worktree
> isolation — which this design rejects (it forks the ledger). On a **multi-target** repo the
> within-phase parallelism is fully safe (disjoint subtrees); on a **single-project** repo the
> remaining caveat is *within* a phase — concurrent verifies share the tree's runtime resources
> (ports when driving the app, build-output dirs, caches), so a curated set should keep those
> few enough not to collide.

## Guardrails (carried over — do not drop)

- **Builder ≠ verifier.** Each build and each verify is a separate `agent()` with its own
  context — structurally guaranteed. Never have one agent both build and verify a slice.
- **Cap the send-back cycle, per run.** The script keeps a per-slice reject tally (starts at 0).
  When a slice comes back `rejected`, increment; once it hits `maxRejectRetries` (default 2),
  **stop driving that slice** (drop it from future rounds) and report it `capped`. Do not
  auto-flip it to `blocked` (there is no `/kuru:*` verb for that and agents can't run `kuru.py`)
  — leaving it `rejected` and reporting it is enough; a re-run gets a fresh budget.
- **`blocked` stops a slice and its dependents, not the whole board.** If a build/verify leaves
  a slice `blocked` (or it was blocked at start), it's dropped from future rounds, and any slice
  that depends on it never enters a phase (its dep can't ship) and is reported `stuck`.
  **Independent slices still finish** — this is not "routing around" a block, it's completing the
  safe work — and every blocked/stuck/capped slice is surfaced at the end for a human. "Some
  slices didn't ship" is a reported outcome, never silent.
- **Only `kuru.py` mutates state** — and only via the `/kuru:*` commands the agents run. Never
  have an agent hand-edit `ledger.json` / `gate-results.json`. A refused transition is a real
  signal.
- **Dependencies must be acyclic** (a kuru invariant — you can't ship a cycle). The round loop
  assumes this: a cycle would never make progress and would stop at the no-progress break with
  the slices reported `stuck`. `doctor` and the slicing methodology keep the graph acyclic.

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
  description: 'Drive ready Kurukuru slices through build -> verify -> ship in PHASE-BARRIERED rounds (all builds finish before any verify, so no in-flight build contaminates a verify on the shared tree); ship defers the commit (the launcher commits once after the run).',
  phases: [
    { title: 'Plan',   detail: 'read the board + dependency graph via /kuru:status' },
    { title: 'Build',  detail: 'all build-ready slices in parallel, then BARRIER' },
    { title: 'Verify', detail: 'all built slices in parallel, then BARRIER' },
    { title: 'Ship',   detail: 'flip verified slices to done (/kuru:ship --no-commit)' },
  ],
}

const MAX_RETRIES = (args && args.maxRejectRetries) || 2
// Scope: args.slices is an array of ids — one id = single-slice (no parallelism), many =
// a curated parallel set, absent/empty = whole board. Case-insensitive.
const SCOPE = args && args.slices && args.slices.length
  ? new Set(args.slices.map((x) => String(x).toUpperCase()))
  : null

// **CRITICAL**: Agents do NOT use isolation: 'worktree'. All agents run in the same tree
// to read/write the shared ledger.json. Worktrees would fragment it into per-agent copies.
// The launcher commits once after the run on the quiescent main tree.

// What the planning agent returns after reading /kuru:status (read ONCE, up front).
const PLAN = {
  type: 'object', additionalProperties: false,
  required: ['slices', 'doneIds', 'blockedIds', 'draftIds'],
  properties: {
    slices: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['id', 'status', 'dependsOn'],
        properties: {
          id: { type: 'string' },
          status: { type: 'string' },                     // ready|in_progress|rejected|built|verifying|verified|reviewed
          dependsOn: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    doneIds:    { type: 'array', items: { type: 'string' } }, // already done before this run
    blockedIds: { type: 'array', items: { type: 'string' } },
    draftIds:   { type: 'array', items: { type: 'string' } },
  },
}

// What each command-agent returns: the slice's status AFTER running its /kuru command.
const RESULT = {
  type: 'object', additionalProperties: false,
  required: ['id', 'status'],
  properties: { id: { type: 'string' }, status: { type: 'string' }, note: { type: 'string' } },
}

// 1. Plan — one fresh agent reads the board + dependency graph, once.
// NOTE: Do NOT add isolation: 'worktree' — this agent must read from the shared ledger.json
const plan = await agent(
  `Run /kuru:status and read its output. Return every slice that is NOT done and NOT draft as
{ id, status, dependsOn } — status is the slice's current state (ready / in_progress / rejected /
built / verifying / verified / reviewed); dependsOn is its list of dependency slice ids. Also
return doneIds (already-done slices), blockedIds, and draftIds. Report exactly what /kuru:status
shows; do NOT start any work.`,
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

// Scoped mode: surface any requested id that isn't an actionable, non-done slice, with WHY —
// so a typo'd / already-done / draft / blocked id is reported, never silently dropped.
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
const blocked = new Set()    // ids that hit blocked this run (stop them + their dependents)
const capped = new Set()     // ids that exhausted the reject budget
for (const s of slices) { status[s.id] = s.status; rejects[s.id] = 0 }

const isLive = (s) => !done.has(s.id) && !blocked.has(s.id) && !capped.has(s.id)
const depsDone = (s) => (s.dependsOn || []).every((d) => done.has(d))
// A dependency that can never ship in this run kills its dependents: blocked, capped, or — for
// whole board (draft/blocked dep) or scoped (dep not named) — absent from `byId` and not done.
const depDead = (s) => (s.dependsOn || []).some(
  (d) => blocked.has(d) || capped.has(d) || (!done.has(d) && !byId[d]))

// Run ONE phase: every live slice whose status routes to `action`, in parallel, then BARRIER.
// The barrier is the whole point — no build mutates the tree while the verify phase runs.
const runPhase = async (action, phaseTitle, mkPrompt) => {
  const batch = slices.filter(
    (s) => isLive(s) && !depDead(s) && depsDone(s) && ACTION[status[s.id]] === action)
  if (!batch.length) return 0
  const results = await parallel(batch.map((s) => () =>
    // No isolation: every agent shares the one tree + ledger.json (worktrees would fork it).
    agent(mkPrompt(s.id), { label: `${action}:${s.id}`, phase: phaseTitle, schema: RESULT })
      .then((r) => ({ id: s.id, status: r && r.status }))
      .catch(() => ({ id: s.id, status: 'error' }))))
  for (const r of results) {
    if (!r || !r.status || r.status === 'error') { blocked.add(r.id); continue }
    status[r.id] = r.status
    if (r.status === 'blocked') blocked.add(r.id)
    else if (r.status === 'rejected') { if (++rejects[r.id] >= MAX_RETRIES) capped.add(r.id) }
    else if (r.status === 'done') done.add(r.id)
  }
  return batch.length
}

// 3. Phase-barriered rounds: ALL builds, barrier, ALL verifies, barrier, ALL ships. Each round's
//    ships mark deps done -> unlock dependents next round. A rejected slice re-enters build next
//    round (capped by its tally). Stop when a whole round makes no progress. The rounds cap is a
//    backstop against a non-advancing status; normal runs hit the no-progress break far sooner.
const MAX_ROUNDS = slices.length * (MAX_RETRIES + 2) + 5
for (let round = 0; round < MAX_ROUNDS; round++) {
  const built = await runPhase('build', 'Build',
    (id) => `Run \`/kuru:build ${id}\` and let it finish (it dispatches the builder and runs the gates). Report this slice's resulting status.`)
  const verified = await runPhase('verify', 'Verify',
    (id) => `Run \`/kuru:verify ${id}\` and let it finish (a fresh, INDEPENDENT verifier re-runs the gates and checks the contract). Report this slice's resulting status.`)
  const shipped = await runPhase('ship', 'Ship',
    (id) => `Run \`/kuru:ship ${id} --no-commit\` — flip the slice to done WITHOUT committing (the launcher commits after the run). Report the slice's status.`)
  if (!built && !verified && !shipped) break   // nothing could progress -> done
}

// 4. Classify outcomes for the launcher.
return {
  shipped: slices.filter((s) => done.has(s.id)).map((s) => s.id),
  capped:  [...capped],
  stuck:   slices.filter((s) => !done.has(s.id) && !capped.has(s.id)).map((s) => ({
    id: s.id,
    reason: blocked.has(s.id) ? 'blocked'
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
  spaces in the list**) → `args.slices` (an array); drive only the named slices, in parallel, on
  the same phase-barriered rounds. A one-element list is the degenerate single-slice case (no parallelism,
  same intent as `/kuru:loop-slice`). Omit the scope to drive the whole board.
- a bare **integer** → `args.maxRejectRetries` (default 2): per-run rejection cap.

In scoped mode **you** assert the set is safe to run together: on a single-project shared tree the
named slices must touch disjoint files, because parallel builds share one working tree and
overlapping edits clobber each other (and contaminate each verify, which runs the gates over the
whole tree). A dependency of a named slice that isn't itself in the set must already be `done`, or
that slice can't ship — the script reports it (`unmet dependency: …`) rather than silently pulling
it in.

Retries are **per run**: start every slice's tally at 0 each launch; never read the lifetime
`rejections` from `show`. Re-launching resets the budget.
