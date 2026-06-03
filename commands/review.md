---
description: Code-review a verified slice and mark it reviewed.
argument-hint: "[slice-id]"
---

Use the `kuru-method` skill for context.

Resolve the target: `$ARGUMENTS` if given, else the first slice in status
`verified` (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" ls --status verified`).

A slice should only reach here after independent verification. Run the project's
code review on this slice's diff at high effort — invoke `/code-review high` (or
the repo's review skill) scoped to the files the slice touched (see its
`build-log.md`). Focus on correctness, security, and maintainability, not style
nits the linters already cover.

Summarize findings for the user. If the review is clean (or findings are
addressed), set:
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" set-status <id> reviewed --by reviewer --note "<summary>"`
then, once merged/shipped per your process,
`... set-status <id> done`.

If the review finds real problems, do NOT mark reviewed — send it back with
`/kuru:build <id>` and note what to fix.
