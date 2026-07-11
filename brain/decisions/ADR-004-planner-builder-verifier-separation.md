# ADR-004: Planner / Builder / Verifier Role Separation

date: 2026-06-21
status: accepted
deciders: [[team:jxhui]]

## Context

AI coding agents are capable implementers but poor self-evaluators. When the same
agent that writes code is also asked to verify it, it tends to confirm its own
assumptions, rationalise gaps, and produce optimistic verdicts — the same failure
mode that makes human code review necessary in the first place. In a multi-session
pipeline where each session starts cold, an agent that "verifies" its own work has
no external check on whether the implementation actually satisfies the contract.

This problem was identified and documented in Anthropic's harness design guidance:
the planner → generator → evaluator separation (described as a GAN-inspired
architecture) is the single biggest quality lever available in multi-agent systems.
Separating the agent doing work from the agent judging it provides meaningful
independent gating that a self-verifying agent cannot.

A secondary problem: a planner agent that also implements code is incentivised to
scope things it can build rather than things that should be built. Keeping planning
separate preserves the integrity of the spec and contract.

## Decision

Three permanently separated roles, each a distinct subagent with different tool
allowlists:

- **`kuru-planner`** — expands charters into specs and specs into candidate slice
  boundaries. Has `Write`/`Edit` to produce artifacts. Never implements code.
- **`kuru-builder`** — implements exactly one slice vertically. Has `Write`/`Edit`.
  The engine hard-blocks `set-status verified --by builder` — the builder cannot
  self-certify under any circumstances.
- **`kuru-verifier`** — adversarial, read-only gatekeeper. Tool allowlist excludes
  `Write`/`Edit` of source entirely (`Read, Grep, Glob, Bash, Skill,
  mcp__playwright`). It judges; it does not fix. It re-runs gates independently,
  exercises the running system, and cites concrete evidence per acceptance criterion.

These are enforced in code, not by convention:
- `kuru set-status <id> verified --by builder` is refused by the engine.
- `kuru set-status <id> reviewed --by builder` is refused by the engine.
- The verifier's tool allowlist is explicit — omitting it would inherit all tools
  including `Write`/`Edit`, silently breaking the guarantee.

## Consequences

- Every slice gets an independent quality gate that the builder cannot influence or
  bypass. The verifier either passes on observed evidence or rejects with specific
  findings.
- The builder cannot "help" the verifier by fixing things mid-verification — the
  verifier has no write tools and operates from a clean context.
- If a slice is rejected, the specific failure is documented in `verification.md`
  before it routes back to the builder, creating an explicit improvement loop.
- Three separate subagent files to maintain (`agents/kuru-planner.md`,
  `kuru-builder.md`, `kuru-verifier.md`). Each must carry explicit instructions
  about what it is not allowed to do, since each starts cold with no memory of the
  others.
- The verifier tool allowlist must be kept explicit and audited when tool sets
  change — an accidental `Write`/`Edit` inclusion would silently weaken the role
  separation.

## Alternatives considered

**Single agent with a "switch roles" prompt.** Rejected — the same agent switching
roles in one context window is not independent verification; it is the same cognitive
process relabelled. The bias to confirm one's own work is not overcome by a prompt.

**Two agents (builder + verifier), no separate planner.** Evaluated — would remove
the incentive problem in scoping but was rejected because it still couples planning
and implementation in one agent. The planner's role in producing a grounded spec
(reading actual code, flagging gaps as open questions rather than invented requirements)
warrants its own separation.

**Verifier with write access, constrained by instruction only.** Rejected — "don't
edit source" as a system-prompt instruction is weaker than "you don't have the tool."
A verifier that encounters a trivial fix may edit it out of helpfulness, invalidating
the independence of the check. The tool allowlist enforces the constraint at the
capability level.

## See also

- [[adr:ADR-001-code-review-is-opt-in]] — review is a third optional check layered on top of verification
