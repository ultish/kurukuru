---
description: Show the Kurukuru delivery dashboard.
---

Use the `kuru-method` skill for context.

Run and present:
- `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls`
- `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next --all`
- `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" doctor`

`next --all` is the dependency-aware view: it groups every slice as **actionable now**
(each with its next action — build / verify / ship — and the `(deps: …)` it was gated
behind), **waiting on dependencies** (`<id> <- <unmet deps>`), **draft**, or **blocked**.
Present those **dependency chains** — which slice is waiting on which — not just a flat
list, so the reader (or an automated driver like `/kuru:loop-workflow`) can see what can
run in parallel now and what is still gated.

Then give the user a short read of the board:
- What is actionable right now, and what each waiting slice is gated on (the chains above).
- What is awaiting verification (`built`) or rejected and needs a builder.
- What is `blocked` and why (check each blocked slice's latest history note).
- Any slice whose `gate-results.json` shows a failing last run.
- The single most important next action.

For each `blocked` slice, also tell the user how to unblock it once the cause is
resolved — `blocked` can exit to any status, usually back to where it was:
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-status <id> <status> --note "unblocked: <why>"`
(e.g. back to `ready`/`in_progress`, or `dropped` if it should be retired).

Keep it to a tight summary, not a dump.
