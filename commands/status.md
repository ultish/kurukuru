---
description: Show the Keel delivery dashboard.
---

Use the `keel-method` skill for context.

Run and present:
- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/keel.py" ls`
- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/keel.py" next`
- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/keel.py" doctor`

Then give the user a short read of the board:
- What is awaiting verification (`built`) or rejected and needs a builder.
- What is `blocked` and why (check each blocked slice's latest history note).
- Any slice whose `gate-results.json` shows a failing last run.
- The single most important next action (from `next`).

Keep it to a tight summary, not a dump.
