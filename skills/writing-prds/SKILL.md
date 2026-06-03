---
name: writing-prds
description: Use when turning a charter into a PRD for an enterprise feature or epic. Covers problem framing, measurable success, non-goals, functional and non-functional (production) requirements, and the acceptance shape that slicing will turn into per-slice criteria.
---

# Writing a PRD

A Keel PRD turns the shared understanding in `.keel/charter.md` into a concrete
specification of **what** to build and **why** — never **how**. It is the input
to slicing. Write it for production: a feature that "works on the happy path" is
not done.

## Start from the charter, never a blank page
Read `.keel/charter.md` first. The PRD must trace back to the charter's problem,
users, and success metrics. If the charter doesn't support a requirement, it
doesn't belong here yet — raise it as an open question, don't invent scope.

## Required sections

1. **Problem & user.** Who has the problem, what it costs them, what changes for
   them when this ships. One or two paragraphs.
2. **Success criteria (measurable).** The signals that prove it worked — adoption,
   latency, error rate, conversion, cost. Numbers, not adjectives. These become
   the north star the acceptance criteria ladder up to.
3. **Non-goals.** What this feature explicitly does not do. As load-bearing as the
   goals — it's what keeps slices from sprawling.
4. **Functional requirements.** The behaviors, as user-observable statements.
   Group by user flow. Each should be specific enough to later become a checkable
   acceptance criterion.
5. **Non-functional requirements (production gate).** Include every one that
   applies; omit with a reason, don't omit by forgetting:
   - **Security & authz** — who can do what; authn/authz model; secrets handling.
   - **Privacy & data** — PII, retention, residency, consent, audit logging.
   - **Reliability & failure modes** — what happens on partial failure, retries,
     idempotency, timeouts, degraded mode.
   - **Performance / SLOs** — target latency/throughput, expected scale.
   - **Observability** — the logs, metrics, traces, and alerts this must emit to
     be operable in prod.
   - **Accessibility & i18n** — where user-facing.
   - **Migration / rollout** — backfills, feature flags, staged rollout, kill
     switch, backward compatibility.
6. **Data & interface deltas.** New/changed schemas, APIs, events, contracts.
7. **Dependencies & risks.** Upstream systems, teams, unknowns; each risk with a
   mitigation or an open question.
8. **Acceptance shape.** The *kinds of evidence* that will prove the feature works
   end to end — e.g. "an automated integration test driving the full flow", "a
   driven-app screenshot of the empty/loading/error states", "an audit-log entry
   for every privileged action". Slicing turns these into concrete per-slice
   acceptance criteria, so make them about observable facts.

## Discipline
- Describe **what/why**, leave **how** to the builder — but pin down contracts
  (APIs, schemas) precisely, because those cross slice boundaries.
- Every requirement should be falsifiable. If you can't imagine the evidence that
  would prove it done, rewrite it until you can.
- Surface unknowns as **Open questions** with an owner. A PRD full of confident
  guesses is worse than one honest about what's undecided.

Write the PRD to `.keel/prd/<feature>.md`.
