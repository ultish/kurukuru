---
name: kuru-method
description: Use when working in a Kurukuru workspace (a .kuru/ directory exists) or running any /kuru:* command. Explains the delivery pipeline, the slice state machine, the artifacts, the hard rules, and the kuru.py command reference. Read this first.
---

# The Kurukuru method

Kurukuru is a delivery harness for building **production** software with coding
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
  metrics, constraints, non-goals. (`/kuru:charter`)
- **prd** — charter becomes a PRD per feature/epic: what & why, functional and
  **non-functional** requirements, acceptance shape. (`/kuru:prd`, skill
  `writing-prds`)
- **slice** — a PRD becomes **vertical slices**: each small enough for one
  session's context, complete enough to build without guessing, with a **frozen
  contract**. (`/kuru:slice`, skill `slicing-work`)
- **build** — the `kuru-builder` subagent implements ONE slice, runs gates, sets
  status `built`. (`/kuru:build`, skill `building-a-slice`)
- **verify** — a SEPARATE `kuru-verifier` subagent gatekeeps against the frozen
  contract with concrete evidence. (`/kuru:verify`, skill `verifying-a-slice`)
- **review** — code review on the diff. (`/kuru:review`)

**Open questions gate the move from charter → PRD → slice.** Ambiguity is cheapest
to catch at the charter, and must be resolved at the latest in the PRD — *with the
user*, folding answers back into the doc. Never start slicing while a blocking open
question is unresolved; slicing freezes the PRD into contracts, so an unanswered
question becomes a guess locked inside one.

The first three phases need a human. Once every slice has a frozen contract, the
build → verify → review → done cycle is mechanical and can be driven
automatically by `/kuru:loop` (optional) — it acts on `kuru next` in order,
spawning a fresh builder and a **separate** verifier per slice, and stops on any
`blocked` slice, a `draft` (uncontracted) slice, or repeated rejection. It never
runs charter/PRD/slicing for you.

## The slice state machine (enforced by kuru.py)

```
draft -> ready -> in_progress -> built -> verifying -> verified -> reviewed -> done
                      ^                       |
                      +------- rejected <-----+
any -> blocked -> (unblock anywhere)          done -> in_progress (reopen)
```

Three rules are enforced **in code** — you cannot talk your way past them:
1. Illegal transitions are refused.
2. A slice cannot reach `verified` unless a recorded gate run exists **and**
   passed (`kuru gate <id>` must be green).
3. `--by builder` may not set `verified` or `reviewed`.
4. A slice cannot **start** (`ready → in_progress`) while any of its
   `--depends-on` slices is not `done`.

## Two non-negotiable disciplines

- **Separation of work and judgment.** The agent that builds a slice never
  verifies it. Building is collaborative; verifying is adversarial. This
  separation is the single biggest quality lever (it's why `/kuru:verify`
  dispatches a fresh `kuru-verifier` subagent).
- **Context resets, not vibes.** Each phase is a clean handoff through files.
  Do not rely on what was said earlier in the chat. At session start run
  `/kuru:bearings` to reconstruct state from `progress.md`, `ledger.json`, and
  git. At session end, update `progress.md`. If you're running low on context,
  **do not fake done to wrap up** — set the slice `blocked` with a note.

## Artifacts (where truth lives)

| File | Truth | Written by |
|---|---|---|
| `.kuru/ledger.json` | **machine** — slices + status + history | `kuru.py` only |
| `.kuru/slices/<id>/gate-results.json` | **machine** — gate pass/fail | `kuru gate` |
| `.kuru/charter.md` | narrative | charter session |
| `.kuru/prd/<f>.md` | narrative | planner |
| `.kuru/slices/<id>/slice.md` | narrative spec | slicer |
| `.kuru/slices/<id>/contract.yml` | narrative, **frozen at `ready`** | slicer |
| `.kuru/slices/<id>/build-log.md` | narrative | builder |
| `.kuru/slices/<id>/verification.md` | narrative + evidence | verifier |
| `.kuru/progress.md` | narrative handoff | every session |

Never hand-edit `ledger.json` or `gate-results.json`. Use kuru subcommands.

## kuru.py command reference

Invoke as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" <cmd>`.

| Command | Effect |
|---|---|
| `init [--force] [--stack node\|python\|go]` | Scaffold `.kuru/` in cwd (optionally with a stack's gate preset). |
| `new-slice "<title>" [--epic E] [--depends-on SL-..,SL-..]` | Create `SL-NNNN` + artifacts; status `draft`. |
| `ls [--status S] [--json]` | Table (or JSON array) of slices. |
| `show <id> [--json]` | Slice JSON + artifact presence (+ gate + rejection count). |
| `next [--json]` | Next actionable slice, in pipeline order (skips dependency-blocked slices). |
| `set-status <id> <status> [--note ..] [--by human\|builder\|verifier\|reviewer]` | Guarded transition. |
| `gate <id>` | Run config gates; write `gate-results.json`; non-zero on fail. |
| `check <id>` | Read-only: may this slice reach `verified`? |
| `doctor` | Validate the workspace. |
