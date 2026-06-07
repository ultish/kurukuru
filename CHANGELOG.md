# Changelog

All notable changes to **kurukuru** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
