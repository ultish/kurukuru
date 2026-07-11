# Board Runner Plan — agent-agnostic multi-slice orchestration + TUI

**Status:** Phase 0–3 implemented (plan + mock + Claude + hierarchical board TUI); Phase 3b Grok next  
**Created:** 2026-07-11  
**Goal:** Port the *policy* of `/kuru:loop-workflow` into a portable Python control
plane with pluggable agent backends (Claude, Grok, Pi, mock) and a Grok-like
board terminal for progress — without depending on Claude Code’s JS `Workflow`
tool.

Use this document to resume work across sessions. The engine (`scripts/kuru.py`)
and loop-workflow skill remain the source of *delivery policy*; this plan is the
source of *orchestration + UI product shape*.

---

## 1. Why

| Today | Problem |
|-------|---------|
| `/kuru:loop-workflow` | Best multi-slice driver, but Claude-only (JS `Workflow` + `agent()`) |
| `runner.py` | Correct architecture (OS process isolation, kuru decides), but sequential + Claude-hardcoded |
| `/kuru:loop` | In-session; context saturates on large boards |

**Thesis:** Claude’s Workflow value is (1) fresh context per stage, (2) parallel
pipelines keyed by gate target, (3) a live progress UI — not the JS runtime.
Replicate those with:

1. **Orchestrator** — pure Python, no LLM, owns scheduling  
2. **Agent backends** — one headless process per stage  
3. **Board TUI** — Grok-like: fast, sparse, keyboard-first, with **hierarchical
   drill-in** (targets → slices → stages/agents, running vs waiting) — a first-class
   v1 requirement, not polish  


```
┌──────────────────────────────────────────────────────────┐
│  kuru-board  (terminal UX)                               │
│  plan · progress · logs · approve · cancel               │
└───────────────────────────┬──────────────────────────────┘
                            │ events + ledger
                            ▼
┌──────────────────────────────────────────────────────────┐
│  orchestrator  (no LLM)                                  │
│  target mutex · deps · max-tries · deferred commit       │
│  NDJSON event stream                                     │
└───────────────────────────┬──────────────────────────────┘
                            │ spawn stage processes
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
           grok -p       claude -p      mock / pi
```

---

## 2. Non-goals

- Reimplement Claude’s JS `Workflow` tool inside Grok or kuru  
- Parallel builds via git worktrees (forks `ledger.json` — forbidden)  
- LLM-as-scheduler (“model, what next?”) — `kuru next` / ledger is the brain  
- Charter / spec / slice discovery automation — still human judgment  
- Third-party deps in `scripts/kuru.py` — engine stays stdlib-only forever  
- Replacing the Claude plugin — `/kuru:*` remains the Claude UX; board runner
  is an additional driver  

---

## 3. Hard invariants (must never regress)

Copied from loop-workflow / runner / engine. Selftests must encode these.
Where loop-workflow skill and the engine disagree, **the engine wins** (see §7.2).

1. **Only `kuru.py` mutates** `ledger.json` / `gate-results.json` (and, once added,
   deferred commits via a dedicated engine verb — see §7.4 / open decision #4).  
2. **Stage outcome = ledger status**, never agent narration. After every stage,
   re-read `kuru show <id> --json` (or equivalent). A **non-zero exit from
   `kuru.py`** (including lock timeout — see #12) is **not** a ledger transition:
   treat it as a **retryable infrastructure failure**, distinct from
   `rejected` / `blocked` / no-verdict.  
3. **Builder ≠ verifier** — separate process (and ideally separate prompt/role)
   per stage. Never one session that both builds and verifies.  
4. **Same gate target → serialize whole pipeline** (build→verify→review→ship).  
   **Different targets → may run in parallel.**  
   **Null / missing target coalesces to `"default"`** — single-app boards are one
   mutex bucket and run fully sequentially (engine returns `target: null`;
   loop-workflow keys these as `"default"`). Never treat `null` as “no mutex.”  
5. **No `isolation: worktree`** for concurrent slices sharing one ledger.  
6. **One try = one full `build → verify (→ review)` cycle**, counted at **build**.  
   - **Verify rejection**, **review rejection**, and **build `blocked`** each
     consume a try and may rebuild (after budget check).  
   - **`verifying` = re-verify, not rebuild.** Engine
     `STATUS_ACTION["verifying"] = "verify"` (`kuru.py`): a verifier started but
     did not record a verdict; the build is still good. Do **not** normalize
     `verifying → rejected` and rebuild. Cap repeated no-verdict verifies
     separately (see §7.2).  
   - Stuck `verified` after a no-verdict **review** → stuck (no infinite re-review).  
7. **Review policy** from workspace (`meta.review` / `kuru next --json` →
   `.review`): on → verified routes to review; off → verified ships.  
8. **Ship during run uses `--no-commit`**; **one deferred commit** after the run
   on the quiescent tree (via engine — §7.4).  
9. **Blocked-at-start** slices are not auto-retried; mid-run `blocked` may
   retry within max-tries (reset `blocked → in_progress` only — matching
   `runner.py`).  
10. **Dependencies acyclic**; a slice starts only when all `depends_on` are
    `done`. Dead deps → dependent is `stuck`, independent slices continue.  
11. **Orchestrator may write only safety transitions:** retry reset
    `blocked → in_progress`, and optional escalate-to-blocked when giving up —
    never fabricate `built` / `verified` / `reviewed`. **Do not** transition
    `verifying → rejected` as a “retry reset.”  
12. **Parallel safety inherits the ledger flock.** `kuru.py` serializes
    read-modify-write on `.kuru/.ledger.lock` (`fcntl`, ~60s timeout, then
    non-zero exit / `die`). Parallel pipelines are only correct because of this.
    Pipeline must handle lock-timeout failures as retryable (see #2), not as
    “agent failed the slice.”  
13. **Run artifacts must not enter git history.** `.kuru/runs/` is machine-local
    and **must** be gitignored (hard requirement — §6.4). Deferred `git add -A`
    in the engine would otherwise sweep logs/prompts into ship commits.  

---

## 4. Product surface (CLI)

Suggested entrypoints (names flexible; pick one and stick to it):

```bash
# Plan only (no agents)
python3 board/orchestrator.py plan --repo . --plugin-dir /path/to/kuru

# Run with plain streaming logs (CI / headless)
python3 board/orchestrator.py run --repo . --backend mock --ui plain

# Run with live board
python3 board/orchestrator.py run --repo . --backend grok --ui board

# Scoped
python3 board/orchestrator.py run --slices SL-0001,SL-0002 --max-tries 2

# Resume / inspect
python3 board/orchestrator.py status --run-id r_01
python3 board/orchestrator.py logs  --run-id r_01 --slice SL-0001 --stage verify
```

**Flags (target set):**

| Flag | Meaning |
|------|---------|
| `--repo` | Target repo with `.kuru/` |
| `--plugin-dir` | Kurukuru plugin root (for `kuru.py` + skills) |
| `--backend` | `mock` \| `claude` \| `grok` \| `cmd` \| later `pi` |
| `--ui` | `plain` \| `board` \| `json` (events only) |
| `--slices` | Optional comma-separated scope |
| `--max-tries` | Per-slice per-run try budget (default 2) |
| `--dry-run` / plan | Show plan, exit 0 |
| `--yes` | Skip approval (CI) |
| `--events PATH` | NDJSON event log path |
| `--run-dir PATH` | Per-run artifacts (logs, prompts, summary) |

Optional thin wrappers later: `kuru-board`, or `runner.py` becoming a
compatibility shim that calls the new orchestrator with `--backend claude
--ui plain --concurrency 1`.

---

## 5. Module layout (proposed)

Keep **out of** the Claude plugin hot path if needed; recommended under repo root:

```
board/                          # NEW — board runner package (stdlib-first)
  __init__.py
  __main__.py                   # python3 -m board …
  cli.py                        # argparse
  models.py                     # SliceState, RunConfig, Stage, Outcome enums
  events.py                     # Event types + NDJSON writer
  ledger.py                     # thin wrapper: invoke kuru.py, parse JSON
  preconditions.py              # doctor, charter, spec, draft rules
  scheduler.py                  # target-mutex + dep DAG + try budget
  pipeline.py                   # per-slice drivePipeline (check→build→…)
  backends/
    base.py                     # AgentBackend protocol
    mock.py                     # deterministic ledger transitions for tests
    claude.py                   # claude -p (port from runner.py)
    grok.py                     # grok -p headless stage
    cmd.py                      # generic shell template
  prompts.py                    # stage prompt templates (skill paths, not prose dumps)
  # deferred commit: prefer engine `kuru commit` (Phase 0/1 engine change);
  # board only invokes it — do not re-implement git add -A here
  ui/
    plain.py                    # log stream
    board.py                    # hierarchical ANSI board (v1)
    viewmodel.py                # events → tree: targets / slices / stages
    # later: rich_board.py only if stdlib board hits a wall — never required for v1
  selftest_scheduler.py         # or extend scripts/selftest.sh
```

**Dependency policy:**

- Prefer **stdlib only** for `board/` core + hierarchical board TUI (matches kuru ethos).  
- If a richer TUI needs Textual/Rich later, isolate under optional extra — never
  import into `scripts/kuru.py`. v1 must ship without that.

**Do not** put orchestration state machines inside skills or command markdown.
Skills stay methodology; board owns scheduling.

---

## 6. Event schema (UI contract)

Append-only NDJSON. One object per line. Consumers: board UI, CI, debugging.

### 6.1 Required fields

Every event:

```json
{"ts": "2026-07-11T12:00:00Z", "run_id": "r_01", "type": "…"}
```

### 6.2 Event types

| type | payload (key fields) | when |
|------|----------------------|------|
| `run.planned` | `slices[]`, `review`, `max_tries`, `scope` | after reading board |
| `run.started` | same summary | after approval |
| `run.finished` | `shipped[]`, `capped[]`, `stuck[]`, `blocked_at_start[]`, `exit_code` | terminal |
| `run.failed` | `error` | orchestrator crash |
| `slice.scheduled` | `id`, `target`, `depends_on`, `status` | entered live set |
| `slice.waiting` | `id`, `reason` (`deps` \| `mutex`), `detail` | not yet startable |
| `slice.started` | `id`, `target` | pipeline starts |
| `slice.finished` | `id`, `outcome` (`shipped`\|`capped`\|`stuck`\|`blocked`) | pipeline ends |
| `stage.started` | `id`, `stage`, `try` | before spawn |
| `stage.log` | `id`, `stage`, `stream` (`stdout`\|`stderr`), `line` | optional, rate-limited |
| `stage.finished` | `id`, `stage`, `ledger_status`, `exit_code`, `elapsed_ms` | after re-read ledger |
| `commit.started` / `commit.finished` | `message`, `ok` | deferred commit |
| `backend.spawn` | `id`, `stage`, `backend`, `pid` (if any), `role` (`builder`\|`verifier`\|`reviewer`\|`critic`\|`planner`\|`ship`) | process start |
| `backend.exited` | `id`, `stage`, `pid`, `exit_code` | process end (pairs with spawn for TUI agent rows) |

**TUI requirement:** every running stage must emit `backend.spawn` (with `pid` when
the backend is a real subprocess) so the board can show agent/process rows under
that stage. Mock backend emits synthetic spawn/exit with fake pids.

### 6.3 Stages

Canonical stage names (align with loop-workflow):

- `check` — `/kuru:check-contract` (advisory; first clean build only)  
- `repair` — planner rewrite after flagged contract  
- `build`  
- `verify`  
- `review` — only if `review` policy on  
- `ship` — always `--no-commit` during run  

### 6.4 Run directory layout

```
.kuru/runs/<run_id>/
  events.ndjson
  summary.json          # final shipped/capped/stuck
  config.json           # frozen RunConfig
  SL-0001/
    build.log
    verify.log
    …
    prompts/build.md    # exact prompt sent (debug)
```

Prefer under target repo `.kuru/runs/` so it stays with workspace state.

#### 6.4.1 Gitignore is a **hard requirement** (not optional)

The engine’s ship commit is unconditional `git add -A` (`kuru.py` `_commit_slice`).
Deferred commit (and any mid-run accidental commit path) would **sweep run logs,
events, and prompts into git history** unless ignored.

**Required before any deferred-commit path ships:**

1. Extend the scaffolded `.kuru/.gitignore` (seeded by `kuru init`, currently
   `engine`, `.ledger.lock`, `slices/*/gate-*.log` only — see `kuru.py` init) to
   include at least:
   ```
   runs/
   ```
2. Board Phase 0 `doctor`/preflight: if `.kuru/runs` would be tracked or is not
   ignored, **warn hard** (prefer refuse deferred commit until fixed).  
3. Existing workspaces: document one-line fix
   `echo 'runs/' >> .kuru/.gitignore`.  
4. Alternative only if gitignore is impossible: put run dir **outside** the repo
   (e.g. `$XDG_STATE_HOME/kuru-runs/<repo-hash>/<run_id>/`) — default remains
   in-tree `.kuru/runs/` + gitignore.

Selftest must assert that after a mock run + deferred commit, no
`.kuru/runs/**` path appears in the commit.

---

## 7. Scheduler algorithm (port of loop-workflow **policy**, engine-aligned)

Reference: `skills/loop-workflow/SKILL.md` for concurrency/try/deferred-commit
ideas; **`scripts/kuru.py` `STATUS_ACTION` + `runner.py` for status routing.**

> **Known skill divergence:** the loop-workflow reference script currently puts
> `verifying` in `NEEDS_BUILD` and normalizes toward rebuild. That is **wrong
> relative to the engine** (`STATUS_ACTION["verifying"] = "verify"`). The board
> runner follows the **engine / runner.py**. Fixing the skill is a separate
> small cleanup (not blocking Phase 0; do not re-encode the skill bug here).

### 7.1 Plan phase (once)

1. Preconditions (see §8).  
2. `kuru next --all --json` (or `ls` + `show` if needed) → slices with
   `status`, `depends_on`, `target`, plus top-level `review`.  
3. Normalize each slice’s concurrency key:
   `mutex_target = slice.target or "default"`  
   (JSON may have `"target": null` for single-app repos — `kuru.py`.)  
4. Apply scope filter if `--slices`.  
5. Drop blocked-at-start from live set; record them.  
6. Emit `run.planned`; if interactive and not `--yes`, wait for approval.

### 7.2 Per-slice pipeline (`drivePipeline`)

Mutable per slice: `status`, `tries`, `no_verdict_verifies`, `repairs`,
`checked`, `pipeline_iters`.

**Routing tables (engine-aligned):**

| Ledger status | Action | Notes |
|---------------|--------|-------|
| `ready`, `in_progress`, `rejected` | **build** | start / continue a try |
| `blocked` (mid-run only) | reset → `in_progress`, then **build** | matches `runner.py`; costs try at next build |
| `built` | **verify** | |
| **`verifying`** | **verify** (re-verify) | **not** rebuild — build is still good |
| `verified` | **review** if review on else **ship** | |
| `reviewed` | **ship** | |
| `done` | terminal shipped | |

```
NEEDS_BUILD = {ready, in_progress, rejected}   # NOT verifying
# blocked mid-run: normalize to in_progress then NEEDS_BUILD

while true:
  if ++pipeline_iters > MAX_PIPELINE_ITERS:  # stall guard (see below)
      stuck; return

  st = status[id]

  if st == blocked and mid_run:
      kuru set-status id in_progress --note "retry after failed build"
      st = in_progress

  if st in NEEDS_BUILD:
      # optional pre-build contract check on first clean entry
      if st in (ready, in_progress) and id not in checked:
          run check; if flagged → repair loop capped by max_tries; else checked
      if tries >= max_tries → capped; return
      tries += 1
      run build stage
      handle_kuru_infra_errors  # lock timeout / non-zero kuru → retryable, do not burn try? (see note)
      re-read ledger → status[id]
      continue

  if st in (built, verifying):
      run verify stage
      re-read ledger
      if still verifying:  # no verdict again
          no_verdict_verifies += 1
          if no_verdict_verifies >= MAX_NO_VERDICT: stuck or capped; return
          continue           # re-verify only — do NOT rebuild
      # verified / rejected / blocked handled below / next loop
      continue

  action = ACTION[st]   # verified→review|ship; reviewed→ship
  if no action → stuck; return
  run stage; re-read ledger
  if done → shipped; return
  if ship refused (still verified/reviewed) → stuck
  if review left verified (no verdict) → stuck   # do not spin re-review
  if rejected or blocked → loop (rejected → NEEDS_BUILD; blocked → normalize)
```

**Stall / spin guards (required — runner parity):**

| Guard | Default | Purpose |
|-------|---------|---------|
| `max_tries` | 2 | Full build→verify(→review) cycles counted at **build** |
| `MAX_NO_VERDICT` | 2 | Consecutive `verifying` after verify stage with no ledger advance |
| `MAX_PIPELINE_ITERS` | e.g. 20 per slice | Hard ceiling on the `while true` loop so success-without-transition cannot spin |
| Global `--max-iters` | optional | Whole-run safety like `runner.py` |

**Infra vs slice failure:** if `kuru show` / `set-status` exits non-zero (including
ledger lock timeout at 60s), emit `stage`/`run` infra event, backoff, retry a
small number of times; **do not** count as a build try and **do not** mark the
slice rejected from narration. Only ledger status after a successful `kuru`
read drives routing.

### 7.3 Target-mutex scheduler

```
def mutex_key(slice) -> str:
    return slice.target or "default"   # null/missing → one shared bucket

busy: set of mutex keys          # never raw null
running: map id → future/thread

loop:
  for each live slice:
    t = mutex_key(s)
    if not running and deps_done and not dep_dead and t not in busy:
      start pipeline (busy.add(t) for entire pipeline lifetime)
  if nothing running: break
  wait for any pipeline to finish (busy.discard(t); may unlock deps)
```

**Single-app boards:** every slice has `target: null` → all key as `"default"` →
**fully sequential**. That is correct (one shared tree). A naive “null means no
mutex” would parallelize every slice against one tree — forbidden by §3.4–3.5.

**Concurrency model:** `concurrent.futures.ThreadPoolExecutor` is fine if stage
runs are subprocess-bound (GIL irrelevant). One pipeline task per slice;
mutex is logical (busy set), not OS. **Ledger correctness under parallelism is
the engine flock (§3.12), not this mutex** — the target mutex protects the
*working tree / gates*, the flock protects the *ledger file*.

### 7.4 Termination and deferred commit

On every exit path (success, capped slices remaining, interrupt):

1. Emit final classification.  
2. **Deferred commit** of shipped work (if any `done` this run and tree dirty)
   — one commit per run, same trade as loop-workflow.  
3. **Do not re-implement `git add -A` in the board.** Prefer a new engine verb
   (Phase 0/1):
   ```text
   kuru commit [--message "..."] [--slices SL-0001,SL-0002]
   ```
   that centralizes message format and respects `.kuru/.gitignore` (same as
   today’s `_commit_slice`, but callable after a batch of `--no-commit` ships).
   Until that lands, board may shell `git` only as a temporary bridge — track
   as debt.  
4. Update `.kuru/progress.md`? **Optional / later** — don’t block MVP.  
5. Exit codes: `0` all scoped work shipped or board clear; `1` capped/stuck
   remain; `2` precondition / config error.

---

## 8. Preconditions

Mirror loop-workflow / runner:

| Check | Whole board | Scoped `--slices` |
|-------|-------------|-------------------|
| `kuru doctor` hard fails | block | block |
| `.kuru/charter.md` present | block | block |
| ≥1 file under `.kuru/spec/` | block | block |
| No draft slices | block | only named + their deps must be contracted |
| Named id exists / not draft | n/a | block if draft or unknown |
| Dep of named slice not done and not in scope | n/a | block or report stuck at plan |

Emit human-readable “run X first” messages (charter / spec / slice).

---

## 9. Agent backend protocol

```python
class AgentBackend(Protocol):
    name: str

    def run_stage(
        self,
        *,
        stage: str,           # build|verify|review|ship|check|repair
        slice_id: str,
        prompt: str,
        cwd: Path,
        env: dict,
        log_path: Path,
        timeout: float | None,
    ) -> StageProcessResult:
        """Run to completion. Does NOT interpret ledger status."""
        ...
```

`StageProcessResult`: `exit_code`, `elapsed_ms`, maybe `pid`.  
**Ledger interpretation always happens in `pipeline.py` after the backend returns.**

### 9.1 Backend: mock

- No model.  
- Implements deterministic transitions for selftest scenarios A–Q style
  (pass, reject once then pass, always fail, no verdict, same-target serial,
  multi-target parallel, review on/off, contract flag/repair).  
- Fast; used in CI.

### 9.2 Backend: claude

Port `runner.py` `dispatch`:

```bash
claude -p "<prompt>" --plugin-dir <plugin> [--permission-mode …] …
```

Default prompt can stay slash-oriented: `/kuru:build SL-0001` when plugin
discovery works; fallback to explicit “run kuru.py + load skill” instructions.

### 9.3 Backend: grok

```bash
grok -p "<prompt>" --cwd <repo> [permissions flags]
```

Prompt must be **self-contained** (no reliance on Claude plugin slash
discovery):

1. Resolve `KURU_PY` / plugin path.  
2. Read the relevant skill file under plugin `skills/…`.  
3. Perform the stage.  
4. End with `kuru show <id>` / ensure ledger transition recorded.

Document exact flags after one live experiment (`--yolo` / permission mode,
streaming). Capture findings in this file’s §15 Session log.

### 9.4 Backend: cmd

User-supplied template, e.g.:

```
--backend cmd --backend-cmd 'my-agent -p {prompt_file} --dir {cwd}'
```

Escape hatch for Pi and others.

### 9.5 Prompt construction (`prompts.py`)

Keep prompts short; point at skills on disk:

```
You are the BUILDER for Kurukuru slice {id}.
1. Read {plugin}/skills/building-a-slice/SKILL.md and follow it.
2. Run: python3 {kuru_py} show {id}
3. Advance the slice through / equivalent of build (implement + gates).
4. Do not set verified/reviewed.
5. When finished, run: python3 {kuru_py} show {id} --json
   Your process may exit; the orchestrator trusts only the ledger.
```

Similar templates for verify / review / ship / check / repair.  
Ship template must use `set-status done --no-commit` (or `/kuru:ship --no-commit`).

---

## 10. Board TUI (v1 requirement: hierarchical drill-in)

**Decision (2026-07-11):** the board is **not** a flat status table only. v1 must
support **browsing the run hierarchy** — what is running in parallel, what is
waiting and why, and drilling into a stage / agent process and its log. This is
the Claude Workflow progress experience, with a Grok-like sparse terminal.

### 10.0 Information hierarchy (required)

The view-model is a tree rebuilt from the event stream (+ ledger snapshots):

```text
Run
└── Gate target                    # concurrency key; at most one live pipeline
    └── Slice pipeline             # one build→verify→(review)→ship chain
        └── Stage                  # check | repair | build | verify | review | ship
            └── Agent process      # one backend spawn per stage (role + pid)
```

**Semantics operators must see at a glance:**

| State | Meaning |
|-------|---------|
| **Running** | Stage has an active backend process (`backend.spawn` without matching exit) |
| **Waiting (mutex)** | Slice is live but target held by another slice’s pipeline |
| **Waiting (deps)** | One or more `depends_on` not yet `done` |
| **Queued** | Deps met, target free, not yet scheduled this tick (brief) |
| **Done stage** | Stage finished; ledger advanced |
| **Failed try** | Stage ended in reject/blocked/no-verdict; may retry |
| **Capped / stuck / shipped** | Terminal slice outcomes for this run |

Parallelism is **across targets** (and thus across slice pipelines). Stages
inside one slice are **serial**. The TUI must make that obvious (one busy slice
per target lane; multiple target lanes active at once).

### 10.1 Three panes / modes (required)

All stdlib ANSI; no mouse required; degrade to `--ui plain` if not a TTY.

#### A. Run overview (default)

Tree or grouped list by **target**, then slices:

```text
 kuru-board  ·  my-service  ·  review on  ·  run r_01  ·  12m 04s
 ────────────────────────────────────────────────────────────────
 ▼ target:api    BUSY · SL-0001                          mutex
   ● SL-0001  Checkout API     try 1/2   3m12
     check ✓  build ✓  verify ●  review ·  ship ·
     agent  verifier  pid 4421  grok  3m12
   · SL-0003  Payments hook    waiting (mutex: SL-0001)
     check ·  build ·  verify ·  review ·  ship ·
 ▼ target:web    BUSY · SL-0002
   ● SL-0002  Storefront       try 1/2   1m40
     check ✓  build ✓  verify ✓  review ●  ship ·
     agent  reviewer  pid 4490  grok  1m40
 · target:worker IDLE
   · SL-0004  Jobs             waiting (deps: SL-0001)
 ────────────────────────────────────────────────────────────────
 2 running · 2 waiting · 0 capped · j/k select · enter drill · l log · p pause · q quit
```

Requirements:

- Expand/collapse targets (`h`/`l` or `←`/`→`, or `space` on target row).  
- Cursor selection (`j`/`k` or arrows) on **any** row type: target, slice, stage, agent.  
- Selected row shows a one-line detail in the footer (reason waiting, log path, pid).  
- Live refresh from events (or 200–500ms tick + event drain).  
- Header: repo/name, review on/off, run id, elapsed, counts.

#### B. Drill-in detail (Enter on slice or stage)

Split or full-panel detail for the selection:

```text
 SL-0001  Checkout API  ·  target=api  ·  deps=none  ·  try 1/2
 ledger: verifying
 pipeline: check ✓ → build ✓ → verify ● → review · → ship ·
 ────────────────────────────────────────────────────────────────
 stage: verify   role: verifier   backend: grok   pid: 4421
 started: 3m12 ago   log: .kuru/runs/r_01/SL-0001/verify.log
 ────────────────────────────────────────────────────────────────
 │ gate unit ok
 │ gate e2e running…
 │ …
 ────────────────────────────────────────────────────────────────
 [esc] back  [l] open log in $PAGER  [c] cancel stage (if supported)  [q] quit
```

Requirements:

- **Stage list** for the slice with status glyphs and elapsed.  
- **Agent/process row** for the active (or last) stage: role, backend name, pid, elapsed.  
- **Log tail** of the selected stage file (last N lines, follow while stage running).  
- Esc returns to overview; selection preserved.

#### C. Waiting / queue focus (filter)

Optional toggle (`w`) that filters overview to **only non-running** actionable
rows (waiting mutex, waiting deps, capped, stuck) so operators can see blockers
without scrolling past busy lanes. Same tree, filtered.

### 10.2 Keybinds (v1)

| Key | Action |
|-----|--------|
| `j` / `k` or `↓` / `↑` | Move selection |
| `h` / `l` or `←` / `→` | Collapse / expand target (or slice stage list) |
| `Enter` | Drill into selection (slice → detail; stage → detail focused on that stage) |
| `Esc` | Back to overview |
| `l` | Open selected stage log in `$PAGER` (or print path if no pager) |
| `w` | Toggle waiting/blocker filter |
| `p` | Pause starting **new** pipelines (in-flight continue) |
| `c` | Cancel selected running stage/slice if orchestrator supports cancel (Phase 4 ok; stub “not yet” in Phase 3) |
| `q` | Quit (confirm if run still active) |
| `?` | Help overlay of keybinds |

### 10.3 Plain UI (still required)

- One line per high-signal event.  
- Suitable for CI logs.  
- `--ui json` = raw NDJSON to stdout.  
- Board is interactive-only; plain remains the headless default for tests.

### 10.4 View-model rules (`ui/viewmodel.py`)

- Pure functions: `apply_event(state, event) -> state`.  
- Derive waiting reasons from scheduler facts already in events
  (`slice.waiting` with `reason: deps|mutex`, or recompute from last plan + busy set).  
- Agent rows appear on `backend.spawn` and close on `backend.exited` / `stage.finished`.  
- Never invent ledger status — on `stage.finished`, use `ledger_status` from the event.  
- Overview and detail are two projections of the **same** state object.

### 10.5 Later polish (after v1 hierarchical board)

- Run history browser across past `run_id`s  
- Horizontal split with persistent log pane while navigating tree  
- Optional Textual/Rich if stdlib hits a wall (not required)  
- Token/cost counters if a backend reports them  
- Mouse support  

### 10.6 Non-goals for the TUI

- Replacing Grok/Claude chat for building code  
- Editing prompts interactively mid-run (v1)  
- Showing internal subagents **spawned inside** a stage process (opaque to us unless
  the backend emits events). The orchestrator’s unit of agent is **one process per
  stage**; that is what the tree displays.

---

## 11. Relationship to existing artifacts

| Artifact | Relationship after this work |
|----------|------------------------------|
| `scripts/kuru.py` | Unchanged role; orchestrator’s only state API |
| `runner.py` | Becomes thin wrapper **or** deprecated in favor of `board` with `--backend claude --ui plain`; document migration |
| `skills/loop-workflow` | Policy reference; Claude path may later shell out to orchestrator instead of authoring JS |
| `commands/loop-workflow.md` | Optional future: “prefer board runner if available” |
| `scripts/selftest.sh` | Keep engine tests; **add** scheduler/orchestrator tests (mock backend) |
| Claude plugin | Untouched for MVP |

**Future convergence (phase 4):** `/kuru:loop-workflow` authors nothing JS —
it runs `python3 -m board run --backend claude --ui board` so Claude and Grok
share one scheduler.

---

## 12. Phased build plan

Each phase is a **session-sized** vertical slice: shippable, testable, documentable.

### Phase 0 — Scaffold + events + plan + engine prerequisites (no agents)

**Deliverables:**

- `board/` package skeleton  
- `ledger.py` wrapping `kuru.py` JSON commands  
- `events.py` + run dir creation  
- `preconditions.py`  
- `cli.py plan` prints actionable board (targets with **null → default**, deps,
  serial vs parallel)  
- **Engine/template:** add `runs/` to scaffolded `.kuru/.gitignore`; document
  migration for existing workspaces  
- **Engine (preferred):** add `kuru commit` for deferred batch commit (or open
  an issue and temporary board-side git with explicit debt)  
- Dry-run selfcheck against a temp `.kuru` workspace  

**Done when:** `python3 -m board plan --repo <fixture>` exits 0 and prints a
correct plan (single-app shows one `default` lane); events file contains
`run.planned`; new inits ignore `runs/`.

### Phase 1 — Scheduler + mock backend + plain UI

**Deliverables:**

- `scheduler.py` + `pipeline.py` with **engine-aligned** routing (§7.2):  
  - `verifying` → re-verify (assert mock scenario: interrupted verify does
    **not** re-build)  
  - `mutex_key = target or "default"`  
  - try budget + `MAX_NO_VERDICT` + `MAX_PIPELINE_ITERS` stall guard  
  - non-zero `kuru` / lock timeout treated as infra retry  
- `backends/mock.py` with scenario hooks  
- Sequential and multi-target concurrent runs  
- Try budget, stuck/capped, deferred commit via `kuru commit` (fixture repo)  
- Assert deferred commit **does not** include `.kuru/runs/**`  
- `--ui plain`  
- Selftests ported from selftest pipeline cases that matter:  
  - same-target serial (H)  
  - different-target parallel (I)  
  - **null-target single-app serial** (all key as `default`)  
  - retry then ship (C/N)  
  - max-tries cap (D/O)  
  - dead dep scoped stuck (E)  
  - review on/off (G/Q)  
  - no-verdict verify re-verifies then stuck/cap (align P to engine, not rebuild)  
  - contract check/repair optional **or** phase 1.5  

**Done when:** `python3 -m board run --backend mock --ui plain` clears a fixture
board; automated tests green without network/models; verifying/null-target/
gitignore cases green.

### Phase 2 — Claude backend (superset of runner.py, not mere parity)

**Deliverables:**

- `backends/claude.py` (port dispatch from `runner.py`)  
- **Superset:** whole-board + scoped runs **plus** Phase 1 multi-target
  concurrency (runner.py is single-threaded — there is no concurrency to
  “port” from it; concurrency comes from the board scheduler)  
- Ship `--no-commit` + deferred `kuru commit`  
- Docs: how to invoke vs `runner.py`  

**Done when:** one real (or smoke) repo stage works; mock tests still pass;
`runner.py` either delegates or docs say “use board”.

### Phase 3 — Hierarchical board TUI (can use mock; Grok optional same phase)

**Deliverables:**

- `ui/viewmodel.py` — event → tree state (targets → slices → stages → agents)  
- `ui/board.py` — stdlib ANSI **hierarchical** board (§10):  
  - overview by target with running / waiting (mutex|deps)  
  - expand/collapse + j/k selection  
  - Enter drill-in: stage list, agent pid/role, **log tail**  
  - `w` waiting filter, `l` pager, `p` pause new starts, `?` help  
- Wire all backends to emit `backend.spawn` / `backend.exited`  
- Selftest: feed a recorded event sequence; assert view-model tree
  (e.g. two targets busy, one mutex waiter, one dep waiter)  
- Short README section: “Board runner” + keybinds  

**Done when:** `run --backend mock --ui board` on a multi-target fixture shows
parallel target lanes, waiting reasons, and drill-in log tail without a model.
Manual checklist: keybinds work on a real TTY.

### Phase 3b — Grok backend (may merge with Phase 3 if time allows)

**Deliverables:**

- `backends/grok.py` + stage prompts that load skills from disk  
- Document flags / permissions discovered in a live spike  

**Done when:** `run --backend grok --ui board` can drive at least one slice
build→verify path on a sample workspace (or documented manual checklist if
model cost is an issue); hierarchical board shows the grok agent row + pid.

### Phase 4 — Polish + Claude loop-workflow convergence (optional)

- Contract check/repair stages if deferred  
- `cmd` / Pi backend  
- `/kuru:loop-workflow` → shell to orchestrator  
- Cancel selected stage (`c`), stronger pause semantics  
- progress.md update  
- Run history browser  
- CHANGELOG + version bump when shipping  

---

## 13. Testing strategy

| Layer | How |
|-------|-----|
| Engine | Existing `scripts/selftest.sh` — do not break |
| Scheduler | Mock backend + fixture ledgers; assert order, mutex, outcomes |
| Events | Assert required event sequence for a happy path |
| Backend smoke | Optional manual / nightly for claude & grok |
| UI view-model | Event-sequence unit tests: parallel targets, mutex wait, dep wait, drill tree shape |
| UI board | Manual TTY checklist; non-TTY falls back to plain (no flaky screenshot CI) |

**Fixture approach:** temp dir, `kuru init`, seed slices via engine commands,
mock flips status with `kuru set-status` under the hood (mock is allowed to call
engine as “fake agent”).

---

## 14. Risks and decisions

| Risk | Mitigation |
|------|------------|
| Grok headless flags / permissions differ from Claude | Spike early in Phase 3; document in §15 |
| Subprocess output floods events | Rate-limit `stage.log`; full log always on disk |
| Thread + signal handling for Ctrl+C | Central cancel event; kill process groups |
| Deferred commit sweeps unrelated dirty files | Same as loop-workflow; document; pathspec later if needed |
| Deferred commit sweeps **run logs** | **Hard:** gitignore `runs/` (§6.4.1); selftest commit contents |
| Duplicate logic vs loop-workflow skill | Board follows **engine**; fix skill’s `verifying`→rebuild separately |
| Scope creep into full agent product | Board is a driver, not a second Grok |
| Hierarchical TUI complexity | View-model pure + tested; ANSI board is a thin renderer; mock-first Phase 3 |
| Parallel set-status lock timeout (60s) | Infra retry with backoff; surface in TUI; do not burn try budget |

**Open decisions (resolve when implementing):**

1. Package path: `board/` vs `scripts/board/` vs `kuru_board/`?  
   **Suggestion:** `board/` at plugin root, not part of Claude auto-discovery.  
2. Should `runner.py` be deleted or become a shim?  
   **Suggestion:** shim for one major version.  
3. Include contract check in Phase 1 or 4?  
   **Suggestion:** Phase 1 without check; Phase 4 add (mock scenarios L/M already
   exist in engine selftest inspiration).  
4. **Deferred commit ownership — RESOLVED (prefer engine):** add
   `kuru commit [--message] [--slices …]` rather than re-implementing
   `_commit_slice` in the board. Message format lives in the engine, e.g.
   `kuru: board run <run_id> — ship SL-0001, SL-0002`. Temporary board-side
   `git` only if engine change slips; track as debt.  
5. Does a kuru lock-timeout burn a build try?  
   **Suggestion:** no — infra retry budget separate (e.g. 3 short retries).  

---

## 15. Session log (append when working)

Use this section when resuming mid-build.

### Template

```
### YYYY-MM-DD — <who/session>
- Phase: N
- Done:
- Next:
- Notes / flag discoveries:
```

### 2026-07-11 — planning session

- Phase: 0 not started  
- Done: this plan written (`impl/BOARD_RUNNER_PLAN.md`)  
- Next: Phase 0 scaffold when ready to implement  
- Notes: design agreed — port policy not Workflow JS; Grok-like board TUI;
  NDJSON events; mock-first; stdlib preferred for core  

### 2026-07-11 — TUI hierarchy promoted to v1

- Phase: 0 not started  
- Done: §10 rewritten — hierarchical drill-in is a **v1 requirement** (targets →
  slices → stages → agents; waiting mutex/deps; log tail; keybinds). Phase 3
  retargeted at mock+board first; Grok is 3b. Events gain `backend.exited` +
  `role` on spawn.  
- Next: Phase 0 scaffold when ready to implement  
- Notes: user explicitly wants Claude-workflow-like phase/agent visibility with
  Grok-like terminal feel  

### 2026-07-11 — Opus 4.8 plan review folded in

- Phase: 0 not started  
- Done: incorporated cross-check vs `kuru.py` / `runner.py` / loop-workflow skill:  
  1. **`verifying` → re-verify**, not rebuild (§3.6, §7.2); note skill divergence  
  2. **`.kuru/runs/` gitignore hard requirement** (§3.13, §6.4.1)  
  3. **null target → `"default"` mutex** (§3.4, §7.3)  
  4. **ledger flock** cited as inherited parallel safety (§3.12); lock timeout =
     infra retry  
  5. **deferred commit via `kuru commit`** engine verb (open decision #4 resolved)  
  6. stall guards: `MAX_NO_VERDICT`, `MAX_PIPELINE_ITERS`  
  7. Phase 2 reframed as **superset** of runner, not parity  
- Next: Phase 0 (include gitignore + prefer `kuru commit` in same wave as scaffold)  
- Notes: findings 1–3 must land before Phase 1 encodes wrong policy in mock tests  

### 2026-07-11 — Phase 0 implemented

- Phase: **0 done**  
- Done:  
  - `board/` package: `cli plan`, `ledger`, `events`, `preconditions`, `plan`, `models`  
  - Engine: `runs/` in init `.gitignore`; `kuru commit`; doctor warns if `runs/` missing;
    `next --all` waiting entries include `target`  
  - `scripts/board-selftest.sh` (12 checks); engine selftest **201** checks  
  - Invoke: `PYTHONPATH=. python3 -m board plan --repo <ws> --emit-events`  
- Next: **Phase 1** — scheduler + mock backend + plain UI (engine-aligned verifying,
  mutex default, stall guards)  
- Notes: no `run` command yet; hierarchical TUI is Phase 3  

### 2026-07-11 — Phase 1 implemented

- Phase: **1 done**  
- Done:  
  - `board/pipeline.py`, `scheduler.py`, `backends/mock.py`, `ui/plain.py`  
  - `python3 -m board run --backend mock -y`  
  - Policy: `verifying` → re-verify only (`build_count=1` under no_verdict)  
  - Target mutex serial + multi-target parallel; max-tries cap; deferred commit  
  - `board-selftest.sh` **18** checks  
- Next: **Phase 2** Claude backend, and/or **Phase 3** hierarchical board TUI  
- Notes: contract check/repair still skipped (`skip_check=True`); claude/grok backends
  not implemented  

### 2026-07-11 — Phase 2 implemented (Claude backend)

- Phase: **2 done**  
- Done:  
  - `board/backends/claude.py` — `find_claude`, `ClaudeBackend.run_stage` → `claude -p`
    with plugin-dir / permission-mode / model / settings / allowed-tools  
  - `board/prompts.py` — slash prompts; ship always `/kuru:ship <id> --no-commit`  
  - CLI: `--backend claude` + pass-through flags; mock remains default  
  - Env: `CLAUDE_PLUGIN_ROOT` + `KURU_PY` for spawned sessions  
  - Selftest: construct / missing-bin clear error / dry-run without binary (no live API)  
- **How to invoke:**
  ```bash
  # Multi-slice / multi-target (preferred over runner.py for boards)
  PYTHONPATH=. python3 -m board run --backend claude -y --repo <ws> \
    --plugin-dir /path/to/kurukuru

  # Optional: --claude-bin, --permission-mode, --model, --settings, --allowed-tools
  # Sequential single-driver still available: python3 runner.py --repo <ws>
  ```
- Next: **Phase 3** hierarchical board TUI (`--ui board`)  
- Notes: live Claude login still required for a real run; CI only exercises the dry
  path. `runner.py` is not deleted — board is the multi-target path.  

### 2026-07-11 — Phase 3 implemented (hierarchical board TUI)

- Phase: **3 done**  
- Done:  
  - `board/ui/viewmodel.py` — pure `apply_event(state, event)`; tree: targets →
    slices → stages → agents; waiting mutex/deps; ledger status only from events  
  - `board/ui/board.py` — stdlib ANSI board; non-TTY → plain via `make_run_ui`  
  - CLI: `--ui board` (also `plain` / `json`); scheduler `pause_event` for `p`  
  - Pipeline emits `backend.spawn` *before* `run_stage` so agent rows can go live  
  - Selftest: view-model sequences (two targets busy, mutex waiter, dep waiter,
    agent on spawn) + non-TTY board fallback  
- **Keybinds:** `j/k` select · `h`/`←` collapse · `→`/space expand · `Enter` drill ·
  `Esc` back · `l` log/$PAGER · `w` waiting filter · `p` pause new starts ·
  `c` cancel stub · `q` quit · `?` help  
- **How to invoke:**
  ```bash
  PYTHONPATH=. python3 -m board run --backend mock --ui board -y --repo <ws> \
    --plugin-dir /path/to/kurukuru
  # Off-TTY / CI: falls back to plain automatically
  PYTHONPATH=. python3 -m board run --backend mock --ui board -y --repo <ws> </dev/null
  ```
- Next: **Phase 3b** Grok backend (optional), or Phase 4 cancel / loop-workflow shell-out  
- Notes: cancel (`c`) still stub; live pid for claude stages still None until
  backend uses Popen (spawn event fires pre-stage with pid=None).  

---

## 16. Acceptance criteria (whole project)

The project is “done” for v1 when:

1. **Mock path:** full multi-slice run with target mutex + retries is automated
   and green in CI/selftest.  
2. **Claude path:** can replace `runner.py` for whole-board and scoped runs.  
3. **Grok path:** can complete at least a single-slice mechanical loop via
   skill-based prompts (documented).  
4. **UI:** `--ui plain` works headlessly; `--ui board` provides **hierarchical
   drill-in** (§10): overview by target (running vs waiting mutex/deps), select
   and Enter into stage/agent detail with log tail, documented keybinds.  
5. **View-model tests** cover parallel targets + wait reasons without a TTY.  
6. **Policy tests:** `verifying` re-verifies (no rebuild); null targets serialize
   under `"default"`; deferred commit excludes `.kuru/runs/**`.  
7. **Invariants §3** all have tests or explicit documented inheritance from
   engine enforcement (including ledger flock).  
8. **No third-party deps** required for core orchestrator + hierarchical board.  
9. README (or this plan §4) documents how to run it cold.

---

## 17. Quick resume checklist (start of any session)

```bash
# 1. Read this plan (§12 current phase, §15 last session log)
# 2. Engine still green
./scripts/selftest.sh

# 3. See what exists
ls board 2>/dev/null || echo "board/ not started"

# 4. Implement only the current phase’s deliverables
# 5. Append §15 session log before stopping
```

**Priority order if time is short:**  
Phase 0 → Phase 1 (mock+scheduler) → **Phase 3 hierarchical board** → Phase 2 Claude
→ Phase 3b Grok.  
(Board can be validated entirely with mock; agent backends plug in under the same UI.)

---

## 18. References (in-repo)

- `skills/loop-workflow/SKILL.md` — policy + reference pipeline script  
- `commands/loop-workflow.md` — Claude command shape  
- `runner.py` — process-isolation driver to generalize  
- `scripts/kuru.py` — state machine / gates / `next --json`  
- `scripts/selftest.sh` — pipeline scenarios A–Q to re-home on mock backend  
- `README.md` — loop-workflow concurrency rules (target mutex, deferred commit)

---

## 19. One-sentence summary

**Build a stdlib Python orchestrator that schedules per-slice pipelines from
ledger truth, runs each stage in a fresh agent process (Claude/Grok/mock),
streams NDJSON events, and renders a fast Grok-like hierarchical board
(targets → slices → stages → agents, with waiting reasons and log drill-in) —
porting loop-workflow’s policy without Claude’s Workflow runtime.**
