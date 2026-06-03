---
description: Run a discovery session and write the shared-understanding charter.
argument-hint: "[optional: feature/topic to focus the discovery]"
---

Use the `keel-method` skill for context.

First ensure a Keel workspace exists. If there is no `.keel/` directory in the
repo, tell the user and offer to run:
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/keel.py" init`

Then run a **discovery conversation** with the user to build shared understanding
BEFORE any PRD. Focus: $ARGUMENTS

Interview for, and do not assume:
- The problem and who has it; what it costs today.
- Stakeholders and sign-offs (eng, product, security, compliance, ops).
- Why now (the forcing function).
- **Measurable** success metrics.
- Constraints (stack, integrations, compliance regime, SLOs, deadlines, budget).
- Non-goals.
- Open questions.

Ask follow-ups where answers are vague — a charter full of guesses is worthless.
When you have enough, write/update `.keel/charter.md` (use its template sections),
then summarize the shared understanding back to the user and point them to
`/keel:prd <feature>` as the next step.
