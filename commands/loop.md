---
description: Autonomously run the build→verify→review→ship loop over ready slices until the board is clear — or, given a slice id, drive just that one slice to done and stop (review runs when the workspace has it on — the default; toggle with `kuru set-review`). Requires charter + spec + frozen slices to already exist.
argument-hint: "[slice-id] [max-tries, default 2]"
---

Use the `kuru-method` skill for context.

This is the **optional autonomous driver** for the mechanical part of the pipeline.
The judgment-heavy phases (`/kuru:charter`, `/kuru:spec`, `/kuru:slice`) are done by a
human first; once every slice has a **frozen contract**, the per-slice
build → verify → review → ship cycle is deterministic enough to loop. **Code review
runs when this workspace has it on** — the `kuru init` default; the loop dispatches a
fresh reviewer on each verified slice and a review rejection sends it back to build like a
verify rejection. Turn it off with `kuru set-review off` (then a verified slice ships
straight to `done`). Which one applies is decided by the engine: **act on the `action`
`kuru next` returns** (`review` vs `ship`). The manual `/kuru:*` commands still work —
this just runs them for you, in `kuru next` order, until there is nothing left to do.

## Arguments (`$ARGUMENTS`)

Parse up to two tokens, in any order:

- An optional **slice id** (`SL-####`, case-insensitive, e.g. `SL-0003`) → **scoped mode**:
  drive **only that one slice** to `done`, then stop, instead of clearing the whole board. In
  scoped mode the loop asks the engine **`kuru next --slice <id>`** every iteration — the next
  action for *that one slice only* — so it can never drift onto a ready sibling the board would
  rank first; the "only this slice" guarantee is machine-checked in `kuru.py`, not narrated. Omit
  the id to drive the **whole board** (the default).
- A bare **integer** → **`max-tries`** (default **2**): how many **build→verify(→review) tries**
  a slice gets **in this run** before the loop stops and asks for a human. One try is one full
  `build → verify → review` cycle (just `build → verify` when review is off); **any** failed cycle
  — a verify rejection, a **review rejection**, a build that goes `blocked` (the builder gave up /
  gates stayed red), or a verify that records no verdict — consumes a try and is retried with a
  **fresh** subagent. The budget is **per run**: re-running `/kuru:loop` resets every slice's tally
  to 0, so the cap governs only the current run — not the slice's lifetime.

So: `/kuru:loop` · `/kuru:loop 5` · `/kuru:loop SL-0003` · `/kuru:loop SL-0003 5`. To work
several independent slices **in parallel** in one workflow, use **`/kuru:loop-workflow`**.

## Preconditions — refuse to start unless ALL hold

Run these checks first; if any fails, STOP and tell the user exactly which command
to run instead. Do **not** start charter/spec/slicing yourself — those need a human.

1. Workspace healthy: `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" doctor`.
2. A charter exists: `.kuru/charter.md` is present and filled in (not the empty
   template). If missing/empty → STOP, point to `/kuru:charter`.
3. At least one spec exists under `.kuru/spec/`. If none → STOP, point to `/kuru:spec`.
4. Slices exist and are **contracted**:
   - `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls` shows ≥1 slice.
   - **Whole-board mode (no slice id):**
     `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls --status draft` shows
     **none**. A `draft` slice still needs human slicing/contracting → STOP and
     point to `/kuru:slice` to finish (and freeze) it first.
   - **Scoped mode (a slice id given):** only the named slice and its dependencies must be
     contracted — other slices may still be `draft`. Run
     `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next --slice <id> --json` and
     read it:
     - missing id → the command errors; STOP and report the typo.
     - `next_action: "slice"` (the slice is `draft`) → STOP; a human must slice/contract it first
       (`/kuru:slice`). This loop never creates contracts.
     - `reason: "waiting_on_deps"` → STOP; its `depends_on` aren't all `done`. Tell the user to
       finish those first (e.g. `/kuru:loop <dep>` each, or `/kuru:loop`).
     - `reason: "blocked"` → STOP; the slice is `blocked` and needs a human.
     - `reason: "done"` → nothing to do; report it already shipped.

## The loop

Repeat until a stop condition fires. **In scoped mode, everywhere below replace `kuru next`
with `kuru next --slice <id>` and act only on what it returns for the named slice** — the moment
that slice reaches `done` you go to **Termination** (you never touch another slice).

1. Re-derive state from files (do not trust earlier chat): run
   `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next` — or, in scoped mode,
   `... next --slice <id>` (both already skip dependency-blocked slices, so just act on what is
   returned). For an unattended run outside this session, use the board runner
   (`python3 -m board run --backend claude`, or `--slices <id>` to scope it) instead.
2. If it prints **"No actionable slices"** (board mode), or returns `next_action: "none"` with
   `reason: "done"` (scoped mode — the slice shipped) → go to **Termination**. In scoped mode a
   `"none"` with `reason: "blocked"`/`"waiting_on_deps"` → STOP and surface it (see Preconditions).
3. If the next slice is in status `draft` → STOP (a human must slice/contract it;
   point to `/kuru:slice`). The loop never creates contracts.
4. Otherwise act on its status, using the **same behavior as the matching command**:

   | status | action |
   |---|---|
   | `ready` / `in_progress` | **first**, if this slice hasn't passed the pre-build contract check this run, run it (see **Pre-build contract check** below). Only once it's `CONTRACT OK` do you dispatch a **fresh `kuru-builder`** subagent (as `/kuru:build`) on exactly that slice. |
   | `rejected` | dispatch a **fresh `kuru-builder`**, passing the verifier's rejection note so it fixes the named failures. |
   | `built` | set `built → verifying`, then dispatch a **fresh `kuru-verifier`** subagent (as `/kuru:verify`). |
   | `verifying` | a verification was claimed but not finished (e.g. a prior session died) → dispatch a **fresh `kuru-verifier`** (as `/kuru:verify`); no status change needed. |
   | `verified` — action `review` (review **on**) | dispatch a **fresh `kuru-reviewer`** (as `/kuru:review <id>`), distinct from the builder/verifier. A clean review records `reviewed`; a rejection records `rejected` — which routes the slice **back to build as the next try** (it counts toward `max-tries`, exactly like a verify rejection). |
   | `verified` — action `ship` (review **off**) | **ship it** — `set-status <id> done` (this auto-commits the slice: code + `.kuru/` artifacts + ledger, as one commit). |
   | `reviewed` | ship it — `set-status <id> done` (auto-commits, as above). |

5. After a build or verify, read `kuru show <id>`. If it left the slice **`blocked`**
   (the builder gave up / gates wouldn't go green) or **`verifying`** with no recorded
   verdict, that's a **failed try**, not a stop: if the slice is still under its
   `max-tries` budget, reset it to buildable (`blocked` → `in_progress`, `verifying` →
   `rejected`) and loop to rebuild it with a **fresh** builder. `kuru next` won't hand you
   a `blocked` slice on its own, so you must do this reset yourself — otherwise a failed
   build ends the run at one attempt. Only once the try budget is spent do you leave it
   `blocked` and stop (see the cap guardrail).
6. After each transition, briefly note progress, then loop.

### Pre-build contract check (advisory, before the first build of a slice)

Before a `ready` slice is built for the first time **this run**, gate it through the
**kuru-contract-critic** (as `/kuru:check-contract <id>`) — this catches a contract no
build could satisfy (an AC nothing builds, or one unverifiable in this environment)
*before* a build→verify loop is wasted on it.

- **CONTRACT OK** → mark it checked for this run and proceed to build.
- **CONTRACT FLAGGED** → run the **contract-repair cycle** instead of building:
  1. `set-status <id> draft`, then dispatch a **fresh `kuru-planner`** subagent with the
     critic's flags (and `contract-review.md`) to rewrite `contract.yml`/`slice.md` so
     every AC is satisfiable and verifiable in this environment. The planner re-freezes:
     `set-status <id> ready`.
  2. Re-run the critic. If `CONTRACT OK`, proceed to build; if still flagged, repeat.
  3. **Cap it with `max-tries`** (the same per-run budget): count each repair
     attempt for the slice. If it can't reach `CONTRACT OK` within the cap, STOP —
     `set-status <id> blocked --note "contract un-satisfiable after N repair attempts:
     <last flags>"` — and hand to a human. Never re-slice forever.

The critic is **advisory** — it never changes status or edits the contract itself; the
planner does the repair, the engine records the `draft→ready` transitions.

### Hard guardrails (these are the point — do not skip)

- **Builder ≠ verifier, every time.** Each build and each verify must be a
  **separate subagent invocation** with its own context. Never let the agent that
  implemented a slice also verify it — the independence is the whole reason this
  works. The engine refuses `verified --by builder`, but you must also not reuse
  the builder's context to verify.
- **A try is a full `build → verify → review` cycle; cap tries, per run.** Keep a
  this-run tally per slice, starting at 0 when the loop starts, and count a try at the
  **build** that starts each cycle — so the budget bounds build→verify(→review) cycles,
  **not just verify rejections**. Any failed cycle consumes a try: a verify that
  **rejects**, a **review that rejects** (when review is on), a build that goes
  **blocked**, or a verify that records **no verdict**. Review is part of the same try —
  a review rejection rebuilds and does not get its own separate budget. Do **not** read
  the slice's lifetime `rejections` from `show` — the budget is per run, so a re-run gets
  a fresh one. When a slice's this-run tally reaches `max-tries`, STOP: `set-status <id>
  blocked --note "exhausted N build→verify tries this run: <last failure>"` and hand to a
  human. Do not spin forever.
- **Check the contract before building, repair before looping.** Don't dispatch a
  builder on a `ready` slice that hasn't gone `CONTRACT OK` this run. A flagged contract
  is repaired by the **planner** (a fresh subagent, distinct from builder/verifier) via
  the `draft→ready` cycle above — never by the critic, the builder, or hand-editing
  `contract.yml`. Contract-repair attempts count toward `max-tries`.
- **A mid-run `blocked` build is a failed try — retry it; a slice blocked at the start of
  the run is left for a human.** If a **builder or verifier** sets a slice `blocked`
  **during this run** (gates that wouldn't go green, low context), that's a failed try,
  not a stop: while the slice is under its `max-tries` budget, reset it to buildable
  (`blocked` → `in_progress`) and dispatch a **fresh** builder (step 5); only once the
  budget is spent do you leave it `blocked` and surface it. A slice that was **already
  `blocked` before the loop started** (a human's deliberate park) is **not** auto-retried —
  `kuru next` never returns it, so leave it and report it at termination. The
  contract-repair cap above is its own hard stop (a contract that won't converge stays
  `blocked`). Never fabricate progress to route around a block, and never `set-status` a
  slice `built`/`verified` yourself — only a builder/verifier subagent earns those.
- **Never fabricate progress.** You only ever change status through `kuru.py`; you
  never hand-edit `ledger.json`/`gate-results.json`. The engine's gate + role rules
  stand — if they refuse a transition, that is a real signal, not an obstacle.

## Termination

Stop when any of these is true; in every case, **update `.kuru/progress.md`** (current
state, what the loop did, the single next action) before reporting:

- **Whole-board mode:** `kuru next` reports no actionable slices **and** `ls --status blocked`
  is empty → success: every slice is `done`. Summarize the run.
- **Scoped mode:** the named slice reached `done` → success. Report that it shipped, and what
  `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next` would pick up next, so the
  user can re-run (`/kuru:loop <next>` or `/kuru:loop`) when ready.
- A slice is `blocked`, or the retry cap was hit, or a `draft`/contract gap
  appeared → STOP and tell the user exactly what needs a human and which command to
  run.

Give the user a short end-of-run briefing: how many slices reached `done`, anything
blocked and why, and the next action.
