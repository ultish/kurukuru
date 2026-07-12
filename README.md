# kurukuru

An enterprise delivery harness for coding agents (Claude Code plugin). It turns a
coding agent into a disciplined pipeline for shipping **production** software
across many sessions — not vibe-coding, not hobby projects.

Shared understanding becomes a spec; the spec becomes **vertical slices** that are
small enough for one agent session yet complete enough to build without guessing;
a builder agent implements one slice; a **separate** verifier agent gatekeeps it
against a **frozen contract** with concrete evidence and deterministic gates; by
default a slice is also code-reviewed before it ships (toggle per workspace with
`kuru set-review`). Progress is tracked in files so each session can pick up cold.

## Why (the three sources)

- **Anthropic, _Effective harnesses for long-running agents_** — the progress
  file + checklist + git-history handoff trio, a get-your-bearings startup ritual,
  end-to-end verification before anything is marked done, and guardrails against
  premature "victory." → `progress.md`, `/kuru:bearings`, the gate rules.
- **Anthropic, _Harness design for long-running apps_** — context **resets** over
  in-place compaction; the planner → generator → evaluator separation (separating
  the agent doing the work from the one judging it is the biggest quality lever);
  **sprint contracts** that fix "done" before code is written; evaluators that
  drive the running app and emit evidence-backed findings. → the subagents, frozen
  `contract.yml`, file-based handoffs.
- **OpenAI, _Harness engineering_** — eval-driven gating; every scaffold encodes
  an assumption about what the model can't yet do, so keep gates deterministic and
  measurable. → `kuru gate` + `config.json`.

## The one idea that holds it together

**Facts that gate progress live in machine-checked files, never in an agent's
narration.** A tiny dependency-free engine (`scripts/kuru.py`) owns the truth:
which slices exist, what state each is in, and whether the gates passed. Agents
reason and write prose; they cannot talk past the engine.

Enforced **in code** (not by trust):
1. Illegal state transitions are refused.
2. A slice cannot become `verified` unless a recorded `kuru gate` run passed —
   and is newer than the slice's latest build (stale green runs don't count).
3. A builder (`--by builder`) may not set `verified` or `reviewed`.
4. A slice cannot start while any of its `--depends-on` slices isn't `done`.

(All three are demonstrated by the dry-run in [`impl/BUILD_PLAN.md`](impl/BUILD_PLAN.md) §7, SL-2,
and exercised by [`scripts/test/selftest.sh`](scripts/test/selftest.sh).)

## Install

**The plugin.** This is a Claude Code plugin. Requires `python3` (stdlib only — no
pip installs). Add the plugin directory to Claude Code (local plugin or via a
marketplace entry); Claude Code auto-discovers `commands/`, `agents/`, and
`skills/`. Commands appear as `/kuru:*`.

**Engine path.** Commands call the engine as
`${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}`. Claude Code sets
`CLAUDE_PLUGIN_ROOT` automatically when the plugin loads, so **no configuration is
needed for most users** — the fallback resolves to the right file out of the box.

If that ever fails (e.g. the plugin is symlinked or loaded in an unusual way), pin
the path explicitly. Add to `~/.claude/settings.json`:

```json
{
  "env": {
    "KURU_PY": "/absolute/path/to/kurukuru/scripts/kuru.py"
  }
}
```

Claude Code has no plugin-scoped env mechanism, so this goes in global settings.
It's a one-time addition — once set, it overrides the `CLAUDE_PLUGIN_ROOT` fallback
everywhere and across all workspaces. Restart Claude Code after saving.

The last-resort fallback is the path `kuru init` records in `.kuru/engine` — useful
if neither of the above is set.

**The board runner (optional, for unattended / multi-slice runs).** The
[`board/`](board) package drives the plugin headlessly (see [Running
headless](#running-headless-board-runner)) — agent-agnostic, and parallel across
gate targets. It needs nothing installed beyond `python3` and the agent CLI you
point it at (`claude`, logged in). You don't need it for manual `/kuru:*` use.

## Quickstart (in the repo you're building)

```bash
# 1. scaffold the workspace
python3 /path/to/kuru/scripts/kuru.py init                  # creates ./.kuru/
#    or seed gates for a build tool: init --stack node|pnpm|gradle|maven|go|python|cargo
#    or reuse a saved environment:   init --profile ~/.kuru/profiles/   (a catalog dir or URL)
# 2. config.json gets configured for you during /kuru:charter (it interviews you about
#    language, build pipeline, deploy env, and air-gapped constraints, then sets the gates).
#    To (re)pick a preset manually:  kuru.py set-stack gradle   then tailor the commands.
#    Also fill in .kuru/init.sh (one command to bring up the dev environment).
```

Then, in Claude Code:

```
/kuru:charter            # build shared understanding -> .kuru/charter.md
/kuru:spec [topic]       # charter -> .kuru/spec/spec-N.md  (auto-numbered; kuru-planner)
/kuru:slice spec-N       # spec -> vertical slices with frozen contracts (epic tag = spec-N)
/kuru:check-contract     # OPTIONAL pre-build: is a slice's contract satisfiable + verifiable here?
/kuru:build              # kuru-builder implements the next ready slice -> built
/kuru:verify             # kuru-verifier independently gatekeeps -> verified|rejected
/kuru:review             # code review of a verified slice -> reviewed -> done (on by default)
/kuru:status             # dashboard      /kuru:next   # what to do next
/kuru:reuse-stats        # reuse-index lookup rollup across builds (advisory)
/kuru:bearings           # run at the start of every session
```

The first three steps need a human (discovery, scoping, slicing). Once slices have
frozen contracts, the rest is mechanical. There are three ways to drive it:

- **In-session (sequential):** `/kuru:loop` runs build → verify → review → ship over the ready
  slices, one at a time, until the board is clear (review runs when the workspace has it on — the
  `kuru init` default; a review rejection rebuilds like a verify rejection). To ship just **one**
  named slice and stop there, pass its id: `/kuru:loop <id>` (or `board run --slices <id>`).
- **Dynamic workflow (per-slice pipelines):** `/kuru:loop-workflow` runs the same build →
  verify → review → ship cycle, but it authors a Claude Code **dynamic workflow** — a JavaScript script
  you approve, which the workflow runtime runs in the background. It asks the engine
  `kuru next --all` for the ready set, shows you the plan and dependency edges first, then runs
  **one `build → verify → review → ship` pipeline per slice** (review runs when the workspace has
  it on), each stage a **fresh, isolated `agent()`**.
  Concurrency is keyed on the **gate target**: a target runs **at most one** slice's pipeline at
  a time (**same target → serialized** — the no-worktrees lesson: slices share one working tree,
  so parallel builds clobber each other and a build-in-flight contaminates a same-tree verify),
  while **different targets run in parallel** (disjoint subtrees). A slice's pipeline starts only
  once its `depends_on` are all `done`, so a dependent begins the instant its last dep ships. A
  single-target repo thus runs fully sequentially by design; a polyglot/monorepo runs one
  pipeline per app at once. That per-step clean context is the point: it clears a large board
  without saturating the session. (For a portable, non-Claude-Code driver with the same per-slice
  pipelines, see the [board runner](#running-headless-board-runner) below.) The
  workflow's agents touch kuru only through `/kuru:build`, `/kuru:verify`, `/kuru:ship --no-commit`
  (never `kuru.py`); the engine serializes ledger writes with a file lock. Ship defers its commit,
  so the launching session makes **one commit after the run** (trading per-slice revert
  granularity for parallel speed). `/kuru:loop-workflow SL-0001,SL-0002` scopes it to a curated
  set — they run in parallel if on different targets, else serialized. (Requires Claude Code
  workflows enabled.)
- **Headless / unattended:** the [board runner](#running-headless-board-runner) (`python3 -m board
  run`) — agent-agnostic (Claude/Grok/cmd), sequential or target-parallel — see below.

Across all three, `max-tries` is **per run**: re-running a `loop*` command resets every
slice's try tally, so the cap governs only the current run. A **try** is one full
`build → verify → review` cycle (just `build → verify` when review is off), counted at the
build — so a failed *build* (the builder gives up → `blocked`), a verify rejection, OR a
review rejection is retried with a fresh agent, up to `max-tries`.
A slice already blocked before the run is left for a human, not auto-retried.

Both refuse to start until charter + spec + non-draft (contracted) slices exist,
spawn a fresh builder and a separate verifier per slice, respect inter-slice
**dependencies**, and stop on a slice blocked at start or after a slice exhausts its
`max-tries` build→verify budget. The manual `/kuru:*` commands still work alongside either.

### Environment profiles (reuse a stack across projects)

If you spin up many projects with the same stack — especially in an **air-gapped**
org — keep a **catalog** of reusable, **single-stack profiles** (one file per build
flavour) and point `init --profile` at it. `--profile` takes **one location**: a
local directory of `*.json` profiles, a single `.json` file, or a hosted catalog
URL (so a whole org can share one canonical set of profiles):

```bash
# a directory of profiles (the charter picks the ones that apply)
python3 /path/to/kuru/scripts/kuru.py init --profile ~/.kuru/profiles/

# a single profile file
python3 /path/to/kuru/scripts/kuru.py init --profile ~/.kuru/profiles/gradle-kube.json

# a hosted catalog — GitHub contents API or GitLab repository-tree API URL
python3 /path/to/kuru/scripts/kuru.py init \
  --profile 'https://gitlab.example.com/api/v4/projects/42/repository/tree?path=kuru-profiles'
```

For a hosted catalog the engine fetches the listing and each `*.json` blob itself
(reading `GITHUB_TOKEN` / `GITLAB_TOKEN` for private repos). When you run it through
`/kuru:init`, the command will prefer a **skill** that already knows how to fetch
from your host (GitLab/GitHub/Bitbucket, with its tokens) and only falls back to the
engine's built-in fetcher if none is found.

A profile is plain JSON you keep **outside the plugin** (see
[`templates/profile.example.json`](templates/profile.example.json)):

- `stack` — a gate preset (`node|pnpm|gradle|maven|go|python|cargo`) used to seed
  the initial `config.json` at `init`,
- `config` — suggested gate commands (e.g. gradle `--offline` against an internal
  mirror) that `/kuru:charter` uses as a **starting point** for this flavour's gates,
- `environment` — language/version, deploy target, and **internal registry
  endpoints** that pre-fill the charter's Technical environment so you don't
  re-type them each time, and
- `conventions` — org-specific "how we build here" rules that aren't gate commands
  (e.g. "generate the Gradle build files with the `setup-gradle` skill"), each paired
  with the **checkable artifact** it produces. Deterministic ones get compiled into a
  `setup-conformance` gate so a builder that ignores the rule fails a gate, not just a
  reviewer's patience; judgmental ones become acceptance criteria.

The profiles are **guidance, not gospel**, and a **catalog**: point `--profile` at a
location holding several and `/kuru:charter` picks the ones matching the apps it
discovers in this repo (a Kotlin service → the gradle profile; a web app → the pnpm
profile), assigns each a gate
**target** + `dir`, and ignores the rest. `init` seeds a starting `config.json` (from
a lone profile's `stack`, else the node default) and stashes the profiles under
`.kuru/profiles/`. Then `/kuru:charter` reads them, **summarizes back to you, hunts
for gaps to confirm** (including each app's `dir`), writes the authoritative
`config.json` (a flat gate set, or a per-app `targets` map — see [Multiple build
targets](#multiple-build-targets-monorepo--polyglot)), and folds the rest (endpoints,
deploy target, required tooling) into the charter — rather than applying any of it
verbatim. Internal endpoints you'd rather not commit can be left out of the profile
and supplied during the charter.

### Dependencies between slices

`kuru new-slice "<title>" --depends-on SL-0001,SL-0002` records a dependency. The
engine then **refuses to start** a slice (`ready → in_progress`) until every
dependency is `done`, and `kuru next` skips dependency-blocked slices — so the
loop builds in a safe order. (Parallel building of independent slices is a planned
upgrade.)

### Multiple build targets (monorepo / polyglot)

One repo, several apps with different pipelines — say a gradle/kotlin service and a
pnpm web app — don't share one gate set. Define a **gate target per app** in
`config.json`, each with its own working `dir` and `gates`:

```json
{
  "project": "my-monorepo",
  "targets": {
    "api": { "dir": "services/api", "gates": { "build": { "cmd": "./gradlew :api:build", "required": true, "timeout": 1800 } } },
    "web": { "dir": "apps/web",     "gates": { "lint":  { "cmd": "pnpm lint",          "required": true, "timeout": 600  } } }
  }
}
```

Targets are discovered and written during `/kuru:charter` (seed each with
`kuru set-stack gradle --target api`, then set its `dir`); each slice declares its
app at `/kuru:slice` time (`kuru new-slice "…" --target web`, or `kuru set-target
<id> web` after). `kuru gate <id>` then runs **only that target's gates, in that
target's dir** — the JS slice never runs `./gradlew`. `kuru doctor` flags a slice
that has no target once more than one exists.

A single-app repo needs none of this: a flat top-level `gates` keeps working and is
treated as one implicit `default` target at the repo root.

**Repo-wide gates.** A check that spans the whole repo and has no single owning app —
the `dupehound` duplicate-code scan is the motivating case — goes in a top-level
`repo_gates` map instead. It legally coexists with `targets`, runs at the repo root for
**every** slice regardless of its target, and is left untouched by `set-stack`. That's
why `kuru init --reuse-check warn|block` seeds the reuse gate there: it survives the
charter's conversion to a multi-app config automatically.

```json
{
  "repo_gates": { "reuse": { "cmd": "dupehound check", "required": false, "timeout": 600 } },
  "targets": {
    "api": { "dir": "services/api", "gates": { "build": { "cmd": "./gradlew :api:build", "required": true, "timeout": 1800 } } },
    "web": { "dir": "apps/web",     "gates": { "lint":  { "cmd": "pnpm lint",          "required": true, "timeout": 600  } } }
  }
}
```

## Running headless (board runner)

The `board/` package (`python3 -m board`) is a standalone Python orchestrator
(stdlib only) that drives the plugin with no human in the chair. It reads engine
state (`kuru next --all --json`) to **decide** what's ready, then launches a
**fresh agent process per stage** to **do** it (`build`, `verify`, `review` when
the workspace has review on, then `ship`), repeating until the board is clear.
Each stage is its own process, so context never accumulates and the builder and
verifier are separate processes, not just separate agents; it reads the ledger
back after every stage, so the engine's gate/role/dependency rules gate every
transition. It runs **one per-slice `build → verify → review → ship` pipeline per
slice**, keyed on the **gate target** — same target serialized (one shared tree),
different targets in parallel, dependency-ordered — the same policy as
`/kuru:loop-workflow`, but as a portable process tree instead of a Claude Code
workflow. Ships defer their commit; the runner makes **one `kuru commit` after the
run**.

It's **agent-agnostic**: `--backend claude` (fresh `claude -p` per stage),
`--backend grok` (skill-on-disk prompts + `kuru.py`), `--backend cmd` (any CLI via
a template), or `--backend mock` (deterministic, for tests). For an interactive
view, `scripts/board-tui.sh` launches the Ratatui board (see
[`tui/README.md`](tui/README.md)); it tails the same run and can start/stop one.

### Setup

1. **Prerequisites:** `python3` and the agent CLI for your backend — e.g. the
   `claude` CLI, logged in (confirm with `claude --version`). The board autodetects
   `claude`/`grok` on `PATH` or common install locations; otherwise pass
   `--claude-bin` / `--grok-bin`.
2. **Prove the bridge works** (once): `./scripts/test/smoke-headless.sh` — it loads
   the plugin into a headless `claude -p` session and confirms a `/kuru:*` command
   resolves.
3. **Get a target repo to the loopable point** with the manual commands:
   `/kuru:charter` → `/kuru:spec` → `/kuru:slice` (these need a human). Now every
   slice has a frozen contract.
4. **Run it** (`scripts/board.sh` wraps `python3 -m board` with the plugin path
   injected; or invoke the module directly):

   ```bash
   # plan only — the multi-slice plan (targets, deps, serial vs parallel), no agents:
   python3 -m board plan --repo /path/to/target-repo

   # drive the whole board with Claude, streaming logs:
   python3 -m board run --repo /path/to/target-repo --backend claude --ui plain -y

   # drive a single slice (or a curated set) by id, then stop:
   python3 -m board run --repo /path/to/target-repo --backend claude --slices SL-0003

   # inspect a past run:
   python3 -m board status --repo /path/to/target-repo
   ```

By default `--plugin-dir` is autodetected; if you run the module from outside this
repo, pass `--plugin-dir /path/to/kuru` so it can find `scripts/kuru.py`.

### Permissions

Autonomous runs can't answer permission prompts, so by default the board passes
`--permission-mode bypassPermissions` to each `claude -p` stage (and
`--always-approve` to `grok`). Two ways to tighten the Claude path:

```bash
# allowlist the exact tools/commands up front via a settings file, no bypass:
python3 -m board run --repo . --backend claude --permission-mode acceptEdits --settings perms.json
# or restrict the tool set directly:
python3 -m board run --repo . --backend claude --allowed-tools "Bash Read Edit Write Glob Grep"
```

### Key flags (`board run`)

| Flag | Default | Purpose |
|---|---|---|
| `--repo` | cwd | Target repo containing `.kuru/`. |
| `--plugin-dir` | autodetect | Where the kuru plugin lives (`scripts/kuru.py`). |
| `--backend` | `mock` | Stage worker: `claude` \| `grok` \| `cmd` \| `mock`. |
| `--slices <ids>` | — | Comma-separated scope (e.g. `SL-0001,SL-0002`); omit for the whole board. |
| `--max-tries` | `2` | Per-slice, per-run try budget (a verify/review rejection or a blocked build each costs a try). |
| `--check-contract` | off | Run the advisory contract check before the first clean build. |
| `--permission-mode` | `bypassPermissions` | Passed to `claude` per stage. |
| `--settings` / `--allowed-tools` | — | Tighten Claude permissions instead of bypassing. |
| `--model` | — | Passed through (`claude --model` / `grok -m`). |
| `--ui` | `plain` | `plain` (streaming logs) or `json` (summary). Interactive board: `scripts/board-tui.sh`. |
| `--yes` / `-y` | — | Skip the approval prompt (for CI / unattended). |
| `--dry-run` | — | Plan only, don't run. |
| `--no-commit` | — | Skip the deferred `kuru commit` after ships. |

## The slice state machine

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> ready
    ready --> in_progress
    in_progress --> built
    built --> verifying
    verifying --> verified
    verifying --> rejected
    verified --> reviewed: /kuru:review (review on)
    verified --> done: ship (review off)
    verified --> rejected: review rejects
    reviewed --> done: ship
    rejected --> in_progress
    done --> in_progress: reopen
    done --> [*]
```

Kept off the diagram for readability: **any → blocked** (and unblock back to
anywhere); **any-except-done → dropped → draft** (retire a slice, resurrect it for
a re-write); and three "step back" edges that let you rework without dropping —
**ready → draft** (re-slice), **built → in_progress** (resume building before
verifying), and **reviewed → in_progress** (reopen a reviewed slice).

**Code review is on by default.** `kuru init` seeds it on, so the loop routes each
verified slice through `/kuru:review` before ship (`verified -> reviewed -> done`);
turn it off per workspace with `kuru set-review off`, and a verified slice ships
straight to `done`. Either way, a review that finds real problems rejects the slice
(`verified -> rejected`), routing it back to the builder — there is no
`verified -> in_progress`. The policy is a machine fact (`kuru next` returns action
`review` vs `ship`), not agent narration.

**Reaching `done` auto-commits.** Whatever path a slice takes to `done`, the engine
commits the working tree as one slice-sized commit — the code, the `.kuru/`
artifacts, and the ledger transition together (`kuru: ship <id> — <title>`). It's
best-effort: outside a git repo, or if `git commit` fails (no identity, a rejecting
hook), the slice still lands `done` and the engine just warns. The exception is
`set-status <id> done --no-commit`, which flips the ledger but skips the commit — used
by `/kuru:loop-workflow`, where many slices ship into one shared tree and the driver
commits once after the parallel run.

A slice that shouldn't be built after all (wrong scope, superseded) is **dropped**
(`kuru set-status <id> dropped --note "<why>"`) — `next` and the loop ignore it.
Resurrect it via `dropped -> draft` to re-write its contract under the same id, or
cut a new slice; `doctor` flags anything still depending on a dropped slice.

## Files

The plugin (the tool):

```
kuru/                       ← the plugin (auto-discovered by Claude Code)
├── .claude-plugin/plugin.json
├── commands/        init charter spec slice check-contract build verify review
│                    ship status next bearings loop loop-workflow …
├── agents/          kuru-planner  kuru-builder  kuru-verifier  kuru-contract-critic
├── skills/          kuru-method writing-specs slicing-work checking-a-contract
│                    building-a-slice verifying-a-slice reviewing-a-slice loop-workflow
├── scripts/kuru.py  the deterministic state + gate engine (single source of truth)
├── scripts/board.sh, board-tui.sh, build-tui-rhel9.sh  production launchers / release
├── scripts/test/    selftest, board-selftest, smoke-headless, smoke-tui-linux-amd64
├── board/           multi-slice orchestrator (python3 -m board)
├── tui/             Ratatui board UI (kuru-board-tui); see tui/README.md
└── templates/       artifact templates copied into target repos by kuru init

impl/                        ← internal/legacy docs (BUILD_PLAN, TASKS) — not shipped
```

The `.kuru/` workspace (per target repo, created by `kuru init`):

```
.kuru/
├── config.json   ledger.json   charter.md   progress.md   BOARD_HANDOFF.md   init.sh
├── profiles/     (resolved env profiles, when used)
├── spec/spec-N.md
├── runs/<run_id>/  events.ndjson  summary.json  …   (gitignored; board orchestrator)
└── slices/<id>/  slice.md  contract.yml  build-log.md  verification.md  gate-results.json
```

`ledger.json` + `gate-results.json` are machine truth (only `kuru.py` writes
them). Everything else is narrative written by agents (or board run logs under
`runs/`).

**Commit `.kuru/`** in the target repo — it's the project's delivery memory.
`kuru init` writes a `.kuru/.gitignore` that excludes the machine-local bits (the
absolute `engine` path, transient `gate-*.log` files, and **`runs/`**); everything
else is meant to be shared.

## Design principles

- **Context resets, not vibes** — each phase is a clean file handoff; sessions
  start with `/kuru:bearings`.
- **Separate work from judgment** — the builder never verifies its own slice.
- **Frozen contracts** — "done" is fixed before code is written; scope changes mean
  a new slice, never silent drift.
- **Deterministic gates** — typecheck/lint/test/build are run by the engine and
  recorded; green gates are necessary but never sufficient.
- **Evidence over assertion** — verification passes only on facts the verifier
  observed by exercising the running system.

## Service

status: in-progress
team: [[team:jxhui]]
domain: [[domain:ai-agents]]
version: 0.7.0

depends_on:
  - Claude Code (plugin host — auto-discovers commands/, agents/, skills/)
  - python3 stdlib (kuru.py engine; no third-party deps, ever)
  - claude CLI + login (board runner headless mode; or the grok/cmd backends)

exposes:
  - /kuru:init        — scaffold a .kuru/ workspace in the target repo
  - /kuru:charter     — discovery session → charter.md
  - /kuru:spec        — charter → spec via kuru-planner
  - /kuru:slice       — spec → vertical slices with frozen contracts
  - /kuru:check-contract — optional pre-build: kuru-contract-critic flags an unsatisfiable/unverifiable contract
  - /kuru:build       — kuru-builder implements the next ready slice
  - /kuru:verify      — kuru-verifier independently gatekeeps a built slice
  - /kuru:review      — code review before marking done (on by default; kuru set-review to toggle)
  - /kuru:ship        — mark a verified/reviewed slice done (auto-commits)
  - /kuru:loop          — autonomous build→verify→review→ship loop over all ready slices (sequential); pass a slice id to scope it to one slice and stop
  - /kuru:loop-workflow — parallel build→verify→review→ship over all ready slices, as a dynamic workflow
  - /kuru:next        — print and start the next actionable slice
  - /kuru:status      — delivery dashboard
  - /kuru:reuse-stats — roll up builders' reuse-index lookups across slices (advisory)
  - /kuru:bearings    — session startup ritual (context-reset recovery)
  - python3 -m board  — agent-agnostic headless multi-slice runner (board/ package)
  - scripts/kuru.py   — deterministic state + gate engine (CLI, callable directly)

events:
  publishes: []
  subscribes: []

see_also:
  - [[adr:ADR-001]]  # planner/builder/verifier separation
  - impl/BUILD_PLAN.md  # original spec (legacy — code wins on conflicts)
