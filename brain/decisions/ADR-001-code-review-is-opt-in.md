# ADR-001: Code Review is Opt-In

date: 2026-06-21
status: accepted
deciders: [[team:jxhui]]

## Context

The original pipeline had code review as a **mandatory** step between `verified` and
`done`. Every slice had to pass through `verified → reviewed → done`, which meant
`/kuru:loop` and `runner.py` spawned a reviewer agent for every slice, regardless
of whether the change warranted a closer look. This added latency and agent overhead
to routine slices (small changes, config updates, boilerplate) where the verifier's
evidence-backed pass was already sufficient confidence. It also meant the loop could
not autonomously ship anything — human or reviewer-agent involvement was always required.

## Decision

Code review is opt-in. The state machine allows `verified → done` directly.
`/kuru:loop` and `runner.py` ship a verified slice straight to `done` (via an inline
`ship` action) without spawning a reviewer. Run `/kuru:review <id>` by hand on the
slices that warrant a closer look.

The `reviewed` status and the `verified → reviewed → done` detour remain fully
supported — this decision removes the mandate, not the capability.

A review that rejects a slice uses `verified → rejected --by reviewer`, routing it
back to the builder. Review rejections count toward the `--max-retries` retry cap
exactly like verifier rejections.

## Consequences

- The autonomous loop (`/kuru:loop`, `runner.py`) can now ship slices end-to-end
  without human review intervention on every slice.
- Engineers decide per-slice whether to invoke `/kuru:review` — typically for
  non-trivial logic changes, security-sensitive code, or public API surface.
- The `reviewed` status still exists and is meaningful; it just isn't on the hot path.
- A `reviewed`-but-unshipped slice is surfaced by `kuru next` so it can't be stranded.
- Slightly less review coverage as a tradeoff for speed. Mitigated by the verifier's
  evidence requirements (the verifier already exercises the running system and cites
  concrete evidence per acceptance criterion — it is not a rubber stamp).

## Alternatives considered

**Mandatory review for every slice (original design).** Rejected because it forced
a reviewer-agent spawn on every slice, blocking autonomous operation and adding
overhead to routine changes. The verifier's adversarial, evidence-backed verdict was
already providing meaningful quality gating; mandatory review on top was duplication
for most slices.

**Reviewer as a separate non-blocking async step.** Not evaluated; the current
opt-in model achieves the same effect without adding a new async coordination
mechanism.

## See also
