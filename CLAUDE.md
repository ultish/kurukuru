# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

**kurukuru** — a Claude Code *plugin* that turns a coding agent into a disciplined
delivery pipeline for production software: `charter → prd → slice → build → verify
→ review → done`. The core thesis: facts that gate progress live in machine-checked
files, never in agent narration. Read `README.md` for the full picture and the
`kuru-method` skill for the methodology. `impl/BUILD_PLAN.md` is the original spec
but is **legacy** — where it and the code disagree, the code (and BUILD_PLAN §9
addendum) win.

## Layout

- `scripts/kuru.py` — the deterministic state + gate engine. **The single source of
  truth.** Only it may mutate `ledger.json` / `gate-results.json`.
- `commands/` — the `/kuru:*` slash commands (thin orchestrators).
- `agents/` — the separated subagents (`kuru-planner`, `kuru-builder`,
  `kuru-verifier`).
- `skills/` — the methodology (deep guidance lives here, not in commands).
- `templates/` — files copied into a target repo's `.kuru/` workspace by `kuru init`.
- `runner.py` — standalone headless driver. **NOT part of the plugin**; it's a thin
  dispatcher over `kuru next --json` + the `/kuru:*` commands.
- `.claude-plugin/` — `plugin.json` + `marketplace.json` manifests.

## Hard constraints (don't break these)

- **Stdlib only.** No third-party Python dependencies, ever. `kuru.py` and
  `runner.py` must run on a clean `python3`.
- **Template filenames are load-bearing.** `kuru.py` reads templates by exact name
  (`config.json`, `charter.md`, `progress.md`, `workspace-readme.md`, `slice.md`,
  `contract.yml`, `build-log.md`, `verification.md`, `init.sh`, `config.<stack>.json`).
  Renaming one without updating `kuru.py` crashes `init`/`new-slice` — that's the
  fastest failure signal.
- **The state machine lives in `kuru.py`** (`STATUSES`, `TRANSITIONS`,
  `STATUS_ACTION`, `pick_next`). If you change it, update every place that mirrors
  it: the diagrams in `README.md` and the `kuru-method` skill, and the command
  prose. They must agree.
- **Keep commands thin, skills deep.** Methodology goes in `skills/`, not duplicated
  into `commands/`.
- **The conventions → `setup-conformance` mechanism is documented in five places,
  deliberately** — each reader sees only one artifact after a context reset (and
  templates ship into target repos without the plugin docs): `commands/charter.md`,
  `skills/slicing-work`, `templates/profile.example.json`, `templates/charter.md`,
  `templates/slice.md`. If you change the mechanism, update all five; they must
  agree.
- **Frontmatter must be valid YAML** in every command/agent/skill file.
- The plugin is the tool; a target repo's `.kuru/` is per-project state — never
  commit a `.kuru/` workspace into this repo.

## Before you finish a change

- Run `scripts/selftest.sh` — it must stay green (exercises the engine's
  guarantees). Add a check when you change engine behavior.
- For command/agent discovery changes, `scripts/smoke-headless.sh` proves `/kuru:*`
  still resolves in a headless `claude -p` session.

## Release checklist (when cutting a version)

**Trigger:** a version bump in `git diff` — whether you wrote it or not — means a
release is being cut. Run this checklist before committing. Ownership of the commit
implies ownership of the checklist; "someone else bumped it" is not an exception.

1. **Update `CHANGELOG.md`** — move the `[Unreleased]` items under a new dated
   version section and start a fresh empty `[Unreleased]`. (Keep a Changelog format.)
2. **Bump the version in all three places** (they must match):
   `.claude-plugin/plugin.json`, and both `version` fields in
   `.claude-plugin/marketplace.json` (top-level and `plugins[0]`).
3. Use SemVer: bug fixes → patch, backward-compatible features → minor, breaking
   changes to the engine / state machine / template contract → major.
