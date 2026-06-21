# Decisions Index

Start here when looking for decisions, ADRs, or architectural choices for this service.
Each entry has enough context to decide whether to read the full file.

| File | Summary | Date |
|------|---------|------|
| [ADR-004-planner-builder-verifier-separation](ADR-004-planner-builder-verifier-separation.md) | Planner/builder/verifier are permanently separated subagents with different tool allowlists; the engine hard-blocks builder self-certification and the verifier has no write tools | 2026-06-04 |
| [ADR-001-code-review-is-opt-in](ADR-001-code-review-is-opt-in.md) | Code review is opt-in — `verified → done` is the default; `/kuru:review` is run by hand on slices that warrant it | 2026-06-16 |
| [ADR-003-stdlib-only-constraint](ADR-003-stdlib-only-constraint.md) | `kuru.py` and `runner.py` use Python stdlib only — no pip, no third-party packages — so the engine runs in air-gapped environments with zero setup | 2026-06-04 |
| [ADR-005-frozen-contracts](ADR-005-frozen-contracts.md) | Contracts are written and locked before implementation begins; the builder cannot edit them, the verifier cannot soften them, and scope changes require an explicit re-draft or a new slice | 2026-06-04 |
| [ADR-002-five-place-documentation-rule](ADR-002-five-place-documentation-rule.md) | The conventions → setup-conformance mechanism is documented in 5 places because templates ship into target repos without plugin docs, and each reader encounters only one artifact after a context reset | 2026-06-11 |

_Use `/brain:adr` to promote any of these into a full ADR file._
