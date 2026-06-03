---
name: keel-method
description: Use when working in a Keel workspace (a .keel/ directory exists) or running any /keel:* command. Explains the delivery pipeline, the slice state machine, the artifacts, the hard rules, and the keel.py command reference. Read this first.
---

# The Keel method

Keel is a delivery harness for building **production** software with coding
agents across many sessions. It exists to stop the two failure modes that kill
long-running agent work: **premature "victory"** (declaring done what isn't) and
**context-reset amnesia** (each session starting blind). It does that by making
the facts that gate progress live in machine-checked files, never in an agent's
narration.

## The pipeline

```
charter -> prd -> slice -> build -> verify -> review -> done
```

- **charter** — shared understanding with the humans. Problem, users, success
  metrics, constraints, non-goals. (`/keel:charter`)
- **prd** — charter becomes a PRD per feature/epic: what & why, functional and
  **non-functional** requirements, acceptance shape. (`/keel:prd`, skill
  `writing-prds`)
- **slice** — a PRD becomes **vertical slices**: each small enough for one
  session's context, complete enough to build without guessing, with a **frozen
  contract**. (`/keel:slice`, skill `slicing-work`)
- **build** — the `keel-builder` subagent implements ONE slice, runs gates, sets
  status `built`. (`/keel:build`, skill `building-a-slice`)
- **verify** — a SEPARATE `keel-verifier` subagent gatekeeps against the frozen
  contract with concrete evidence. (`/keel:verify`, skill `verifying-a-slice`)
- **review** — code review on the diff. (`/keel:review`)

## The slice state machine (enforced by keel.py)

```
draft -> ready -> in_progress -> built -> verifying -> verified -> reviewed -> done
                      ^                       |
                      +------- rejected <-----+
any -> blocked -> (unblock anywhere)          done -> in_progress (reopen)
```

Three rules are enforced **in code** — you cannot talk your way past them:
1. Illegal transitions are refused.
2. A slice cannot reach `verified` unless a recorded gate run exists **and**
   passed (`keel gate <id>` must be green).
3. `--by builder` may not set `verified` or `reviewed`.

## Two non-negotiable disciplines

- **Separation of work and judgment.** The agent that builds a slice never
  verifies it. Building is collaborative; verifying is adversarial. This
  separation is the single biggest quality lever (it's why `/keel:verify`
  dispatches a fresh `keel-verifier` subagent).
- **Context resets, not vibes.** Each phase is a clean handoff through files.
  Do not rely on what was said earlier in the chat. At session start run
  `/keel:bearings` to reconstruct state from `progress.md`, `ledger.json`, and
  git. At session end, update `progress.md`. If you're running low on context,
  **do not fake done to wrap up** — set the slice `blocked` with a note.

## Artifacts (where truth lives)

| File | Truth | Written by |
|---|---|---|
| `.keel/ledger.json` | **machine** — slices + status + history | `keel.py` only |
| `.keel/slices/<id>/gate-results.json` | **machine** — gate pass/fail | `keel gate` |
| `.keel/charter.md` | narrative | charter session |
| `.keel/prd/<f>.md` | narrative | planner |
| `.keel/slices/<id>/slice.md` | narrative spec | slicer |
| `.keel/slices/<id>/contract.yml` | narrative, **frozen at `ready`** | slicer |
| `.keel/slices/<id>/build-log.md` | narrative | builder |
| `.keel/slices/<id>/verification.md` | narrative + evidence | verifier |
| `.keel/progress.md` | narrative handoff | every session |

Never hand-edit `ledger.json` or `gate-results.json`. Use keel subcommands.

## keel.py command reference

Invoke as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/keel.py" <cmd>`.

| Command | Effect |
|---|---|
| `init [--force]` | Scaffold `.keel/` in cwd. |
| `new-slice "<title>" [--epic E]` | Create `SL-NNNN` + artifacts; status `draft`. |
| `ls [--status S]` | Table of slices. |
| `show <id>` | Slice JSON + artifact presence. |
| `next` | Next actionable slice, in pipeline order. |
| `set-status <id> <status> [--note ..] [--by human\|builder\|verifier\|reviewer]` | Guarded transition. |
| `gate <id>` | Run config gates; write `gate-results.json`; non-zero on fail. |
| `check <id>` | Read-only: may this slice reach `verified`? |
| `doctor` | Validate the workspace. |
