---
description: Print and start the next actionable slice.
---

Use the `keel-method` skill for context.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/keel.py" next`. Based on the returned
status, recommend (and offer to start) the matching command:

- `draft` → `/keel:slice` (it still needs a frozen contract).
- `ready` → `/keel:build <id>`.
- `in_progress` / `rejected` → `/keel:build <id>` (resume).
- `built` → `/keel:verify <id>` (with an independent verifier).
- `verified` → `/keel:review <id>`.

If nothing is actionable, say whether everything is `done`/`blocked` and suggest
`/keel:slice` or unblocking.
