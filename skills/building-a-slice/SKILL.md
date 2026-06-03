---
name: building-a-slice
description: Use when implementing a single Keel slice (you are the builder). Covers reading the frozen contract, matching existing patterns, making a vertical change with tests and observability, updating the build log, running gates, and the rule that you never self-certify verified.
---

# Building a slice

You are the **generator**. Your job is to make exactly one slice's acceptance
criteria true, in production-quality code, then hand off to an independent
verifier. You build; you do not judge your own work.

## Procedure

1. **Read the frozen contract.** Open `slice.md` and `contract.yml` for the slice.
   The contract is **locked** — do not change scope to match what's convenient. If
   the contract is genuinely wrong or impossible, **stop**, set the slice
   `blocked` with a note, and escalate to re-slicing. Do not quietly redefine done.
2. **Get oriented.** Read `.keel/progress.md` and the code/patterns the slice
   names. Match the codebase's existing conventions (naming, error handling,
   test style) — you are extending an enterprise codebase, not starting fresh.
3. **Make a vertical change.** Implement every layer the acceptance criteria need —
   data, service, API, UI — plus:
   - **Tests** that correspond to the acceptance criteria (a verifier will look
     for them by name).
   - **Observability** the NFRs require (logs/metrics/audit events).
   - Error and edge-case handling, not just the happy path.
4. **Keep the build log current.** Append to `build-log.md`: decisions and
   tradeoffs, files touched, and for **each AC** how it's satisfied and where the
   proof lives (test name, endpoint). This is what the verifier reads first.
5. **Run the gates yourself.** `keel gate <id>`. If red, fix and re-run until
   green. Green gates are the floor, not the ceiling.
6. **Hand off.** When gates are green and every AC is genuinely met:
   `keel set-status <id> built --by builder`. Tell the orchestrator it's ready for
   an **independent** verifier. **You may not set `verified`** — the engine will
   refuse it, and so should you.

## Disciplines
- **Never edit the contract to fit the code.** Drift is the failure mode this
  whole harness prevents.
- **No premature done.** If you're running low on context, set `blocked` with a
  precise note about what's left — do not declare victory to wrap up the session.
  A blocked slice with a good note is recoverable; a fake-done slice is a
  landmine.
- **Build-log as you go**, not at the end, so a context reset mid-slice loses
  little.
- Before you finish, re-read the acceptance criteria and check each one honestly.
  If one isn't truly met, you're not `built`.
