# Changelog

All notable changes to **kurukuru** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`/kuru:loop-slice <id>` — drive ONE named slice to `done`, then stop.** A dedicated
  single-slice counterpart to `/kuru:loop`: it builds → verifies → ships exactly the
  slice you name and terminates, instead of clearing the whole board. Unrelated slices
  may still be `draft`; it refuses to start only if the *named* slice is a draft or its
  dependencies aren't `done`. `runner.py` gains a matching `--slice <id>` flag.
- **`kuru next --slice <id>` — the engine query the single-slice loop is built on.**
  Returns the next action for one named slice (or `none` with a reason: `done` /
  `blocked` / `waiting_on_deps`), enforcing its dependencies. This is why single-slice
  mode can't drift onto a sibling: the "only this slice" guarantee is machine-checked in
  `kuru.py`, not narrated. The board ranks building a fresh `ready` slice above shipping
  a `verified` one, so a loop that followed the board's `next` each step could interleave
  a second slice before the target ships — `next --slice` sidesteps that entirely.

### Changed
- `runner.py --once` (which stopped after a single build/verify *step*) is replaced by
  `--slice <id>`, which drives one named slice all the way to `done`.

## [0.5.0] - 2026-06-18

### Added
- **A slice auto-commits when it reaches `done`.** `set-status <id> done` (the
  single chokepoint every path — loop, runner, `/kuru:review`, manual — flows
  through) now commits the working tree as one atomic commit: the slice's code, its
  `.kuru/` artifacts, and the ledger transition together, with the message
  `kuru: ship <id> — <title>`. Best-effort: if the repo isn't a git work tree,
  there's nothing to commit, or `git commit` fails (no identity, a rejecting hook),
  it warns and leaves the slice `done` rather than erroring.
- **Per-app gate targets (monorepo / polyglot support).** `config.json` can now
  define a `targets` map — one entry per app/build flavor, each with its own working
  `dir` and `gates` (e.g. a gradle service in `services/api`, a pnpm app in
  `apps/web`). Each slice is bound to a target (`new-slice --target` / new
  `set-target <id> <name>`), and `kuru gate <id>` runs **only that target's gates,
  in that target's dir** — no more running `./gradlew` against a JS slice.
  `set-stack <tool> --target <name>` seeds one target without clobbering the others;
  `doctor` validates target dirs and flags a slice that has no target when several
  exist; `ls`/`next` surface the target. Fully backward compatible: a flat top-level
  `gates` is treated as a single implicit `default` target at the repo root, so
  single-app configs are unchanged. Targets are defined in `/kuru:charter` and
  assigned in `/kuru:slice`.
- **`init --profile` is repeatable — environment profiles are now a catalog.** Pass
  several single-stack profiles (`init --profile gradle-kube.json --profile
  pnpm-web.json`); they're stashed under `.kuru/profiles/`, and `/kuru:charter`
  matches each to an app it discovers in the repo, assigns it a gate target + dir,
  and folds its environment/conventions in per app. Profiles that match nothing are
  ignored. (A single profile still seeds its stack preset at init, as before.)

### Changed
- **Profiles are stashed under `.kuru/profiles/` (was `.kuru/profile.json`)** and no
  longer carry a `targets` block — each profile is one single-stack build flavor,
  and the charter composes targets from the matched set.

### Fixed
- **`new-slice` validates before it writes.** Invalid args (e.g. an unknown
  `--target`) are now rejected before the slice directory is created, so a failed
  `new-slice` no longer leaves an orphan `SL-NNNN/` dir that collides with the next
  id.

## [0.4.0] - 2026-06-16

### Changed
- **Code review is now opt-in instead of a mandatory per-slice step.** The state
  machine allows `verified → done` directly, and `/kuru:loop` (plus `runner.py`)
  ships a verified slice straight to `done` via a new inline `ship` action rather
  than spawning a reviewer for every slice. Run `/kuru:review <id>` by hand on the
  slices that warrant a closer look — it still does a `/code-review`, marks the
  slice `reviewed`, and can reject (`verified → rejected`) to send it back to the
  builder. The `reviewed` status and the verified→reviewed→done detour are
  unchanged, so existing review workflows keep working. State-machine diagrams in
  the README and `kuru-method` skill are now Mermaid graphs.

## [0.3.1] - 2026-06-12

### Fixed
- **Subagents can resolve `kuru` again.** The builder/verifier/planner agents and
  the build/verify skills wrote gate and status commands as a bare `kuru <cmd>`,
  but `kuru.py` ships inside the plugin and is **not** on `PATH` — so a subagent in
  a fresh context (which loads its task skill, not `kuru-method`, where the
  resolution order was documented) ran `kuru` literally and failed with "command
  not found". Each subagent entry point (`kuru-builder`, `kuru-verifier`,
  `kuru-planner`) and the two task skills (`building-a-slice`, `verifying-a-slice`)
  now carry a short "Running `kuru`" note mapping the shorthand to
  `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" <cmd>`, with the
  `.kuru/engine` fallback. The slash commands were already unaffected — they use
  the explicit form.

## [0.3.0] - 2026-06-11

### Added
- **`dropped` status — retire a slice without faking or hand-editing.** Any slice
  except shipped (`done`) work can be set `dropped` (wrong scope, superseded);
  `next` and the loop ignore it, `doctor` flags slices that still depend on a
  dropped one, and `dropped → draft` resurrects it for a contract re-write under
  the same id. Previously a wrongly-cut slice could only sit `blocked` forever —
  and parking it as `draft` poisoned `/kuru:loop`'s preconditions.
- **Gate freshness is enforced in code.** `set-status <id> verified` now also
  requires the recorded gate run to be newer than the slice's latest transition
  into `built` — a green run from before a rebuild is rejected as stale evidence.
- **Subagents can actually load their skills.** The builder/verifier/planner
  agents said "follow the X skill", but their `tools:` allowlists omitted the
  `Skill` tool, so the deep methodology never reached them (confirmed by probing a
  live builder subagent: no Skill tool, no skill content in context). All three
  now get `Skill` plus an explicit load-the-skill-first instruction with a Read
  fallback.

- **`.kuru/` commit guidance + scaffolded `.kuru/.gitignore`.** The workspace is
  meant to be committed (charter, PRDs, slices, progress are the project's memory);
  `kuru init` now writes a `.kuru/.gitignore` excluding the only machine-local
  bits: the absolute `engine` path and transient `gate-*.log` files. Documented in
  the workspace README and the main README.
- **Unblock guidance in `/kuru:status`.** The dashboard now tells the user how to
  release a `blocked` slice once its cause is resolved (`set-status <id> <status>`
  — blocked exits to anywhere).
- **Greenfield gate caveat in `slicing-work`.** On a fresh repo the gates may not
  be able to pass until the toolchain exists; making them green is explicitly part
  of the walking-skeleton slice's job.

### Changed
- **`/kuru:loop`'s review step no longer assumes `/code-review` exists** — it
  falls back to the repo's review skill or a careful manual diff review, matching
  `/kuru:review`'s wording.
- **`smoke-headless.sh` pass condition tightened** — it now requires the slice id
  or title in the output; generic words ("draft", "dashboard") no longer count as
  proof the command resolved.
- **Ledger writes are atomic.** `save_json` writes a temp file and `os.replace`s
  it into place, so a crash or killed terminal mid-write can no longer corrupt
  `ledger.json` / `gate-results.json`.
- **Gate timeouts kill the whole process group.** The watchdog previously killed
  only the shell; a hung gradle/npm child survived the timeout holding the stdout
  pipe open, wedging the gate run — the exact silent hang the timeout exists to
  stop. Gates now run in their own session and get `SIGKILL`ed as a group.

### Removed
- **The dead per-slice `gates:` field.** The `contract.yml` and `slice.md`
  templates implied each slice could select which gates apply, but the engine
  never read them — `kuru gate` always runs every gate in `config.json` (and the
  defaults named gates that don't exist on gradle/maven/python stacks). The
  templates now state plainly that gates are global. Also removed the unused
  `BUILDER_SETTABLE` constant from the engine.

### Fixed
- README: `init` was missing from the commands list; the "enforced in code" list
  now matches the engine's actual rules (gate freshness, dependency start guard);
  the state-machine diagrams (README + `kuru-method`, which claimed "three rules"
  while listing four) now agree and include `dropped`.
- `/kuru:verify` target resolution now also looks for a resumed `verifying` slice,
  matching its own prose.
- `runner.py` stall-guard comment now matches its behavior (one retry, then block).
- Changelog version links: added the missing `[0.2.1]` reference and repointed
  `[Unreleased]`.

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

[Unreleased]: https://example.com/kurukuru/compare/v0.5.0...HEAD
[0.5.0]: https://example.com/kurukuru/compare/v0.4.0...v0.5.0
[0.4.0]: https://example.com/kurukuru/compare/v0.3.1...v0.4.0
[0.3.1]: https://example.com/kurukuru/compare/v0.3.0...v0.3.1
[0.3.0]: https://example.com/kurukuru/compare/v0.2.1...v0.3.0
[0.2.1]: https://example.com/kurukuru/compare/v0.1.3...v0.2.1
[0.1.3]: https://example.com/kurukuru/compare/v0.1.1...v0.1.3
[0.1.1]: https://example.com/kurukuru/compare/v0.1.0...v0.1.1
[0.1.0]: https://example.com/kurukuru/releases/tag/v0.1.0
