---
description: Optionally code-review a verified slice and mark it reviewed. Review is opt-in — the loop ships verified slices without it.
argument-hint: "[slice-id]"
---

Use the `kuru-method` skill for context.

**Code review is opt-in.** A verified slice may ship straight to `done` (that's
what `/kuru:loop` does); run this command by hand on the slices that warrant a
closer look. It is not a required pipeline step.

Resolve the target: `$ARGUMENTS` if given, else the first slice in status
`verified` (`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls --status verified`).

**If the target is already `reviewed`** (reviewed in a prior session but not yet
shipped), don't re-review — just mark it done once it's merged/shipped per your
process:
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-status <id> done` — and stop here.
(Marking a slice `done` auto-commits the working tree as one slice-sized commit.)

A `verified` slice reaches here after independent verification. Run the project's
code review on this slice's diff at high effort — invoke `/code-review high` (or
the repo's review skill) scoped to the files the slice touched (see its
`build-log.md`). Focus on correctness, security, and maintainability, not style
nits the linters already cover.

Summarize findings for the user. If the review is clean (or findings are
addressed), set:
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-status <id> reviewed --by reviewer --note "<summary>"`
then, once merged/shipped per your process,
`... set-status <id> done`.

If the review finds real problems, do NOT mark reviewed — send it back to the
builder by **rejecting** it (the engine allows `verified → rejected`; there is no
`verified → in_progress`):
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-status <id> rejected --by reviewer --note "<what to fix>"`
From `rejected` the slice flows back through `/kuru:build <id>`, and the rejection
counts toward the retry cap like a verifier rejection.
