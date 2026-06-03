---
description: Decompose a PRD into vertical slices with frozen contracts.
argument-hint: "<feature-name>"
---

Use the `slicing-work` skill.

Read `.kuru/prd/$ARGUMENTS.md`. Propose a set of **vertical** slices — each one
observable behavior, session-sized, carrying its context inline, with checkable
acceptance criteria, sequenced as a walking skeleton first. Show the proposed
boundaries and acceptance criteria to the user and refine before materializing.

For each agreed slice:
1. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" new-slice "<title>" --epic $ARGUMENTS`
   (add `--depends-on SL-000X,SL-000Y` for any slice that must wait on earlier
   ones — the engine then refuses to start it until those are `done`, and orders
   the loop safely. Sequence the walking-skeleton slice first, with no deps.)
2. Fill its `slice.md` (goal, why-one-slice, inline context, in/out of scope,
   dependencies, numbered acceptance criteria, gates).
3. Fill its `contract.yml` (done_definition, acceptance_criteria with
   evidence_required, gates, out_of_scope) and set `frozen: true`.
4. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" set-status <id> ready --note "contract frozen"`

Finish with `kuru ls` and tell the user to run `/kuru:build`.
