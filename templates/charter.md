# Charter — {{PROJECT}}

_Generated {{DATE}}. This is the shared-understanding document. It precedes any
spec. Keep it short and honest; unknowns belong in "Open questions", not guessed._

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
- **Verification access:** <!-- HOW a verifier reaches the running system + its dependencies to obtain evidence, and what it must NOT assume. This shapes the KIND of test that can pass here — get it wrong and the verifier builds tests that can't run. E.g. "deps (mongo, kafka) run in-cluster and are NOT reachable from outside; verify by exec'ing into the app pod, not via an external integration test." Folds into the resolved profile's `environment.verification_access`, which `kuru env <id>` reports to the builder/verifier. -->
- **Air-gapped / restricted constraints:** <!-- internal registries (.npmrc / settings.xml / init.gradle / .cargo/config.toml), offline build flags, no internet during build. Be specific — the builder must conform. Endpoints may be left as "TBD — to provide" if the user is supplying them later. -->
- **Reference template / project:** <!-- path or repo to copy build config, registry settings, and layout from, or "none" -->

## Required tooling / conventions
<!-- Org-specific "how we build here" rules that are NOT gate commands: skills,
generators, or reference setups a builder MUST use, each paired with the CHECKABLE
artifact it produces. These flow from profile.conventions (confirmed with the user)
and get turned into concrete acceptance criteria during slicing. State the outcome,
not just the means — the harness enforces "the catalog file exists / the offline
build passes", never "the agent used skill X". "none" if there are no such rules.
  - **<rule>** -> verify: <checkable artifact>
    e.g. **Generate Gradle build files with the `setup-gradle` skill** -> verify:
    gradle/libs.versions.toml pins versions via the catalog; ./gradlew --offline assemble exits 0 -->

## Constraints
<!-- Non-technical constraints: integrations we must support, compliance regimes
(SOC2 / HIPAA / GDPR / PCI), performance/SLO targets, hard deadlines, budget.
(Tech stack lives in Technical environment above.) -->

## Non-goals
<!-- What we are explicitly NOT doing. This is as important as the goals. -->

## Open questions
<!-- Anything undecided. RESOLVE these with the user before moving to the spec/slicing —
they are a gate, not a footnote. As each is answered, fold the answer into the
section above and delete it here. Only items the user explicitly agrees are
non-blocking may remain, marked: `DEFERRED (non-blocking): <why>`. -->
- _none — or list, each resolved or explicitly DEFERRED before slicing_
