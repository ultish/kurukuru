# ADR-005: Frozen Contracts — Done Defined Before Code is Written

date: 2026-06-21
status: accepted
deciders: [[team:jxhui]]

## Context

When an AI coding agent implements a feature without a fixed definition of done,
scope drifts in two directions: the agent either under-delivers (stops when it
runs out of obvious things to do) or over-delivers (adds polish, refactors
adjacent code, or interprets the intent generously). In both cases the verifier
has no stable target to check against, and the builder has no clear signal for
when to stop.

In a multi-session pipeline with context resets, the problem compounds: a builder
in session two has no memory of what the builder in session one committed to. If
the definition of done lives in conversation history or in the builder's narration
("I'll add auth, pagination, and error handling"), it evaporates at the next reset.

A related problem: without a frozen contract, a builder under pressure (context
running low, gates failing) is incentivised to reinterpret scope downward — quietly
dropping an acceptance criterion and declaring done. The verifier cannot catch this
if it has no authoritative record of what was originally required.

## Decision

Every slice must have a **frozen contract** (`contract.yml`) that is written and
locked at the moment the slice transitions to `ready` — before any implementation
begins. The contract specifies:

- `done_definition` — one or two sentences stating what is unambiguously true when
  the slice is done
- `acceptance_criteria` — numbered, concrete, observable facts (`AC-1`, `AC-2`, …),
  each with a `kind` (`automated | manual | observed`) and an `evidence_required`
  field naming the exact proof the verifier must produce
- `out_of_scope` — explicit list of things this slice deliberately does not do

The contract is **immutable once frozen**. If scope must change:
- Minor clarification: re-`draft` the slice, rewrite the contract, re-`ready` it
  before any build starts
- New requirement: cut a new slice with its own contract

The builder may not edit `contract.yml`. The verifier checks the running system
against the contract as written — it does not negotiate criteria down to make the
slice pass. If the contract itself is wrong, the verifier rejects and escalates to
the planner.

## Consequences

- The builder has a stable, unambiguous target that survives context resets.
- The verifier has a machine-readable checklist to check against, with no ambiguity
  about what counts as passing.
- Scope cannot silently drift — any change is explicit and leaves a trail (a re-draft
  or a new slice id in the ledger).
- "Done" is a fact, not a negotiation. A builder that drops a criterion fails
  verification; a verifier that softens a criterion to pass is violating its role.
- Slicing becomes the highest-leverage planning step: a badly-written acceptance
  criterion (vague, untestable, or wrong) cannot be fixed during build without
  re-drafting. Good slicing discipline is essential — see [[adr:ADR-004-planner-builder-verifier-separation]]
  and the `slicing-work` skill.
- Contracts add upfront cost: writing concrete, testable acceptance criteria before
  touching code takes more thought than starting to implement and seeing what happens.
  This is the point — the cost of a vague criterion is paid during verification, not
  during slicing.

## Alternatives considered

**Loose specification — the builder decides scope as it goes.** Rejected — this is
the default mode of unconstrained coding agents and the exact failure mode kurukuru
exists to prevent. A builder that defines its own done criteria cannot be
independently verified.

**Soft contracts — the builder may update the contract during implementation.**
Rejected — a contract the builder can edit is not a contract; it is a post-hoc
description of what was built. It removes the builder's accountability to the
original scope and makes the verifier's job meaningless.

**Natural-language definition of done in `slice.md`, no structured `contract.yml`.**
Evaluated — `slice.md` carries the human-readable spec, but a verifier reading
prose must interpret whether each criterion is met. A structured `contract.yml`
with explicit `evidence_required` fields forces the acceptance criteria to be
concrete and makes the verifier's checklist mechanical. Both files exist; the
contract is the machine-adjacent truth.

## See also

- [[adr:ADR-004-planner-builder-verifier-separation]] — the verifier's adversarial stance depends on the contract being an authoritative source it did not help write
