---
description: Autonomously run the build→verify→done loop over ready slices until the board is clear (code review is opt-in). Requires charter + PRD + frozen slices to already exist.
argument-hint: "[max-reject-retries, default 2]"
---

Use the `kuru-method` skill for context.

This is the **optional autonomous driver** for the mechanical part of the pipeline.
The judgment-heavy phases (`/kuru:charter`, `/kuru:prd`, `/kuru:slice`) are done by a
human first; once every slice has a **frozen contract**, the per-slice
build → verify → done cycle is deterministic enough to loop. **Code review is
opt-in** — the loop ships a verified slice straight to `done`; run `/kuru:review
<id>` by hand on the slices that warrant a closer look. The manual `/kuru:*`
commands still work — this just runs them for you, in `kuru next` order, until
there is nothing left to do.

`max-reject-retries` (from `$ARGUMENTS`, default **2**) caps how many times a single
slice may be rejected/sent-back before the loop stops and asks for a human.

## Preconditions — refuse to start unless ALL hold

Run these checks first; if any fails, STOP and tell the user exactly which command
to run instead. Do **not** start charter/PRD/slicing yourself — those need a human.

1. Workspace healthy: `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" doctor`.
2. A charter exists: `.kuru/charter.md` is present and filled in (not the empty
   template). If missing/empty → STOP, point to `/kuru:charter`.
3. At least one PRD exists under `.kuru/prd/`. If none → STOP, point to `/kuru:prd`.
4. Slices exist and are **contracted**:
   - `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls` shows ≥1 slice.
   - `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls --status draft` shows
     **none**. A `draft` slice still needs human slicing/contracting → STOP and
     point to `/kuru:slice` to finish (and freeze) it first.

## The loop

Repeat until a stop condition fires:

1. Re-derive state from files (do not trust earlier chat): run
   `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next` (it already skips
   dependency-blocked slices, so just act on what it returns). For an unattended
   run outside Claude, use the top-level `runner.py` instead — same logic in plain Python.
2. If it prints **"No actionable slices"** → go to **Termination**.
3. If the next slice is in status `draft` → STOP (a human must slice/contract it;
   point to `/kuru:slice`). The loop never creates contracts.
4. Otherwise act on its status, using the **same behavior as the matching command**:

   | status | action |
   |---|---|
   | `ready` / `in_progress` | dispatch a **fresh `kuru-builder`** subagent (as `/kuru:build`) on exactly that slice. |
   | `rejected` | dispatch a **fresh `kuru-builder`**, passing the verifier's rejection note so it fixes the named failures. |
   | `built` | set `built → verifying`, then dispatch a **fresh `kuru-verifier`** subagent (as `/kuru:verify`). |
   | `verified` | **ship it** — `set-status <id> done`. Code review is opt-in and the loop does **not** run it. (Slices you want reviewed: run `/kuru:review <id>` by hand before/instead of looping, or pause the loop for them.) |
   | `reviewed` | reviewed by hand in a prior session but not shipped → `set-status <id> done`. |

5. After each transition, briefly note progress, then loop.

### Hard guardrails (these are the point — do not skip)

- **Builder ≠ verifier, every time.** Each build and each verify must be a
  **separate subagent invocation** with its own context. Never let the agent that
  implemented a slice also verify it — the independence is the whole reason this
  works. The engine refuses `verified --by builder`, but you must also not reuse
  the builder's context to verify.
- **Cap the send-back cycle.** Before (re)building a `rejected` slice, read its
  history (`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" show <id>`) and count how
  many times it has been `rejected` (by the verifier, or by a manual review). If that count ≥
  `max-reject-retries`, STOP: `set-status <id> blocked --note "exceeded N
  build/verify retries: <last failure>"` and hand to a human. Do not spin forever.
- **`blocked` means stop, not skip.** If a builder or verifier sets a slice
  `blocked` (wrong/impossible contract, gates that genuinely can't go green), do
  **not** route around it — STOP and surface it. "Nothing actionable" while a slice
  is `blocked` is a failure, not success.
- **Never fabricate progress.** You only ever change status through `kuru.py`; you
  never hand-edit `ledger.json`/`gate-results.json`. The engine's gate + role rules
  stand — if they refuse a transition, that is a real signal, not an obstacle.

## Termination

Stop when any of these is true; in every case, **update `.kuru/progress.md`** (current
state, what the loop did, the single next action) before reporting:

- `kuru next` reports no actionable slices **and** `ls --status blocked` is empty →
  success: every slice is `done`. Summarize the run.
- A slice is `blocked`, or the retry cap was hit, or a `draft`/contract gap
  appeared → STOP and tell the user exactly what needs a human and which command to
  run.

Give the user a short end-of-run briefing: how many slices reached `done`, anything
blocked and why, and the next action.
