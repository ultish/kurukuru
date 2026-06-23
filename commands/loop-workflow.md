---
description: Drive ready slices through build→verify→ship IN PARALLEL by authoring and launching a Claude Code dynamic workflow — fresh context per build/verify/ship, so the board clears without saturating this session. Optionally scope to a single slice.
argument-hint: "[slice-id] [max-reject-retries, default 2]"
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
   to this board and to `$ARGUMENTS` (passed as the workflow's `args`), and **launch it directly
   with the `Workflow` tool**. Do **not** print the script and ask the user to confirm first —
   the `Workflow` tool's own approval card already offers *Yes, run it / View raw script / No*, so
   a manual prompt just duplicates it (and shows the script twice). Hand the script to the tool;
   the runtime then asks the user to approve and runs it in the background (`/workflows` shows
   live progress). Do not invoke other slash commands yourself to drive slices; the workflow's
   agents do that via `/kuru:build`, `/kuru:verify`, `/kuru:ship`.
4. **After the workflow returns, commit once** (see Termination & reporting) — the run's shipped
   slices are `done` in the ledger but not yet committed.

The script (see skill) is a **per-slice promise-DAG pipeline**: a planning agent reads
`/kuru:status` once, then each slice gets its own driver that runs `build → verify → ship` as
fast as it can, `await`ing its dependency drivers first (the dependency graph becomes a promise
graph, so dependents start the instant their deps ship). The kuru state machine routes retries
(a rejected slice loops back to build); ship runs `/kuru:ship <id> --no-commit`, so it has no
quiescent-tree requirement and fires immediately. The per-run reject cap and the
blocked-stops-a-slice-and-its-dependents rule are enforced in the script.

## Arguments (`$ARGUMENTS`)

Parse up to two tokens, in any order, and pass them through as the workflow's `args`:

- A **slice id** (`SL-####`, case-insensitive) → **single-slice mode** (`args.slice`): drive only
  that slice to `done` (no parallelism — one slice), like `/kuru:loop-slice`.
- A bare **integer** → **`args.maxRejectRetries`** (default **2**): how many times a slice may be
  rejected/sent-back **in this run** before the workflow stops driving it and surfaces it.

So: `/kuru:loop-workflow` · `/kuru:loop-workflow 5` · `/kuru:loop-workflow SL-0002` ·
`/kuru:loop-workflow SL-0002 5`.

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
   **single-slice mode** only the named slice and its dependencies must be contracted — check
   `next --slice <id> --json` (a `slice` action or `waiting_on_deps`/`blocked` reason → STOP).

## Termination & reporting

When the `Workflow` tool returns, it hands back `{ shipped, capped, stuck, blockedAtStart,
draftAtStart }`. Then, **from this session** (which has shell access — the workflow's agents do
not):

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
   `stuck` or `blockedAtStart` (needs a human — name the slice and the command to run), and any
   `draftAtStart` slices still needing a contract.
