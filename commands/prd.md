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

Write it to `.kuru/prd/$ARGUMENTS.md`. When done, show the user the open questions
the planner surfaced and ask them to resolve any blockers, then point to
`/kuru:slice $ARGUMENTS`.
