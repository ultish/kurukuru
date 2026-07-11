# Architecture

## Components

| Component | Role |
|-----------|------|
| `scripts/kuru.py` | **The single source of truth.** Deterministic state + gate engine. The only thing allowed to mutate `ledger.json` and `gate-results.json`. stdlib only, no pip. |
| `commands/` (12 files) | Thin slash-command orchestrators (`/kuru:*`). They call the engine and dispatch subagents. Methodology lives in skills, not here. |
| `agents/kuru-planner` | Expands charters → specs → candidate slice boundaries. Has Write/Edit — it produces artifacts. |
| `agents/kuru-builder` | Implements exactly one slice vertically. Runs gates. Sets `built`. Cannot set `verified` or `reviewed`. Has Write/Edit. |
| `agents/kuru-verifier` | Adversarial gatekeeper. Re-runs gates, exercises the running app, cites concrete evidence per AC. Read-only tools (no Write/Edit of source). Sets `verified` or `rejected`. |
| `skills/` (8 dirs) | Deep methodology: `kuru-method` (spine), `writing-specs`, `slicing-work`, `checking-a-contract`, `building-a-slice`, `verifying-a-slice`, `reviewing-a-slice`, `loop-workflow`. These are the highest-value content. |
| `templates/` | Files copied into the target repo's `.kuru/` by `kuru init`. Filenames are **load-bearing** — `kuru.py` reads them by exact name. |
| `runner.py` | Standalone headless driver. NOT part of the plugin. Reads `kuru next --json`, spawns a fresh `claude -p` per step. Builder and verifier are separate processes. |
| `.claude-plugin/plugin.json` | Plugin manifest. Claude Code auto-discovers `commands/`, `agents/`, `skills/` by convention — no explicit listing needed. |

## Data flow

```
User runs /kuru:<cmd>
  → command (thin orchestrator) reads engine state via kuru.py
  → dispatches subagent (planner / builder / verifier) if needed
  → subagent writes narrative artifacts (slice.md, build-log.md, verification.md)
  → subagent calls kuru.py to run gates and record transitions
  → kuru.py writes machine truth (ledger.json, gate-results.json)
  → → done auto-commits the working tree
```

For headless runs, `runner.py` wraps this loop: read `kuru next --json` → spawn
`claude -p /kuru:build` or `claude -p /kuru:verify` as a fresh process → repeat.

## Storage

All storage is in the **target repo's `.kuru/` directory** (created by `kuru init`).
The plugin itself holds no runtime state.

| File | Type | Owner |
|------|------|-------|
| `ledger.json` | Machine truth — slice list, statuses, history | `kuru.py` only |
| `gate-results.json` | Machine truth — gate run results per slice | `kuru.py` only |
| `config.json` | Gate commands for this project (editable by user/charter) | User / `/kuru:charter` |
| `charter.md`, `progress.md`, `spec/*.md` | Narrative — written by agents and humans | Agents / humans |
| `slices/<id>/slice.md`, `contract.yml` | Slice spec + frozen contract | Planner / `/kuru:slice` |
| `slices/<id>/build-log.md` | Builder's running notes | kuru-builder |
| `slices/<id>/verification.md` | Verifier's evidence record | kuru-verifier |

`ledger.json` writes are atomic (`os.replace` after writing to a temp file) — a
crash mid-write cannot corrupt machine state.

## Key design decisions

- **Planner / builder / verifier separation** — GAN-inspired: the agent doing work
  is never the agent judging it. The builder cannot set `verified`; the verifier has
  no Write/Edit tools so it cannot fix what it judges. [[adr:ADR-001]]
- **Frozen contracts** — "done" is defined before code is written. Scope changes
  require a new slice or an explicit re-`draft`, never silent drift.
- **Deterministic gates** — typecheck / lint / test / build are run by the engine
  and recorded. Gate freshness is enforced: a green run from before a rebuild is
  rejected as stale evidence (the run must be newer than the slice's latest
  transition into `built`).
- **Code review is opt-in** — verified slices ship straight to `done`; `/kuru:review`
  is run by hand on slices that warrant a closer look. Changed in v0.4.0. [[adr:ADR-002]]
- **stdlib only** — no `pip install`, ever. The plugin must run on a clean `python3`
  and be air-gap friendly.
- **Auto-commit on `done`** — `set-status <id> done` is the single chokepoint; it
  commits code + `.kuru/` artifacts + ledger together as one atomic commit. Best-effort.
- **Five-way documentation of conventions → setup-conformance** — the mechanism is
  deliberately documented in five places (commands/charter.md, skills/slicing-work,
  templates/profile.example.json, templates/charter.md, templates/slice.md) because
  each is the only artifact a reader sees after a context reset. All five must agree.
