---
description: Drive ready slices through build→verify→ship IN PARALLEL by authoring and launching a Claude Code dynamic workflow — fresh context per build/verify/ship, so the board clears without saturating this session. Optionally scope to a single slice or a comma-separated set of slices.
argument-hint: "[slice-id | id1,id2,... ] [max-reject-retries, default 2]"
---

Use the `loop-workflow` skill for context — it holds the full design and the reference script.

This is the **parallel** autonomous driver. It runs the mechanical `build → verify → ship` part
of the pipeline over **every slice that is actionable right now** (dependencies satisfied), but
unlike `/kuru:loop` it does so as a **Claude Code dynamic workflow**: you author a JavaScript
orchestration script, the user approves it, and the workflow runtime runs each phase as a fresh,
isolated `agent()`. That per-step clean context is the point — it's why this can clear a large
board without the orchestrator's context degrading, and why it supersedes `runner.py`.

The judgment-heavy phases (`/kuru:charter`, `/kuru:prd`, `/kuru:slice`) are still done by a human
first; this only loops the deterministic part. **Code review is opt-in** — a verified slice ships
straight to `done`; run `/kuru:review <id>` by hand on slices that warrant it.

## What this command does (it does not drive the slices itself)

1. **Check preconditions** (below). If any fail, STOP and name the command to run.
2. **Read the board:** `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next --all`.
   Present a short **plan** first — actionable-now (with next action + `depends_on`), waiting-on-
   deps (and which deps), draft/blocked — so the user sees the parallel set and the dependency
   edges before anything runs.
3. **Author the workflow script** per the `loop-workflow` skill's reference shape, parameterized
   to this board and to `$ARGUMENTS` (passed as the workflow's `args`). **Critical**: agents must
   NOT use `isolation: 'worktree'` — they all run in the same project tree to read/write the
   shared `ledger.json`. Then **launch it directly with the `Workflow` tool**. Do **not** print
   the script and ask the user to confirm first — the `Workflow` tool's own approval card already
   offers *Yes, run it / View raw script / No*, so a manual prompt just duplicates it (and shows
   the script twice). Hand the script to the tool; the runtime then asks the user to approve and
   runs it in the background (`/workflows` shows live progress). Do not invoke other slash
   commands yourself to drive slices; the workflow's agents do that via `/kuru:build`,
   `/kuru:verify`, `/kuru:ship`.
4. **After the workflow returns, commit once** (see Termination & reporting) — the run's shipped
   slices are `done` in the ledger but not yet committed.

The script (see skill) drives **phase-barriered rounds**, NOT a per-slice pipeline: a planning
agent reads `/kuru:status` once, then each round builds every build-ready slice in parallel,
**waits for all builds (barrier)**, verifies every built slice in parallel, **barrier**, then
ships every verified slice (`/kuru:ship <id> --no-commit`). The barrier is load-bearing — slices
share **one working tree**, and `verify` re-runs the gates and drives the app against the *whole*
tree, so a build still in flight would contaminate a concurrent verify *even on disjoint files*.
Each round's ships unlock dependents for the next round; a rejected slice re-enters the next
round's build phase. The per-run reject cap and the blocked-stops-a-slice-and-its-dependents rule
are enforced in the script.

## Arguments (`$ARGUMENTS`)

Parse up to two tokens, in any order, and pass them through as the workflow's `args`:

- A **slice scope** — one slice id, or a **comma-separated list** of ids (`SL-####`,
  case-insensitive, **no spaces inside the list**) → **scoped mode** (`args.slices`, an array):
  drive only the named slices, in the same phase-barriered rounds as whole-board mode. A
  single id is the degenerate **single-slice** case (no parallelism), like `/kuru:loop-slice`.
  Omit the scope entirely to drive the **whole board**.
- A bare **integer** → **`args.maxRejectRetries`** (default **2**): how many times a slice may be
  rejected/sent-back **in this run** before the workflow stops driving it and surfaces it.

So: `/kuru:loop-workflow` · `/kuru:loop-workflow 5` · `/kuru:loop-workflow SL-0002` ·
`/kuru:loop-workflow SL-0002 5` · `/kuru:loop-workflow SL-0001,SL-0002,SL-0011` ·
`/kuru:loop-workflow SL-0001,SL-0002,SL-0011 5`.

**Scoped mode = you curate a parallel set.** You, not the engine, assert the named slices are
safe to run together. On a single-project shared tree that means **they must touch disjoint
files** — parallel builds share one working tree, so overlapping edits clobber each other (and
contaminate each verify, which runs the gates over the whole tree). Slices that really overlap
belong in separate launches, or in sequential `/kuru:loop`. A dependency of a named slice that
isn't itself in the set must already be `done`, or that slice can't ship (the workflow reports it
rather than silently pulling the dep in).

**Retries are per-run.** Each launch starts every slice's tally at 0 — re-launching resets the
budget. Do not read the lifetime `rejections` from `show`.

## Preconditions — refuse to author/launch unless ALL hold

Run these first; if any fails, STOP and tell the user exactly which command to run instead. Do
**not** start charter/PRD/slicing yourself — those need a human.

1. Workspace healthy: `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" doctor`.
   (Target-dir *warnings* for not-yet-built slices don't block; a hard ✗ does.)
2. A charter exists: `.kuru/charter.md` present and filled in (not the empty template) → else
   STOP, point to `/kuru:charter`.
3. At least one PRD exists under `.kuru/prd/` → else STOP, point to `/kuru:prd`.
4. **Whole-board mode only:** no `draft` slices remain
   (`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls --status draft` is empty). A
   `draft` still needs human slicing/contracting → else STOP, point to `/kuru:slice`. In
   **scoped mode** (one id or a list) only the named slices and their dependencies must be
   contracted: run `next --slice <id> --json` for **each** named id — a `slice` action means it's
   still `draft` (STOP, point to `/kuru:slice`); a `blocked` reason → STOP; a `waiting_on_deps`
   whose blocking dep is **neither `done` nor itself in the named set** → STOP and tell the user
   to add that dep to the list or ship it first. Deps already `done`, or also named in the set,
   are fine — the workflow drives the set in dependency order.

## Termination & reporting

When the `Workflow` tool returns, it hands back `{ shipped, capped, stuck, blockedAtStart,
draftAtStart, requestedUnavailable }` (the last is scoped-mode only: named ids that weren't
actionable — already `done`, `draft`, `blocked`, or an unknown id, each with a `why`). Then,
**from this session** (which has shell access — the workflow's agents do not):

1. **Commit the run, once.** Every shipped slice is `done` in the ledger but uncommitted (the
   workflow shipped with `--no-commit`). The tree is now quiescent, so make a single commit:
   ```
   git add -A && git commit -m "kuru: ship <shipped ids> (loop-workflow run)"
   ```
   Do this on **every** exit path where anything shipped — success, retry-cap, or blocked — so the
   ledger (`done`) and git reconverge no matter how the run ended. If `shipped` is empty, skip the
   commit (nothing to record) and say so. If the commit fails (e.g. a rejecting hook), report it
   loudly: the slices are `done` but their code is uncommitted in the working tree.
2. **Update `.kuru/progress.md`** (current state, what the run did, the single next action).
3. **Report.** Summarize: how many slices shipped (and the commit sha), anything in `capped` or
   `stuck` or `blockedAtStart` (needs a human — name the slice and the command to run), any
   `draftAtStart` slices still needing a contract, and — in scoped mode — any
   `requestedUnavailable` ids you asked for that the run skipped (with the `why`, e.g. "SL-0011:
   already done", "SL-0002: draft — run /kuru:slice").
