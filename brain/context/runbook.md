# Runbook

## Deployment (plugin install)

kurukuru is a Claude Code plugin — it is not deployed as a service.

**To install:** add the plugin directory to Claude Code (local plugin or marketplace
entry). Claude Code auto-discovers `commands/`, `agents/`, `skills/` — no config
needed for most users. `CLAUDE_PLUGIN_ROOT` is set automatically by Claude Code when
the plugin loads, so the engine path resolves out of the box.

**To use in a target repo:** run `kuru init` to scaffold `.kuru/`, then use
`/kuru:charter` → `/kuru:prd` → `/kuru:slice` → `/kuru:build` → `/kuru:verify`.

## Configuration

| Setting | Where | Purpose |
|---------|-------|---------|
| `KURU_PY` | `~/.claude/settings.json` `env` | Override engine path for unusual setups (symlinked plugin, etc.). Not needed for most users. |
| `.kuru/config.json` `gates` | Target repo | Gate commands for this specific project. Written by `/kuru:charter`. |
| `.kuru/config.json` `targets` | Target repo | Per-app gate targets for monorepos/polyglot — each with its own `dir` and `gates`. |
| `GITHUB_TOKEN` / `GITLAB_TOKEN` | Environment | For fetching private hosted profile catalogs via `init --profile <url>`. |
| `--permission-mode` | `runner.py` flag | Default `bypassPermissions` for headless runs. Tighten with `--settings` or `--allowed-tools`. |

Engine path resolution order: `KURU_PY` env var → `${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py` → `.kuru/engine` file written at init.

## Common issues

**`kuru: command not found` in subagents**
`kuru.py` is not on PATH — it lives inside the plugin. Subagents must call it as
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" <cmd>`. This was a
real bug fixed in v0.3.1. If a subagent silently fails to run gates, check this first.

**`set-status verified` refused with "no gate run" or "gate FAILED"**
Gate freshness is enforced: the gate run must exist, must have passed, and must be
newer than the slice's latest `built` transition. Run `kuru gate <id>` to get a fresh green run.

**Builder tries to self-certify `verified`**
The engine refuses `--by builder` for `verified` or `reviewed`. This is not a bug
— it's a hard rule. The verifier subagent must be dispatched separately.

**Subagent doesn't pick up its methodology skill**
All three subagents need `Skill` in their `tools:` allowlist. Without it, the skill
is never loaded and the subagent operates on its base training only. Fixed in v0.3.0.

**`new-slice` leaves an orphan directory on bad args**
Fixed in v0.5.0 — validation now happens before any directory is created. On older
versions, manually delete the orphan `SL-NNNN/` dir; it will conflict with the next id.

**Review rejection path**
Review send-back is `verified → rejected` (not `verified → in_progress` — that
transition doesn't exist). Use `kuru set-status <id> rejected --by reviewer --note "..."`.

**`.kuru/` accidentally committed to plugin repo**
The plugin repo should never contain a `.kuru/` workspace. If one appears, delete it —
it's per-project state from testing. The plugin's `.gitignore` should catch it.

## Verifying the plugin works

```bash
# Confirm the engine is reachable and passes all guarantees
scripts/selftest.sh          # must stay green; add checks when engine behavior changes

# Confirm /kuru:* resolves in a headless claude -p session
scripts/smoke-headless.sh    # requires claude CLI logged in
```

`selftest.sh` covers: illegal transitions refused, gate-freshness enforcement,
builder role restriction, dependency start guard, and the review reject path.

## On-call / escalation

Solo project — Jimmy (jxhui) is the only contact. For bugs, check:
1. `kuru doctor` in the target repo for workspace issues
2. `scripts/selftest.sh` for engine regressions
3. `CHANGELOG.md` for recent behavior changes that might explain the symptom
