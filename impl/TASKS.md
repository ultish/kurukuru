# Tasks — what's left after the expensive-model build

The plugin is **built and engine-verified**. Everything judgment-heavy
(methodology skills, subagent prompts, orchestration commands, engine-coupled
templates, README) is done. What remains is **execution and real-world
validation** — exactly the work suited to a cheaper model.

## What is already DONE (do not rewrite)

- `scripts/kuru.py` — deterministic state/gate engine. Compiles; CLI verified.
- `.claude-plugin/plugin.json`, all `templates/*`.
- All 5 skills, all 3 agents, all 9 commands. Frontmatter validated.
- `README.md`, `BUILD_PLAN.md`.
- **Engine guarantees already proven** (re-run any time to re-confirm):
  - `init` + `new-slice` scaffold correctly (BUILD_PLAN §7 SL-1).
  - Hard rules fire: no-gate-run blocks verify; failing gate blocks verify;
    builder can't self-certify; illegal transition refused (SL-2).
  - Full `draft→done` lifecycle runs clean (SL-6).

## Status (updated 2026-06-03)

- ✅ **T2 done** — `scripts/selftest.sh` reproduces SL-1/SL-2/SL-6 (+ verifying
  state, `--stack` presets, builder self-cert refusal). 33 checks, exits non-zero
  on any failure.
- ✅ **T4 done** — `templates/config.{python,go,node}.json` presets + `kuru init
  --stack <name>`. Default `kuru init` unchanged.
- ✅ **T5 done** — `templates/init.sh` stub dropped into `.kuru/` (executable),
  referenced from `progress.md`'s "How to run / verify".
- ✅ **T6 done** — README quickstart, `.kuru/` tree, and plugin tree reconciled
  with the above.
- ⬜ **T1, T3 still open** — both require loading the plugin into a live Claude
  Code session (command/agent discovery; real end-to-end dry-run). Can't be done
  headlessly. See those tasks below.

### Beyond the original list (added since)

- ✅ **Headless bridge proven** — `scripts/smoke-headless.sh` runs
  `claude -p "/kuru:status" --plugin-dir <here> --permission-mode bypassPermissions`
  in a throwaway repo and confirms the plugin command resolves and runs `kuru.py`
  in that fresh session.
- ✅ **Machine-readable state** — `kuru next|ls|show --json` for external tooling.
- ✅ **Dependency chains** — `new-slice --depends-on …`; `next` skips
  dependency-blocked slices; engine refuses `ready → in_progress` until deps are
  `done`; `doctor` flags unknown deps. (Parallel building of independent slices:
  planned upgrade.)
- ✅ **External autonomous runner** — `runner.py` (repo root): a plain Python loop that
  reads `kuru next --json` and launches a fresh `claude -p` per step
  (build/verify/review), with retry caps, stall/blocked detection, precondition
  gating, and configurable permissions. In-session `/kuru:loop` retained for
  testing without the runner.
- ⬜ **Still unproven: a full autonomous end-to-end run** of `runner.py` (multi
  `claude -p` sessions actually driving a real slice ready→done). That's T3-scale
  (needs a real repo + contract); the bridge, decisions, and gates are all proven.

## Tasks for the follow-up (cheaper) model

### T1 — Install & discovery smoke test  (verification, ~15 min)
Goal: confirm Claude Code actually loads the plugin.
- Add this directory as a local Claude Code plugin.
- Confirm `/kuru:charter`, `/kuru:build`, `/kuru:verify`, `/kuru:status`, etc.
  appear as commands, and that `kuru-builder` / `kuru-verifier` / `kuru-planner`
  show up as agents.
- Acceptance: every `/kuru:*` command is listed; no load errors in the plugin
  panel.
- If anything fails to load, the cause is almost always frontmatter or the
  manifest — check against the official Claude Code plugin docs (don't guess the
  schema).

### T2 — Re-run the engine acceptance checks on this machine  (verification)
Goal: reproduce the three proven guarantees as a regression check.
- Follow BUILD_PLAN §7 SL-1, SL-2, SL-6. The exact commands are in the git/chat
  history; each is a few lines in a `mktemp -d` dir with a trivial gate
  (`{"unit":{"cmd":"true","required":true}}`).
- Acceptance: SL-1, SL-2, SL-6 all pass as described.
- Optional but recommended: turn these into a script `scripts/selftest.sh` that
  exits non-zero on any failure, so the harness can self-check.

### T3 — Real dry-run on a throwaway repo  (verification, end-to-end)
Goal: drive the whole pipeline through Claude Code on a tiny real app.
- In a small Node/TS (or your stack) sample repo: `kuru init`, edit
  `.kuru/config.json` gates to that repo's real commands.
- Run `/kuru:charter` → `/kuru:prd` → `/kuru:slice` for ONE trivial feature
  (e.g. a health-check endpoint), then `/kuru:build` and `/kuru:verify`.
- Acceptance: a slice goes `ready → built → verified` with a real
  `gate-results.json` and a `verification.md` that cites concrete evidence; the
  verifier was a different agent than the builder.
- Capture any friction (confusing prompt wording, missing step) as notes in this
  file under "Findings".

### T4 — Config presets for common stacks  (low-judgment authoring)
Goal: make `kuru init` useful beyond Node.
- Add `templates/config.<stack>.json` presets (e.g. `python`, `go`, `node`) with
  the right gate commands (pytest/ruff/mypy; go test/vet/build; etc.).
- Wire an optional `kuru init --stack <name>` flag in `kuru.py` (small change:
  pick which config template to copy; default stays the current `config.json`).
- Acceptance: `kuru init --stack python` writes a config whose gates are pytest /
  ruff / mypy; existing `kuru init` is unchanged.

### T5 — `init.sh` generation for target repos  (optional, nice-to-have)
The long-running-agents article recommends an `init.sh` that starts the env in
one command. Add a `templates/init.sh` and have `kuru init` drop a stub into
`.kuru/` for the user to fill, and reference it from `progress.md`'s "How to run".
- Acceptance: `.kuru/init.sh` exists after init and is referenced by progress.md.

### T6 — Docs polish  (low priority)
- Add a short `CONTRIBUTING`/usage note if you create a marketplace entry.
- Verify all README links and the file tree match reality after T4/T5.

## Guardrails for the follow-up model
- **Do not change the slice state machine, the hard-rule enforcement, or the
  template filenames** in `kuru.py` — templates are read by exact name; a mismatch
  crashes `init`/`new-slice` (that's your fastest failure signal).
- Keep it **stdlib-only**; no new dependencies.
- Verify each task against its acceptance criteria before moving on — this plugin
  exists precisely to not trust "looks done."

## Findings
<!-- Append anything you discover during T1–T3 here. -->
