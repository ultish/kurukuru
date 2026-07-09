---
description: Autonomously drive ONE named slice through build→verify→done, then stop (code review is opt-in). Like /kuru:loop but scoped to a single slice you name.
argument-hint: "<slice-id> [max-tries, default 2]"
---

Use the `kuru-method` skill for context.

Same mechanical build → verify → done driver as `/kuru:loop`, but scoped to **one slice
you name** — it ships exactly that slice and then stops, instead of clearing the whole
board. Use it to step through delivery one slice per invocation while staying hands-on,
or to push a specific slice that matters next.

The slice id comes from `$ARGUMENTS` (e.g. `SL-0003`); `max-tries` (also from
`$ARGUMENTS`, default **2**) caps how many **build→verify tries** the slice gets
**in this run** before the loop stops and asks for a human. One try is one full
`build → verify` cycle; **any** failed cycle — a verify rejection, a build that goes
`blocked`, or a verify with no verdict — consumes a try and is retried with a **fresh**
subagent. The budget is **per run** — re-running this command resets the tally to 0, so
the cap governs only the current run.
**Code review is opt-in** — a verified slice ships straight to `done`; run
`/kuru:review <id>` by hand if this slice warrants a closer look.

**Why a separate command (not a flag on `/kuru:loop`):** the board loop picks work with
`kuru next`, whose ranking can hand back a *different* ready slice — so a single-slice
mode bolted onto it could drift onto a sibling. This command instead asks the engine
**`kuru next --slice <id>`** every iteration, which returns the next action for *that one
slice only*. The "only this slice" guarantee is machine-checked in `kuru.py`, not
narrated — there is no board pick to drift onto.

## Preconditions — refuse to start unless ALL hold

Run these first; if any fails, STOP and tell the user exactly which command to run
instead. Do **not** start charter/spec/slicing yourself — those need a human.

1. Workspace healthy: `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" doctor`.
2. A charter exists: `.kuru/charter.md` present and filled in (not the empty template).
   If missing/empty → STOP, point to `/kuru:charter`.
3. At least one spec exists under `.kuru/spec/`. If none → STOP, point to `/kuru:spec`.
4. The named slice is **actionable**. Run
   `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next --slice <id> --json`
   and read the result (unlike `/kuru:loop`, **other** slices may still be `draft` — only
   this slice and its dependencies need to be contracted):
   - missing id → the command errors; STOP and report the typo.
   - `next_action: "slice"` (the slice is `draft`) → STOP; a human must slice/contract it
     first (`/kuru:slice`). This command never creates contracts.
   - `reason: "waiting_on_deps"` → STOP; its `depends_on` aren't all `done`. Tell the user
     to finish those first (e.g. `/kuru:loop-slice <dep>` each, or `/kuru:loop`).
   - `reason: "blocked"` → STOP; the slice is `blocked` and needs a human.
   - `reason: "done"` → nothing to do; report it already shipped.

## The loop

Repeat until a stop condition fires:

1. Re-derive state from files (do not trust earlier chat): run
   `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next --slice <id> --json`.
   Act **only** on what it returns for this slice. For an unattended run outside Claude,
   use the top-level `runner.py --slice <id>` instead — same logic in plain Python.
2. If `next_action` is `"none"`:
   - `reason: "done"` → go to **Termination** (the slice shipped — success).
   - `reason: "blocked"` or `"waiting_on_deps"` → STOP and surface it (see Preconditions).
3. Otherwise act on `next_action`, using the **same behavior as the matching command**:

   | next_action | action |
   |---|---|
   | `build` (status `ready` / `in_progress` / `rejected`) | **first**, if this slice hasn't passed the pre-build contract check this run, run it (see **Pre-build contract check** below) and proceed only on `CONTRACT OK`. Then dispatch a **fresh `kuru-builder`** subagent (as `/kuru:build <id>`) on this slice. If it's `rejected`, pass the verifier's rejection note so the builder fixes the named failures. (Skip the contract check on a `rejected` slice — its contract already passed; only the build failed.) |
   | `verify` (status `built` / `verifying`) | set `built → verifying`, then dispatch a **fresh `kuru-verifier`** subagent (as `/kuru:verify <id>`). |
   | `ship` (status `verified` / `reviewed`) | **ship it** — `set-status <id> done` (auto-commits the slice: code + `.kuru/` artifacts + ledger, as one commit), then go to **Termination**. Code review is opt-in and this loop does **not** run it. |

4. After a build or verify, read `kuru show <id>`. If it left the slice **`blocked`** or
   **`verifying`** with no recorded verdict, that's a **failed try**, not a stop: while the
   slice is under its `max-tries` budget, reset it to buildable (`blocked` → `in_progress`,
   `verifying` → `rejected`) and loop to rebuild with a **fresh** builder. `kuru next
   --slice <id>` won't hand back a `blocked` slice, so do this reset yourself — otherwise a
   failed build ends the run at one attempt. Only once the budget is spent do you leave it
   `blocked` and stop.
5. After each transition, briefly note progress, then loop.

### Pre-build contract check (advisory, before the first build)

Before this slice is built for the first time **this run**, gate it through the
**kuru-contract-critic** (as `/kuru:check-contract <id>`) — catching a contract no build
could satisfy *before* a build→verify loop is wasted on it.

- **CONTRACT OK** → mark it checked for this run, build.
- **CONTRACT FLAGGED** → run the **contract-repair cycle**: `set-status <id> draft` →
  dispatch a **fresh `kuru-planner`** with the critic's flags + `contract-review.md` to
  rewrite `contract.yml`/`slice.md` → `set-status <id> ready` → re-run the critic.
  Repeat until `CONTRACT OK`, **capped by `max-tries`** (repair attempts count
  toward the same per-run budget). If it can't converge, STOP — `set-status <id> blocked
  --note "contract un-satisfiable after N repair attempts: <last flags>"`.

The critic is **advisory** — it never changes status or edits the contract; the planner
repairs, the engine records the `draft→ready` transitions.

### Hard guardrails (these are the point — do not skip)

- **Builder ≠ verifier, every time.** Each build and each verify must be a **separate
  subagent invocation** with its own context. Never let the agent that implemented the
  slice also verify it — the independence is the whole reason this works. The engine
  refuses `verified --by builder`, but you must also not reuse the builder's context.
- **A try is a full `build → verify` cycle; cap tries, per run.** Keep a this-run tally
  for the slice, starting at 0, and count a try at the **build** that starts each cycle —
  so the budget bounds build→verify cycles, **not just verify rejections**. Any failed
  cycle consumes a try: a verify that **rejects**, a build that goes **blocked**, or a
  verify with **no verdict**. Do **not** read the slice's lifetime `rejections` from
  `show` — the budget is per run, so a re-run gets a fresh one. When the tally reaches
  `max-tries`, STOP: `set-status <id> blocked --note "exhausted N build→verify tries this
  run: <last failure>"` and hand to a human. Do not spin forever.
- **Check the contract before building.** Don't dispatch a builder on a `ready` slice
  that hasn't gone `CONTRACT OK` this run. A flagged contract is repaired by a fresh
  **planner** (distinct from builder/verifier) via the `draft→ready` cycle above — never
  by the critic, the builder, or hand-editing `contract.yml`. Repair attempts count
  toward `max-tries`.
- **A mid-run `blocked` build is a failed try; a slice blocked at start is left for a
  human.** If a **builder or verifier** blocks the slice **during this run**, reset it
  (`blocked` → `in_progress`) and retry with a fresh builder while under `max-tries` (step
  4); only once the budget is spent do you leave it `blocked` and surface it. A slice
  **already `blocked` before the run** (or the contract-repair cap's hard stop) is not
  auto-retried — surface it. Never fabricate progress, and never `set-status` the slice
  `built`/`verified` yourself.
- **Never fabricate progress.** You only ever change status through `kuru.py`; you never
  hand-edit `ledger.json`/`gate-results.json`. If the engine's gate + role rules refuse a
  transition, that is a real signal, not an obstacle.

## Termination

In every case, **update `.kuru/progress.md`** (current state, what you did, the single
next action) before reporting:

- The slice reached `done` → success. Report that it shipped, and what
  `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next` would pick up next, so
  the user can re-run (`/kuru:loop-slice <next>` or `/kuru:loop`) when ready.
- The slice is `blocked`, the retry cap was hit, or it needs a human (draft / unmet deps)
  → STOP and tell the user exactly what needs a human and which command to run.
