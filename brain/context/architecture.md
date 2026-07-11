# Architecture

## Components

| Component | Role |
|-----------|------|
| `scripts/kuru.py` | **The single source of truth.** Deterministic state + gate engine. The only thing allowed to mutate `ledger.json` and `gate-results.json`. stdlib only, no pip. |
| `commands/` | Thin slash-command orchestrators (`/kuru:*`). They call the engine and dispatch subagents. Methodology lives in skills, not here. |
| `agents/kuru-planner` | Expands charters → specs → candidate slice boundaries. Has Write/Edit — it produces artifacts. |
| `agents/kuru-builder` | Implements exactly one slice vertically. Runs gates. Sets `built`. Cannot set `verified` or `reviewed`. Has Write/Edit. |
| `agents/kuru-verifier` | Adversarial gatekeeper. Re-runs gates, exercises the running app, cites concrete evidence per AC. Read-only tools (no Write/Edit of source). Sets `verified` or `rejected`. |
| `agents/kuru-contract-critic` | Advisory pre-build contract check (`/kuru:check-contract`). |
| `skills/` | Deep methodology: `kuru-method` (spine), `writing-specs`, `slicing-work`, `checking-a-contract`, `building-a-slice`, `verifying-a-slice`, `reviewing-a-slice`, `loop-workflow`. |
| `templates/` | Files copied into the target repo's `.kuru/` by `kuru init`. Filenames are **load-bearing** — `kuru.py` reads them by exact name. |
| `board/` | Agent-agnostic multi-slice orchestrator (`python3 -m board`). Plan/run/status/logs; backends mock/claude/grok/cmd; writes `.kuru/runs/*/events.ndjson`. Progress UI: plain/json only. |
| `tui/` | Ratatui hierarchical board (`kuru-board-tui`). Watches run events; can spawn `board run --ui plain`. Launcher: `scripts/board-tui.sh`. |
| `runner.py` | Standalone single-threaded Claude driver. NOT part of the plugin. Prefer `python3 -m board` for multi-slice / multi-target runs. |
| `.claude-plugin/plugin.json` | Plugin manifest. Claude Code auto-discovers `commands/`, `agents/`, `skills/` by convention — no explicit listing needed. |

## Data flow

```
User runs /kuru:<cmd>
  → command (thin orchestrator) reads engine state via kuru.py
  → dispatches subagent (planner / builder / verifier) if needed
  → subagent writes narrative artifacts (slice.md, build-log.md, verification.md)
  → subagent calls kuru.py to run gates and record transitions
  → kuru.py writes machine truth (ledger.json, gate-results.json)
  → → done auto-commits the working tree (unless --no-commit)

Multi-slice board:
  python3 -m board run  (or TUI start → same)
  → scheduler pipelines per slice / target mutex
  → backends invoke agents / mock
  → EventWriter → .kuru/runs/<id>/events.ndjson
  → kuru-board-tui tails events (optional)
  → deferred kuru commit + BOARD_HANDOFF.md
```

## Storage

All storage is in the **target repo's `.kuru/` directory** (created by `kuru init`).
The plugin itself holds no runtime state.

| File | Type | Owner |
|------|------|-------|
| `ledger.json` | Machine truth — slice list, statuses, history | `kuru.py` only |
| `gate-results.json` | Machine truth — gate run results per slice | `kuru.py` only |
| `config.json` | Gate commands for this project (editable by user/charter) | User / `/kuru:charter` |
| `charter.md`, `progress.md`, `spec/*.md` | Narrative — written by agents and humans | Agents / humans |
| `BOARD_HANDOFF.md` | Agent-tab orient + latest board run summary | `kuru init` seed; rewritten after `board run` |
| `runs/<run_id>/` | Board event streams (gitignored) | `board/` orchestrator |
| `slices/<id>/slice.md`, `contract.yml` | Slice spec + frozen contract | Planner / `/kuru:slice` |
| `slices/<id>/build-log.md` | Builder's running notes | kuru-builder |
| `slices/<id>/verification.md` | Verifier's evidence record | kuru-verifier |

`ledger.json` writes are atomic (`os.replace` after writing to a temp file) — a
crash mid-write cannot corrupt machine state.

## Key design decisions

- **Planner / builder / verifier separation** — GAN-inspired: the agent doing work
  is never the agent judging it. The builder cannot set `verified`; the verifier has
  no Write/Edit tools so it cannot fix what it judges. [[adr:ADR-004]]
- **Frozen contracts** — "done" is defined before code is written. Scope changes
  require a new slice or an explicit re-`draft`, never silent drift. [[adr:ADR-005]]
- **Deterministic gates** — typecheck / lint / test / build are run by the engine
  and recorded. Gate freshness is enforced: a green run from before a rebuild is
  rejected as stale evidence (the run must be newer than the slice's latest
  transition into `built`).
- **Code review is on by default** — `kuru init` seeds `meta.review: true`; loops
  route `verified` → review → ship. Toggle with `kuru set-review on|off` (or
  `kuru init --no-review`). Historical ADR-001 described an earlier opt-in default.
- **stdlib only** for the engine and board package — no `pip install` for those.
  The Ratatui TUI is a separate Rust binary (optional). [[adr:ADR-003]]
- **Auto-commit on `done`** — `set-status <id> done` is the single chokepoint; it
  commits code + `.kuru/` artifacts + ledger together as one atomic commit. Best-effort.
  Board/loop-workflow ships with `--no-commit` and defers one batch `kuru commit`.
- **Five-way documentation of conventions → setup-conformance** — the mechanism is
  deliberately documented in five places (commands/charter.md, skills/slicing-work,
  templates/profile.example.json, templates/charter.md, templates/slice.md) because
  each is the only artifact a reader sees after a context reset. All five must agree.
  [[adr:ADR-002]]
