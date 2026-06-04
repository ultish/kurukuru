---
description: Turn the charter into a production PRD for a feature/epic.
argument-hint: "<feature-name>"
---

Use the `writing-prds` skill.

Target feature: **$ARGUMENTS** (if empty, ask which feature).

Read `.kuru/charter.md` first. Then dispatch the **kuru-planner** subagent to
draft the PRD, grounded in the actual codebase. The PRD must cover problem &
user, measurable success criteria, non-goals, functional requirements, the
applicable **non-functional** requirements (security/authz, privacy & audit,
reliability/failure modes, performance/SLOs, observability, a11y/i18n,
migration/rollout), data & interface deltas, dependencies & risks, and an explicit
**acceptance shape**.

Write it to `.kuru/prd/$ARGUMENTS.md`.

**Gate: resolve open questions before slicing.** When the planner returns, walk the
user through **every** open question it surfaced — ask them directly (use
`AskUserQuestion` for discrete choices). Fold each answer back into the PRD (and, if
it's a charter-level gap, update `.kuru/charter.md` too) and clear it from the Open
questions list. If a question is genuinely out of scope for now, keep it only with
the user's explicit agreement, marked `DEFERRED (non-blocking): <why>`.

Do **not** point the user to `/kuru:slice` while any blocking open question is
unresolved — slicing on top of unanswered questions bakes guesses into frozen
contracts. Only once Open questions are answered (or explicitly deferred) tell them
to run `/kuru:slice $ARGUMENTS`.
