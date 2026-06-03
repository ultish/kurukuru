# kurukuru

An enterprise delivery harness for coding agents (Claude Code plugin). It turns a
coding agent into a disciplined pipeline for shipping **production** software
across many sessions — not vibe-coding, not hobby projects.

```
charter -> prd -> slice -> build -> verify -> review -> done
```

Shared understanding becomes a PRD; the PRD becomes **vertical slices** that are
small enough for one agent session yet complete enough to build without guessing;
a builder agent implements one slice; a **separate** verifier agent gatekeeps it
against a **frozen contract** with concrete evidence and deterministic gates; then
it's code-reviewed. Progress is tracked in files so each session can pick up
cold.

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
2. A slice cannot become `verified` unless a recorded `kuru gate` run passed.
3. A builder (`--by builder`) may not set `verified` or `reviewed`.

(All three are demonstrated by the dry-run in `BUILD_PLAN.md` §7, SL-2.)

## Install

This is a Claude Code plugin. Requires `python3` (stdlib only — no pip installs).
Add the plugin directory to Claude Code (local plugin or via a marketplace entry);
Claude Code auto-discovers `commands/`, `agents/`, and `skills/`. Commands appear
as `/kuru:*`.

## Quickstart (in the repo you're building)

```bash
# 1. scaffold the workspace
python3 /path/to/kuru/scripts/kuru.py init     # creates ./.kuru/
# 2. edit .kuru/config.json so the gates match this repo (typecheck/lint/test/build)
```

Then, in Claude Code:

```
/kuru:charter            # build shared understanding -> .kuru/charter.md
/kuru:prd <feature>      # charter -> .kuru/prd/<feature>.md  (kuru-planner)
/kuru:slice <feature>    # PRD -> vertical slices with frozen contracts
/kuru:build              # kuru-builder implements the next ready slice -> built
/kuru:verify             # kuru-verifier independently gatekeeps -> verified|rejected
/kuru:review             # code review -> reviewed -> done
/kuru:status             # dashboard      /kuru:next   # what to do next
/kuru:bearings           # run at the start of every session
```

## The slice state machine

```
draft -> ready -> in_progress -> built -> verifying -> verified -> reviewed -> done
                      ^                       |
                      +------- rejected <-----+
any -> blocked -> (unblock anywhere)          done -> in_progress (reopen)
```

## Files

The plugin (the tool):

```
kuru/
├── .claude-plugin/plugin.json
├── commands/        charter prd slice build verify review status next bearings
├── agents/          kuru-planner  kuru-builder  kuru-verifier
├── skills/          kuru-method writing-prds slicing-work building-a-slice verifying-a-slice
├── scripts/kuru.py  the deterministic state + gate engine (single source of truth)
├── templates/       artifact templates copied into target repos
├── BUILD_PLAN.md    full implementation spec
└── TASKS.md         what's left for a follow-up (cheaper) model
```

The `.kuru/` workspace (per target repo, created by `kuru init`):

```
.kuru/
├── config.json   ledger.json   charter.md   progress.md
├── prd/<feature>.md
└── slices/<id>/  slice.md  contract.yml  build-log.md  verification.md  gate-results.json
```

`ledger.json` + `gate-results.json` are machine truth (only `kuru.py` writes
them). Everything else is narrative written by agents.

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
