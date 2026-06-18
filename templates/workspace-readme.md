# `.kuru/` workspace

This directory is the **state** of a Kurukuru-managed delivery effort for this repo.
The `kuru` plugin is the tool; this directory is the per-project memory.

## The pipeline
```
charter -> prd -> slice -> build -> verify -> review -> done
```

## Files
| Path | What it is | Truth type |
|---|---|---|
| `config.json` | The deterministic gates for THIS repo (typecheck/lint/test/build) — one flat set, or a per-app `targets` map for a monorepo. | machine |
| `profiles/` | Optional. The environment profile(s) passed to `kuru init --profile` (one file per build flavor; the charter matches them to apps). | reference |
| `init.sh` | One command to bring up the dev environment (fill in its TODOs). | script |
| `ledger.json` | Every slice + its status + full history. Single source of truth for state. | **machine** |
| `charter.md` | Shared understanding. Precedes PRDs. | narrative |
| `progress.md` | Cross-session handoff. Read first each session. | narrative |
| `prd/<feature>.md` | One PRD per feature/epic. | narrative |
| `slices/<id>/slice.md` | The vertical-slice spec. | narrative |
| `slices/<id>/contract.yml` | Frozen definition-of-done + acceptance criteria. | narrative (frozen) |
| `slices/<id>/build-log.md` | Builder's running notes. | narrative |
| `slices/<id>/verification.md` | Verifier's evidence-backed verdict. | narrative |
| `slices/<id>/gate-results.json` | Output of `kuru gate`. | **machine** |

**Rule:** machine truth lives in `ledger.json` + `gate-results.json` and is only
written by `kuru.py`. Everything else is narrative written by agents. A slice
cannot become `verified` unless a recorded gate run passed — this is enforced in
code, not by trust.

Do not hand-edit `ledger.json` or `gate-results.json`; use `kuru` subcommands.

## Commit this directory

`.kuru/` is the project's delivery memory — charter, PRDs, slices, progress — and
should be **committed**, so a teammate or a fresh session can pick up cold. The
scaffolded `.kuru/.gitignore` excludes the only machine-local bits:

- `engine` — an absolute path to `kuru.py` on one machine (`kuru init --force`
  regenerates it after a clone or if the plugin moves);
- `slices/*/gate-*.log` — transient gate output (each run's tail is preserved in
  the committed `gate-results.json`).
