---
description: Build the next ready slice (or a named one) via the builder subagent.
argument-hint: "[slice-id]"
---

Use the `building-a-slice` skill for context.

Resolve the target slice: if `$ARGUMENTS` names a slice id, use it; otherwise run
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next` and pick the first slice in
status `ready` (or `rejected`/`in_progress` to resume). Confirm the choice if
ambiguous.

Then dispatch the **kuru-builder** subagent to implement **exactly that one
slice**. The builder reads the frozen contract, makes a vertical production change
with tests and observability, keeps `build-log.md` current, runs
`kuru gate <id>` until green, and sets status `built`. It will NOT set `verified`.

When the subagent returns, show
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" show <id>` and tell the user to
run `/kuru:verify <id>` with a fresh verifier. Do not verify it yourself in this
same flow — verification must be independent.
