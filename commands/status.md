---
description: Show the Kurukuru delivery dashboard.
---

Use the `kuru-method` skill for context.

Run and present:
- `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls`
- `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" next`
- `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" doctor`

Then give the user a short read of the board:
- What is awaiting verification (`built`) or rejected and needs a builder.
- What is `blocked` and why (check each blocked slice's latest history note).
- Any slice whose `gate-results.json` shows a failing last run.
- The single most important next action (from `next`).

Keep it to a tight summary, not a dump.
