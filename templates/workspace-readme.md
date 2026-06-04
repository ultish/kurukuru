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
| `config.json` | The deterministic gates for THIS repo (typecheck/lint/test/build). | machine |
| `profile.json` | Optional. The environment profile passed to `kuru init --profile`. | reference |
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
