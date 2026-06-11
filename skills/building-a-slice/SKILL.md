---
name: building-a-slice
description: Use when implementing a single Kurukuru slice (you are the builder). Covers reading the frozen contract, matching existing patterns, making a vertical change with tests and observability, updating the build log, running gates, and the rule that you never self-certify verified.
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
2. **Get oriented.** Read `.kuru/progress.md` and the code/patterns the slice
   names. Conventions are something you **adopt, not assert**: where the codebase
   already has them (naming, error handling, test style), match them instead of
   inventing your own; where the slice context names a tool, skill, or reference
   setup to use, *that* is the convention — use it. This holds **especially** on a
   greenfield or setup slice, where "there's nothing to copy yet" is not license to
   improvise an equivalent because you "know the parameters" — the named tooling
   exists precisely because the details (mirror URLs, plugin versions, layout) are
   easy to get wrong by hand. If the named tooling genuinely seems wrong or
   unnecessary, you don't silently skip it — set the slice `blocked` with a note and
   escalate.
3. **Make a vertical change.** Implement every layer the acceptance criteria need —
   data, service, API, UI — plus:
   - **Tests** that correspond to the acceptance criteria (a verifier will look
     for them by name).
   - **Observability** the NFRs require (logs/metrics/audit events).
   - Error and edge-case handling, not just the happy path.
4. **Keep the build log current.** Append to `build-log.md`: decisions and
   tradeoffs, files touched, and for **each AC** how it's satisfied and where the
   proof lives (test name, endpoint). This is what the verifier reads first.
5. **Run the gates yourself.** `kuru gate <id>`. If red, fix and re-run until
   green. Green gates are the floor, not the ceiling. `kuru gate` streams each
   gate's output live **and** writes it to `.kuru/slices/<id>/gate-<name>.log`, so a
   long build (gradle, etc.) is watchable with `tail -f` and never looks "stuck".
   When you run a long build/test command *outside* the gate, do the same — never
   send its output to `/dev/null`; tee it to a log so progress is visible.
6. **Hand off.** When gates are green and every AC is genuinely met:
   `kuru set-status <id> built --by builder`. Tell the orchestrator it's ready for
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
