---
description: Independently verify a built slice against its frozen contract.
argument-hint: "[slice-id]"
---

Use the `verifying-a-slice` skill for context.

Resolve the target: `$ARGUMENTS` if given, else the first slice in status `built`
(`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" ls --status built`).

Dispatch the **kuru-verifier** subagent — this MUST be a separate agent from the
one that built the slice; that independence is the whole point. The verifier:
- reads `contract.yml` first,
- re-runs `kuru gate <id>` itself,
- obtains **concrete observed evidence** for every acceptance criterion (running
  named tests, driving the running app, inspecting state/logs),
- writes `verification.md`,
- sets `verified --by verifier` (all pass + gates green) or `rejected --by
  verifier` with specifics.

When it returns, summarize the verdict and evidence. If `rejected`, point to
`/kuru:build <id>` to resume. If `verified`, point to `/kuru:review <id>`.
