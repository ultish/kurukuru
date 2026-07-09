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
- `verifying` → `/kuru:verify <id>` (a verification was claimed but not finished —
  re-verify with a fresh, independent verifier).
- `verified` → follow the action `kuru next` returns (it reflects this workspace's review policy):
  - action `review` (review **on**, the default) → `/kuru:review <id>` — a fresh reviewer;
    a clean review → `reviewed`, a rejection → `rejected` (back to the builder).
  - action `ship` (review **off**) → ship it: `/kuru:ship <id>` (or `set-status <id> done`),
    which auto-commits the slice.
- `reviewed` → ship it: `/kuru:ship <id>` (or `set-status <id> done`), auto-commits the slice.

If nothing is actionable, say whether everything is `done`/`blocked` and suggest
`/kuru:slice` or unblocking.
