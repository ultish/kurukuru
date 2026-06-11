# Changelog

All notable changes to **kurukuru** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-06-11

### Added
- **`conventions` block in the environment profile.** `profile.example.json` gains a
  first-class `conventions` field: org-specific "how we build here" rules (skills,
  generators, reference setups) that are not gate commands, each pairing a `rule`
  (the means) with a `verify` list (the checkable artifact it produces). `/kuru:charter`
  reads these as guidance, confirms them with the user (pinning down a checkable
  outcome where the profile omits one), and records them in a new **Required tooling /
  conventions** charter section. Previously such rules lived in freeform notes and were
  lost in summarization before reaching the contract.

- **"Outcomes gate, not means" is now a stated discipline.** The `kuru-method` skill
  gains a third non-negotiable discipline: a requirement the engine can't check ("use
  skill X") is only as real as the checkable artifact attached to it. This is the
  thesis behind the convention/gate changes below.

### Changed
- **Required tooling becomes a checkable outcome, not "use skill X".** `slicing-work`
  now turns each convention into the *artifact the tool produces* (e.g. catalog file
  present, `--offline assemble` exits 0) and names the skill in slice context as the
  cheapest path with the consequence of skipping it spelled out. The harness can only
  enforce outcomes, never "the agent invoked skill X", so an ignoring builder is now
  caught rather than trusted. Enforcement is layered by what each artifact admits:
  deterministic facts (a file exists, a string is/isn't present, a command exits 0)
  are **compiled by `/kuru:charter` into a `setup-conformance` gate** in `config.json`
  — a cheap `grep`/`test` assertion that runs on every slice (machine-checked, and a
  free regression guard since these are invariants); judgmental facts stay acceptance
  criteria the verifier checks. No engine change — a gate is just a command, and the
  profile is never executed directly (it informs the gate charter writes).
- **Builder is no longer told it's "extending, not starting fresh."** Both the
  `building-a-slice` skill (step 2) and the `kuru-builder` agent's mirrored rule
  reframe conventions as *adopt, not assert*: match existing conventions where they
  exist, use the slice's named tooling where it doesn't, and treat greenfield/setup
  slices as where this matters *more* — not an exemption. Doubt about named tooling now
  routes to `blocked` + escalate instead of silent improvisation. The old wording read
  as license to "do its own thing" on fresh projects.

## [0.1.3] - 2026-06-07

### Added
- **`/kuru:init` slash command.** Exposes `kuru init` as a first-class `/kuru:init`
  command with support for `--stack`, `--profile`, and `--force` flags. Guards
  against silent overwrites of an existing `.kuru/` workspace and points to next
  steps after scaffolding.

### Changed
- **README: clarified `KURU_PY` setup.** `CLAUDE_PLUGIN_ROOT` is set automatically
  by Claude Code when the plugin loads, so no explicit configuration is needed for
  most users. The `~/.claude/settings.json` `env.KURU_PY` override is now framed as
  an escape hatch for unusual setups (symlinked plugin, etc.), with a note that
  Claude Code has no plugin-scoped env mechanism.

## [0.1.1] - 2026-06-04

### Fixed
- **Code review can now actually reject a slice.** `/kuru:review` and `/kuru:loop`
  used to send a failed review back with `verified → in_progress`, a transition the
  engine refuses — so a review that found real problems had no working path. Review
  send-backs now use the legal `verified → rejected` transition (`--by reviewer`),
  which routes the slice back to the builder via `rejected → in_progress`. A review
  rejection now also counts toward the `--max-retries` retry cap, exactly like a
  verifier rejection.
- **A `reviewed`-but-unshipped slice is no longer invisible.** `kuru next` skipped
  slices stuck in `reviewed`, so a slice reviewed in one session but not yet marked
  `done` could be silently stranded. `next` now surfaces it (and `/kuru:review` on a
  `reviewed` target just marks it `done`).

### Changed
- **Environment profiles are guidance, not gospel.** `kuru init --profile <file>`
  no longer writes the profile's `config` block into `.kuru/config.json` verbatim
  (which `/kuru:charter`'s gate-setup step could then clobber). `init` now seeds
  `config.json` from the profile's `stack` preset (or the node default) and stashes
  the profile at `.kuru/profile.json`. `/kuru:charter` reads it as a starting point:
  it summarizes the profile back to the user, hunts for gaps to confirm, then writes
  the authoritative `config.json` and folds the rest (deploy target, air-gap
  endpoints) into the charter.
- State-machine diagrams (`README.md`, `kuru-method` skill) now show that both the
  verifier (`verifying → rejected`) and code review (`verified → rejected`) send a
  slice back to the builder.
- `runner.py` retry-cap messaging now reflects that a rejection can come from the
  verifier **or** code review.

### Added
- The verifier may take browser screenshots via a **Playwright MCP** when one is
  connected (`mcp__playwright` added to its tool allowlist). When no such server is
  registered the entry resolves to nothing and the verifier falls back to HTTP/API
  evidence — "use it if available." kuru does not bundle the server, keeping the
  plugin stdlib-only and air-gap friendly.
- `/kuru:bearings` now skims the technical environment (charter + `profile.json`) at
  session start, so the stack, deploy target, and air-gap constraints are known
  before any work begins.
- `scripts/selftest.sh` regression coverage for the review-reject path and `reviewed`
  visibility (33 → 67 checks).

## [0.1.0] - 2026-06-04

Initial release of the kurukuru enterprise delivery harness.

### Added
- **The pipeline:** `charter → prd → slice → build → verify → review → done` as
  Claude Code slash commands (`/kuru:*`).
- **Deterministic state + gate engine** (`scripts/kuru.py`, stdlib only): the slice
  state machine and three hard rules enforced in code — illegal transitions refused,
  no `verified` without a recorded green `kuru gate` run, and builders (`--by
  builder`) may not set `verified`/`reviewed`.
- **Separated roles as subagents:** `kuru-planner`, `kuru-builder`, and an
  adversarial read-only `kuru-verifier` (the builder never verifies its own slice).
- **Methodology skills:** `kuru-method`, `writing-prds`, `slicing-work`,
  `building-a-slice`, `verifying-a-slice`.
- **File-based handoffs:** the `.kuru/` workspace (`ledger.json`, `config.json`,
  `charter.md`, `progress.md`, per-slice `slice.md` / `contract.yml` /
  `build-log.md` / `verification.md` / `gate-results.json`) with `kuru init`
  scaffolding and templates.
- **`/kuru:charter`** captures the technical environment and configures the
  `config.json` gates for the project's stack; open questions gate the
  charter → PRD → slice progression.
- **Reusable environment profiles** (`kuru init --profile`) with skippable air-gap
  endpoints.
- **Dependency chains** (`new-slice --depends-on …`): the engine refuses to start a
  slice until its dependencies are `done`, and `next` skips dependency-blocked
  slices.
- **Autonomous drivers:** in-session `/kuru:loop` and the standalone headless
  `runner.py` (fresh `claude -p` per step; builder and verifier are separate
  processes), with retry caps, stall/blocked detection, and precondition gating.
- **Machine-readable state** (`ls|show|next --json`) for external tooling.
- **Stack presets** (`templates/config.<stack>.json` for
  node/pnpm/gradle/maven/go/python/cargo) via `init --stack` / `set-stack`.
- **Robust engine path resolution** (`KURU_PY` → `${CLAUDE_PLUGIN_ROOT}` →
  `.kuru/engine`) and watchable, live-streamed gate logs.
- **Self-checks:** `scripts/selftest.sh` (engine guarantees) and
  `scripts/smoke-headless.sh` (proves `/kuru:*` resolves in a headless session).

[Unreleased]: https://example.com/kurukuru/compare/v0.1.3...HEAD
[0.1.3]: https://example.com/kurukuru/compare/v0.1.1...v0.1.3
[0.1.1]: https://example.com/kurukuru/compare/v0.1.0...v0.1.1
[0.1.0]: https://example.com/kurukuru/releases/tag/v0.1.0
