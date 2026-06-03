---
description: Get your bearings at the start of a session (recover from a context reset).
---

Use the `kuru-method` skill for context.

This is the **session-startup ritual** — the antidote to context-reset amnesia.
Do NOT rely on anything from earlier chat; reconstruct state from files:

1. Read `.kuru/progress.md` (current state, last session, the stated next action,
   known landmines, how to run/verify).
2. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" doctor` — confirm the workspace
   is healthy.
3. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" ls` and `... next`.
4. `git log --oneline -15` and `git status` to see recent work and any
   uncommitted changes.
5. For the slice `next` points at, read its `slice.md` / `contract.yml` and its
   latest history note.

Then give the user a 4-6 line briefing: where the project stands, what the last
session did, anything broken/blocked, and **the single next action** with the
exact command to take it. Do not start building until the user confirms.
