# Memory

## Conventions

- **Commands thin, skills deep.** Methodology belongs in `skills/`, never duplicated
  into `commands/`. Commands orchestrate; skills teach.
- **stdlib only, forever.** No `pip install`. `kuru.py` and `runner.py` must run on
  a clean `python3`. This is an air-gap requirement and a hard constraint, not a preference.
- **Template filenames are load-bearing.** `kuru.py` reads templates by exact name
  (`config.json`, `charter.md`, `progress.md`, `workspace-readme.md`, `slice.md`,
  `contract.yml`, `build-log.md`, `verification.md`, `init.sh`). Renaming one without
  updating `kuru.py` crashes `init`/`new-slice` immediately — that's the fastest failure signal.
- **Frontmatter must be valid YAML** in every command, agent, and skill file.
- **Five-place documentation rule.** The conventions → `setup-conformance` mechanism
  is documented in five places deliberately: `commands/charter.md`, `skills/slicing-work`,
  `templates/profile.example.json`, `templates/charter.md`, `templates/slice.md`. If
  you change the mechanism, all five must be updated to agree.
- **State machine mirrored in four places.** `kuru.py` (`STATUSES`, `TRANSITIONS`,
  `STATUS_ACTION`, `pick_next`) is the truth. Diagrams in `README.md` and the
  `kuru-method` skill must match. When you change the engine, update all three.
- **Never commit `.kuru/` to this repo.** The plugin is the tool; `.kuru/` is
  per-project state created in the target repo.

## Decisions

- **Code review is opt-in** (since v0.4.0). Verified slices ship straight to `done`.
  `/kuru:review` is run by hand on slices that warrant a closer look. Changed from
  mandatory because it was blocking the loop unnecessarily.
- **Review send-back is `verified → rejected`** (since v0.1.1). There is no
  `verified → in_progress` transition in the engine. Review rejections use
  `--by reviewer` and count toward the retry cap exactly like verifier rejections.
- **Gate freshness is enforced** (since v0.3.0). A green gate run from before the
  latest `built` transition is rejected as stale. `kuru gate <id>` must be re-run
  after any rebuild.
- **`--profile` takes a single catalog location** (since v0.7.0). One flag pointing
  at a dir / single file / URL — not multiple `--profile` flags. Profiles are stashed
  under `.kuru/profiles/`; charter picks the matching ones per app.
- **Auto-commit on `done`** (since v0.5.0). `set-status <id> done` commits code +
  `.kuru/` artifacts + ledger together (`kuru: ship <id> — <title>`). Best-effort.
- **Ledger writes are atomic** (since v0.3.0). `save_json` uses `os.replace` so a
  crash mid-write cannot corrupt `ledger.json` or `gate-results.json`.

## Watch out for

- **`kuru` is not on PATH in subagents.** Always call the engine as
  `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" <cmd>` — never bare
  `kuru`. Subagents in fresh contexts don't inherit the command resolution from
  `kuru-method`. This caught us in v0.3.1.
- **Subagents need `Skill` in their tools allowlist** or the methodology skill is
  never loaded and the subagent works off base training only. Caught in v0.3.0.
- **`impl/BUILD_PLAN.md` is legacy.** Where it and the code disagree, the code and
  `BUILD_PLAN.md §9` win. Don't restore spec behavior from the original sections.
- **Gate timeouts kill the entire process group** (since v0.3.0). Previously only
  the shell was killed; hung gradle/npm children survived holding stdout open and
  silently wedged the gate run.
- **The verifier's tools list must be explicit.** Omitting `tools:` entirely would
  inherit everything including Write/Edit, breaking the "judge, don't fix" guarantee.
  The explicit allowlist (`Read, Grep, Glob, Bash, Skill, mcp__playwright`) stays.
- **`runner.py` is NOT part of the plugin.** It's a standalone driver at the repo
  root. Don't reference it from plugin commands or treat it as a plugin component.
