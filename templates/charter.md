# Charter — {{PROJECT}}

_Generated {{DATE}}. This is the shared-understanding document. It precedes any
PRD. Keep it short and honest; unknowns belong in "Open questions", not guessed._

## Problem
<!-- What problem are we solving, for whom, in one or two paragraphs. What is
painful today? What happens if we do nothing? -->

## Who it's for / stakeholders
<!-- Primary users, secondary users, and the people who must sign off
(eng, product, security, compliance, ops). -->

## Why now
<!-- The forcing function: deadline, contract, regulatory date, dependency. -->

## Success metrics (measurable)
<!-- How we will KNOW this worked. Numbers where possible (adoption, latency,
error rate, conversion, cost). "Better UX" is not a metric. -->

## Technical environment
<!-- This drives .kuru/config.json (the gate commands). Fill in concretely. -->
- **Language & version:** <!-- e.g. Kotlin 2.0 / JDK 21 target; TypeScript 5.x / Node 20; Go 1.22; Rust 1.78 -->
- **Build pipeline / tool:** <!-- npm | pnpm | gradle | maven | go | cargo — selects the config preset -->
- **Gate commands:** <!-- typecheck / lint / test / build as they actually run here; mirror these into config.json -->
- **Deploy environment:** <!-- Kubernetes | Docker | VM | serverless -->
- **Deployment artifacts:** <!-- if k8s: Helm charts | raw YAML; the container registry -->
- **Air-gapped / restricted constraints:** <!-- internal registries (.npmrc / settings.xml / init.gradle / .cargo/config.toml), offline build flags, no internet during build. Be specific — the builder must conform. -->
- **Reference template / project:** <!-- path or repo to copy build config, registry settings, and layout from, or "none" -->

## Constraints
<!-- Non-technical constraints: integrations we must support, compliance regimes
(SOC2 / HIPAA / GDPR / PCI), performance/SLO targets, hard deadlines, budget.
(Tech stack lives in Technical environment above.) -->

## Non-goals
<!-- What we are explicitly NOT doing. This is as important as the goals. -->

## Open questions
<!-- Anything undecided. RESOLVE these with the user before moving to the PRD/slicing —
they are a gate, not a footnote. As each is answered, fold the answer into the
section above and delete it here. Only items the user explicitly agrees are
non-blocking may remain, marked: `DEFERRED (non-blocking): <why>`. -->
- _none — or list, each resolved or explicitly DEFERRED before slicing_
