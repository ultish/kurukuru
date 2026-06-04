---
description: Print and start the next actionable slice.
---

Use the `kuru-method` skill for context.

Run `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next`. Based on the returned
status, recommend (and offer to start) the matching command:

- `draft` → `/kuru:slice` (it still needs a frozen contract).
- `ready` → `/kuru:build <id>`.
- `in_progress` / `rejected` → `/kuru:build <id>` (resume).
- `built` → `/kuru:verify <id>` (with an independent verifier).
- `verified` → `/kuru:review <id>`.
- `reviewed` → `/kuru:review <id>` (marks it `done` once shipped).

If nothing is actionable, say whether everything is `done`/`blocked` and suggest
`/kuru:slice` or unblocking.
