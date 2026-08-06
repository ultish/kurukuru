# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

**kurukuru** — a Claude Code *plugin* that turns a coding agent into a disciplined
delivery pipeline for production software: `charter → spec → slice → build → verify
→ review → done` (with an advisory `check-contract` step between `slice` and `build`, and a
`review` step before `done` that is **on by default** but toggleable per workspace with
`kuru set-review`). The core thesis: facts that gate progress live in machine-checked
files, never in agent narration. Read `README.md` for the full picture and the
`kuru-method` skill for the methodology. `impl/BUILD_PLAN.md` is the original spec
but is **legacy** — where it and the code disagree, the code (and BUILD_PLAN §9
addendum) win.

## Layout

- `scripts/kuru.py` — the deterministic state + gate engine. **The single source of
  truth.** Only it may mutate `ledger.json` / `gate-results.json`. Path is
  load-bearing (`${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py`); do not relocate.
- `scripts/board.sh`, `board-tui.sh`, `build-tui-rhel9.sh`, `build-tui-macos.sh` —
  production launchers / release builds (see `scripts/README.md`).
- `scripts/test/` — engine/board/smoke self-checks (`selftest.sh`,
  `board-selftest.sh`, `smoke-headless.sh`, `smoke-tui-linux-amd64.sh`).
- `commands/` — the `/kuru:*` slash commands (thin orchestrators).
- `agents/` — the separated subagents (`kuru-planner`, `kuru-builder`,
  `kuru-verifier`).
- `skills/` — the methodology (deep guidance lives here, not in commands).
- `templates/` — files copied into a target repo's `.kuru/` workspace by `kuru init`.
- `board/` — agent-agnostic multi-slice orchestrator (`python3 -m board`).
- `tui/` — Ratatui board UI (`kuru-board-tui`); airgap Linux amd64 builds via
  `scripts/build-tui-rhel9.sh` → `dist/kuru-board-tui-linux-amd64.tar.gz`, and
  native macOS builds via `scripts/build-tui-macos.sh` →
  `dist/kuru-board-tui-macos-<arch>.tar.gz` (both share one merged
  `dist/SHA256SUMS`). Guide: `tui/README.md`.
- `.claude-plugin/` — `plugin.json` + `marketplace.json` manifests.

Headless / multi-slice runs go through `board/` (`python3 -m board run --backend
claude|grok|cmd`); the old single-threaded `runner.py` has been removed.

## Hard constraints (don't break these)

- **Stdlib only.** No third-party Python dependencies, ever. `kuru.py` and the
  `board/` package must run on a clean `python3`.
- **Template filenames are load-bearing.** `kuru.py` reads templates by exact name
  (`config.json`, `charter.md`, `progress.md`, `board-handoff.md`, `workspace-readme.md`,
  `slice.md`, `contract.yml`, `build-log.md`, `verification.md`, `init.sh`,
  `config.<stack>.json`). Renaming one without updating `kuru.py` crashes
  `init`/`new-slice` — that's the fastest failure signal.
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

- Run `scripts/test/selftest.sh` — it must stay green (exercises the engine's
  guarantees). Add a check when you change engine behavior. Board changes: also
  `scripts/test/board-selftest.sh`.
- For command/agent discovery changes, `scripts/test/smoke-headless.sh` proves
  `/kuru:*` still resolves in a headless `claude -p` session.

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
4. **Board TUI release assets (do not skip the Linux one).** Air-gapped / RHEL 9
   users install the prebuilt Linux binary; a version cut without it leaves them
   stranded. Build the macOS binary too so Mac users don't need a Rust toolchain —
   both scripts write into `dist/` and share one merged `dist/SHA256SUMS` (run
   either order, or both).
   - Linux (repo root; Docker or Apple Container; network at build time):
     ```bash
     ./scripts/build-tui-rhel9.sh
     ```
     Prefer `DOCKER=docker` when the daemon is up; otherwise
     `DOCKER=container` on Apple Silicon. Details: `tui/README.md`,
     `docs/airgap-tui.md`.
   - macOS (repo root; native, no Docker — just needs `cargo`):
     ```bash
     ./scripts/build-tui-macos.sh
     ```
   - Confirm artifacts exist and look right:
     - `dist/kuru-board-tui-linux-amd64.tar.gz` — **primary GitHub Release asset**
     - `dist/kuru-board-tui-macos-<arch>.tar.gz` — macOS asset (arch = whatever
       Mac you built on, e.g. `arm64`)
     - `dist/SHA256SUMS` — has entries for **both**
     - `file dist/kuru-board-tui-linux-amd64` → ELF 64-bit **x86-64** (not arm64)
   - Mention both tarballs in the release notes (Linux: RHEL 9–class glibc,
     x86_64 only, unpack → `chmod +x kuru-board-tui`; macOS: native build for the
     builder's arch — other Mac architectures still build from `tui/` source).
   - Do **not** commit `dist/` (gitignored). The assets live on the Release only —
     they are attached in step 5, not committed.
5. **Commit, tag, and cut the GitHub Release** — there is **no CI**; releases are
   100% manual. Committing and pushing to `main` does **not** create a release; a
   Release only exists once you run `gh release create`. Every version bump gets a
   matching git tag *and* a GitHub Release.
   - Commit the release (on a branch, then fast-forward `main`), and `git push origin main`.
   - Tag the release commit and push the tag:
     ```bash
     git tag -a v<X.Y.Z> -m "Release <X.Y.Z>: <headline>"
     git push origin v<X.Y.Z>
     ```
   - Create the Release, attaching the dist assets in the same command (this both
     creates the release for the tag and uploads the assets):
     ```bash
     gh release create v<X.Y.Z> \
       --title "v<X.Y.Z> — <headline>" \
       --notes "<the CHANGELOG section for this version>" \
       dist/kuru-board-tui-linux-amd64.tar.gz \
       dist/kuru-board-tui-macos-*.tar.gz \
       dist/SHA256SUMS
     ```
   - Verify: `gh release view v<X.Y.Z>` shows all assets (Linux, macOS,
     SHA256SUMS) and `isDraft: false`.
