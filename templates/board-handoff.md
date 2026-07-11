# Board handoff — for a new agent tab

_You are joining a Kurukuru workspace (possibly next to a board TUI). Reconstruct
state from files — do not assume chat history. This file is updated when a board
run finishes; `progress.md` is the longer cross-session note._

## Orient (do this first)

1. Read **`.kuru/progress.md`** (current state, next action, landmines).
2. Run the engine (from the repo root):
   ```bash
   python3 "${KURU_PY:-$(cat .kuru/engine 2>/dev/null)}" doctor
   python3 "${KURU_PY:-$(cat .kuru/engine 2>/dev/null)}" ls
   python3 "${KURU_PY:-$(cat .kuru/engine 2>/dev/null)}" next
   ```
   If `KURU_PY` / `.kuru/engine` are unset, use the absolute path to
   `scripts/kuru.py` in the kurukuru plugin checkout.
3. **Latest board run** (if any): list `.kuru/runs/` (newest `r_*` dir).
   - `summary.json` — shipped / capped / stuck
   - `config.json` — backend, review on/off, max_tries
   - `events.ndjson` — full timeline (same stream the TUI watches)
   - `SL-xxxx/*.log` — stage logs for failures
4. For the slice you will touch: `.kuru/slices/<id>/{slice.md,contract.yml,build-log.md,verification.md}`
5. Skim `.kuru/charter.md` (and `.kuru/profiles/` if present).

Then give a **5–8 line briefing**: board/ledger state, anything stuck, and **one**
recommended next action. Do not start a full board run or large refactor until
the user confirms.

## Rules (do not break)

- **Only `kuru.py` mutates** `ledger.json` / `gate-results.json` — never hand-edit.
- **Builder ≠ verifier**; a builder must not set `verified` / `reviewed`.
- **`verifying` means re-verify**, not rebuild (engine `STATUS_ACTION`).
- Ship during multi-slice board runs uses **`--no-commit`**; deferred commit is
  separate (`kuru commit` / board end-of-run).
- Same gate target → pipelines serialize; different targets may parallelize.

## Useful commands

```bash
# Board (from the kurukuru plugin, with PYTHONPATH set to the plugin root)
python3 -m board plan --repo . --plugin-dir <plugin>
python3 -m board run -y --backend mock|claude|grok --repo . --plugin-dir <plugin>
python3 -m board status --repo .
# Ratatui UI (optional): scripts/board-tui.sh --repo .
```

## Latest board run

_No board run recorded yet. After `board run` finishes, this section is rewritten
with the run id, backend, outcomes, and paths._
