---
description: Code-review a verified slice and mark it reviewed (or reject it back to the builder). Review is on by default; the loop runs it on every verified slice — run this by hand for one-offs or when review is off.
argument-hint: "[slice-id]"
---

Use the `reviewing-a-slice` skill for methodology (and `kuru-method` for context).

**Code review is on by default** (`kuru init` seeds it on), so `/kuru:loop` and
`/kuru:loop-workflow` already route each verified slice through this step before ship.
Run this command **by hand** when you want to review a specific slice yourself, or when
the workspace has review **off** (`kuru set-review off` — then a verified slice ships
straight to `done`) and this one still warrants a closer look. Toggle the policy with
`kuru set-review on|off`.

Resolve the target: `$ARGUMENTS` if given, else the first slice in status
`verified` (`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls --status verified`).

**If the target is already `reviewed`** (reviewed in a prior session but not yet
shipped), don't re-review — just mark it done once it's merged/shipped per your
process:
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-status <id> done` — and stop here.
(Marking a slice `done` auto-commits the working tree as one slice-sized commit.)

A `verified` slice reaches here after independent verification — the spec axis is
already settled, so review is the **quality axis** (see `reviewing-a-slice` for the
standards-source hierarchy and the code-smell baseline). Run the project's code
review on this slice's diff at high effort — invoke `/code-review high` (or the
repo's review skill) scoped to the files the slice touched (see its `build-log.md`).
Apply the repo's documented conventions first, skip anything the gates already
enforce, and reserve findings for design, naming, duplication, correctness, and
security.

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
