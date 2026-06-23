---
name: loop-workflow
description: Use when running /kuru:loop-workflow, or when asked to drive ready Kurukuru slices through build→verify→ship in parallel using a Claude Code dynamic workflow. Explains how to author, present, and launch the workflow script, the per-slice promise-DAG pipeline, how slices route on the state machine (including retry/back-to-build edges), the deferred single commit, and the guardrails.
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
quiescence requirement and can fire the instant a slice is `verified`. Then, **after the whole
run**, the launching session makes **one commit** for everything shipped, on a now-quiescent
tree.

The trade you are accepting (deliberately): **one commit per run, not per slice** — you lose
per-slice revert granularity, and during the run `done`-in-the-ledger does not yet mean
committed-in-git. The final commit must therefore run on **every** exit path (success,
retry-cap, blocked), so the ledger and git reconverge no matter how the run ends.

## The loop is a per-slice promise-DAG pipeline

Because ship is free and commit is deferred, there is no need for round barriers. Each slice
gets its **own driver** that runs `build → verify → ship` as fast as it independently can,
with the back-to-build retry edges handled in a local loop. The clever part: **a slice's
dependencies are modelled as promises** — its driver `await`s its dependency drivers before it
starts building. So the kuru dependency DAG becomes a JavaScript promise DAG: a dependent slice
starts the moment its deps have shipped, with no polling and no coordinator re-read.

State routing comes straight from the state machine: each command-agent reports the slice's
**status afterward**, and the driver dispatches the next action for that status
(`ready`/`in_progress`/`rejected` → build, `built`/`verifying` → verify,
`verified`/`reviewed` → ship). A `rejected` simply loops back to build. The planning agent
reads the board **once** at the start to get the slice set, each slice's current status, and
the dependency graph.

## Guardrails (carried over — do not drop)

- **Builder ≠ verifier.** Each build and each verify is a separate `agent()` with its own
  context — structurally guaranteed. Never have one agent both build and verify a slice.
- **Cap the send-back cycle, per run.** Each driver keeps its own reject tally (starts at 0).
  When its slice is `rejected`, increment; once it hits `maxRejectRetries` (default 2), **stop
  driving that slice** and report it `capped`. Do not auto-flip it to `blocked` (there is no
  `/kuru:*` verb for that and agents can't run `kuru.py`) — leaving it `rejected` and reporting
  it is enough; a re-run gets a fresh budget.
- **`blocked` stops a slice and its dependents, not the whole board.** If a build/verify leaves
  a slice `blocked` (or it was blocked at start), its driver returns un-shipped, and every slice
  that depends on it returns un-shipped too (its `await` of that dep fails). **Independent
  slices still finish** — this is not "routing around" a block, it's completing the safe work —
  and every blocked/stuck/capped slice is surfaced at the end for a human. "Some slices didn't
  ship" is a reported outcome, never silent.
- **Only `kuru.py` mutates state** — and only via the `/kuru:*` commands the agents run. Never
  have an agent hand-edit `ledger.json` / `gate-results.json`. A refused transition is a real
  signal.
- **Dependencies must be acyclic** (a kuru invariant — you can't ship a cycle). The promise-DAG
  assumes this; `doctor` and the slicing methodology keep it true.

## Preconditions (the command checks these before authoring anything)

The judgment-heavy phases stay human. Refuse to author/launch unless **all** hold; otherwise
STOP and name the command to run:

1. `kuru doctor` reports healthy (a hard ✗ blocks; target-dir *warnings* don't).
2. `.kuru/charter.md` exists and is filled in → else `/kuru:charter`.
3. At least one PRD under `.kuru/prd/` → else `/kuru:prd`.
4. **Whole-board mode:** no `draft` slices remain (`kuru ls --status draft` is empty) → else
   `/kuru:slice`. **Single-slice mode** (`/kuru:loop-workflow SL-0002`): only the named slice and
   its deps must be contracted — check `kuru next --slice <id> --json`.

## Reference script

Author a script in this shape, parameterized to the current board. Pass options through the
`Workflow` tool's `args` (e.g. `{ maxRejectRetries: 2, slice: "SL-0002" }`). Keep the structure;
adapt prompts/labels to the slices you're driving.

```javascript
export const meta = {
  name: 'kuru-loop-workflow',
  description: 'Drive ready Kurukuru slices through build -> verify -> ship as a per-slice promise-DAG pipeline; ship defers the commit (the launcher commits once after the run).',
  phases: [
    { title: 'Plan',   detail: 'read the board + dependency graph via /kuru:status' },
    { title: 'Build',  detail: 'one fresh agent per slice (/kuru:build)' },
    { title: 'Verify', detail: 'a separate fresh agent (/kuru:verify)' },
    { title: 'Ship',   detail: 'flip verified slices to done (/kuru:ship --no-commit)' },
  ],
}

const MAX_RETRIES = (args && args.maxRejectRetries) || 2
const ONLY = args && args.slice ? String(args.slice).toUpperCase() : null

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
let slices = ONLY ? plan.slices.filter((s) => s.id === ONLY) : plan.slices
const byId = Object.fromEntries(slices.map((s) => [s.id, s]))

// 2. One memoized driver per slice. Dependencies are awaited as PROMISES, so a slice starts
//    building only once every dep has shipped — the dependency DAG becomes a promise DAG.
const drivers = {}
const driveSlice = (s) => {
  if (drivers[s.id]) return drivers[s.id]
  drivers[s.id] = (async () => {
    // Wait for dependencies (skip any already done before this run).
    const deps = (s.dependsOn || []).filter((d) => !done.has(d))
    const depOutcomes = await Promise.all(
      deps.map((d) => (byId[d] ? driveSlice(byId[d]) : Promise.resolve({ shipped: false, id: d })))
    )
    if (depOutcomes.some((o) => !o.shipped)) {
      return { id: s.id, shipped: false, reason: 'a dependency did not ship' }
    }

    let status = s.status
    let rejects = 0
    while (true) {
      const action = ACTION[status]
      if (action === 'build') {
        const r = await agent(
          `Run \`/kuru:build ${s.id}\` and let it finish (it dispatches the builder and runs the gates). Report this slice's resulting status.`,
          { label: `build:${s.id}`, phase: 'Build', schema: RESULT })
        status = r.status
        if (status === 'blocked') return { id: s.id, shipped: false, reason: 'blocked' }
        if (status === 'rejected') {
          if (++rejects >= MAX_RETRIES) return { id: s.id, shipped: false, capped: true, reason: 'build retry cap' }
          continue
        }
      } else if (action === 'verify') {
        const r = await agent(
          `Run \`/kuru:verify ${s.id}\` and let it finish (a fresh, INDEPENDENT verifier re-runs the gates and checks the contract). Report this slice's resulting status.`,
          { label: `verify:${s.id}`, phase: 'Verify', schema: RESULT })
        status = r.status
        if (status === 'blocked') return { id: s.id, shipped: false, reason: 'blocked' }
        if (status === 'rejected') {
          if (++rejects >= MAX_RETRIES) return { id: s.id, shipped: false, capped: true, reason: 'verify retry cap' }
          continue
        }
      } else if (action === 'ship') {
        await agent(
          `Run \`/kuru:ship ${s.id} --no-commit\` — flip the slice to done WITHOUT committing (the launcher commits after the run). Report the slice's status.`,
          { label: `ship:${s.id}`, phase: 'Ship', schema: RESULT })
        return { id: s.id, shipped: true }
      } else {
        return { id: s.id, shipped: false, reason: `unexpected status ${status}` }
      }
    }
  })()
  return drivers[s.id]
}

// 3. Run every slice's driver concurrently; dependents naturally wait on their deps' promises.
const outcomes = await Promise.all(slices.map(driveSlice))

return {
  shipped: outcomes.filter((o) => o.shipped).map((o) => o.id),
  capped:  outcomes.filter((o) => o.capped).map((o) => o.id),
  stuck:   outcomes.filter((o) => !o.shipped && !o.capped).map((o) => ({ id: o.id, reason: o.reason })),
  blockedAtStart: plan.blockedIds,
  draftAtStart: plan.draftIds,
}
```

When the `Workflow` tool returns, the launching session **commits once** (the run's shipped
slices are all `done` in the ledger but uncommitted in git), then reports what shipped, what was
`capped`/`stuck`/`blocked` (and the command to run for a human), and updates
`.kuru/progress.md`. See `commands/loop-workflow.md` for the exact post-run commit + reporting.

## Arguments

Parse up to two tokens from `$ARGUMENTS`, in any order, and pass them as the workflow's `args`:

- a **slice id** (`SL-####`, case-insensitive) → single-slice mode (`args.slice`); drive only
  that slice. (No parallelism — one slice — same intent as `/kuru:loop-slice`.)
- a bare **integer** → `args.maxRejectRetries` (default 2): per-run rejection cap.

Retries are **per run**: start every slice's tally at 0 each launch; never read the lifetime
`rejections` from `show`. Re-launching resets the budget.
