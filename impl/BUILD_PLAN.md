# Build Plan — `kuru`: an enterprise delivery harness for coding agents

> **For the implementing model:** This is a complete, self-contained spec. Build it
> slice by slice in the order given (§7). Each slice has explicit acceptance
> criteria — do not advance until they pass. Where exact file contents are given
> in fenced blocks, reproduce them faithfully; where a *spec/outline* is given,
> write the file to satisfy every listed requirement. One file already exists and
> is **load-bearing reference**: `scripts/kuru.py` — use it as-is.

---

## 1. What we are building and why

`kuru` is a Claude Code **plugin** that turns a coding agent into a disciplined
delivery pipeline for **production enterprise software** — not vibe-coding. It
encodes the hard-won patterns from three harness-design articles:

- **Anthropic — "Effective harnesses for long-running agents"**: two-agent
  architecture, `init.sh` + progress file + feature checklist as the handoff
  trio, get-your-bearings session startup, end-to-end verification before
  marking anything done, guardrails against premature "victory."
- **Anthropic — "Harness design for long-running apps"**: *context resets* over
  in-place compaction; planner → generator → evaluator separation (a
  GAN-inspired lever — separate the agent doing work from the agent judging it);
  *sprint contracts* that negotiate "done" before code is written; evaluators
  that test the running app and emit granular, evidence-backed findings;
  file-based async coordination between agents.
- **OpenAI — "Harness engineering"**: eval-driven gating; every scaffold
  component encodes an assumption about what the model can't yet do on its own,
  so keep gates deterministic and measurable.

**The core thesis of kuru:** facts that gate progress (does a slice exist, what
state is it in, did the tests/typecheck/lint/build pass) must live in
machine-checked files, never in an agent's narration. Agents reason and write
prose; a tiny deterministic engine (`kuru.py`) owns the truth.

### The pipeline

```
  /kuru:charter  ── shared understanding, captured as a charter
        │
  /kuru:prd      ── charter → PRD (problem, scope, NFRs, acceptance shape)
        │
  /kuru:slice    ── PRD → vertical slices: small enough for ONE session's
        │            context, complete enough to build without guessing.
        │            Each slice gets a FROZEN contract (definition of done +
        │            acceptance criteria + which deterministic gates apply).
        │
  /kuru:build    ── builder subagent implements ONE slice; updates build-log;
        │            runs gates; sets status `built`. Cannot self-certify.
        │
  /kuru:verify   ── SEPARATE verifier subagent. Re-runs gates, exercises the
        │            running app, and for EVERY acceptance criterion cites
        │            concrete evidence. Verdict: verified | rejected.
        │
  /kuru:review   ── code review (delegates to /code-review) → reviewed
        │
       done
```

### The slice state machine (owned by `kuru.py`)

```
draft → ready → in_progress → built → verifying → verified → reviewed → done
                     ▲                     │
                     └──── rejected ◄──────┘
any → blocked → (unblock to anywhere)        done → in_progress (reopen)
```

Hard rules enforced in code (already implemented in `kuru.py`):
- Illegal transitions are rejected.
- A slice **cannot** enter `verified` unless a recorded gate run exists **and**
  passed (`kuru gate <id>` must have been run green).
- `--by builder` may not set `verified` or `reviewed`.

---

## 2. Final file tree

Build exactly this layout under `/Users/jxhui/Developer/harness/`:

```
harness/
├── README.md                         # ⟵ overwrite the empty stub (spec in §6.1)
├── BUILD_PLAN.md                     # this file (leave as-is)
├── .claude-plugin/
│   └── plugin.json                   # §3.1
├── commands/                         # user-facing slash commands (/kuru:*)
│   ├── charter.md                    # §3.2
│   ├── prd.md
│   ├── slice.md
│   ├── build.md
│   ├── verify.md
│   ├── review.md
│   ├── status.md
│   ├── next.md
│   └── bearings.md
├── agents/                           # the separated roles (subagents)
│   ├── kuru-planner.md               # §3.3
│   ├── kuru-builder.md
│   └── kuru-verifier.md
├── skills/                           # the methodology (model-invokable)
│   ├── kuru-method/SKILL.md          # §3.4 — the spine; everything refers here
│   ├── writing-prds/SKILL.md
│   ├── slicing-work/SKILL.md
│   ├── building-a-slice/SKILL.md
│   └── verifying-a-slice/SKILL.md
├── scripts/
│   └── kuru.py                       # ✅ ALREADY WRITTEN — reference engine
└── templates/                        # artifact templates copied into target repos
    ├── config.json                   # §5.1
    ├── charter.md                    # §5.2
    ├── progress.md                   # §5.3
    ├── workspace-readme.md           # §5.4
    ├── slice.md                      # §5.5
    ├── contract.yml                  # §5.6
    ├── build-log.md                  # §5.7
    └── verification.md               # §5.8
```

The plugin operates on a **`.kuru/` workspace** that `kuru.py init` scaffolds
inside the *target* repository (the enterprise app being built). Plugin files
above are the tool; `.kuru/` is the per-project state. Never commit `.kuru/`
state into this plugin repo.

```
<target-repo>/.kuru/
├── config.json        # gate commands for THIS project (typecheck/lint/test/build)
├── ledger.json        # machine truth: all slices + statuses + history
├── charter.md         # shared understanding
├── progress.md        # narrative handoff across sessions
├── prd/<feature>.md   # one PRD per feature/epic
└── slices/<SL-id>/
    ├── slice.md            # the vertical slice spec (human/agent readable)
    ├── contract.yml        # FROZEN definition-of-done + acceptance criteria
    ├── build-log.md        # builder's running notes
    ├── verification.md     # verifier's evidence-backed verdict
    └── gate-results.json   # written by `kuru gate` (machine truth)
```

---

## 3. Plugin component specs

### 3.1 `.claude-plugin/plugin.json`

Exact contents:

```json
{
  "name": "kuru",
  "version": "0.1.0",
  "description": "Enterprise delivery harness: charter → PRD → vertical slices → build → independent verification → review, with deterministic gates and file-based handoffs for long-running coding agents.",
  "author": { "name": "kuru" },
  "keywords": ["harness", "agents", "prd", "verification", "enterprise", "workflow"]
}
```

Claude Code auto-discovers `commands/`, `agents/`, and `skills/` by convention —
no need to list them. Commands become `/kuru:<file-stem>`.

### 3.2 Slash commands (`commands/*.md`)

Each command is a markdown file with YAML frontmatter then a prompt body.
Conventions for the implementing model:

- Frontmatter keys: `description` (one line), `argument-hint` (optional).
- The body is the instruction the main agent executes when the user runs it.
- Commands may shell out with lines beginning `!` and embed file contents with
  `@path`. Use `${CLAUDE_PLUGIN_ROOT}` to reach `scripts/kuru.py`.
- Invoke the deterministic engine as
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" <subcommand>`.
- Commands are thin: they orchestrate and point at the matching **skill** for
  methodology. Keep heavy guidance in skills, not commands.

Per-command spec:

| File | `description` | Body must do |
|---|---|---|
| `charter.md` | "Run a discovery session and write the shared-understanding charter." | Invoke the `kuru-method` + (a new) charter guidance; interview the user for problem, stakeholders, success metrics, constraints, non-goals; write/update `.kuru/charter.md`. If no `.kuru/` exists, instruct running `kuru init` first (offer to run it). |
| `prd.md` | "Turn the charter into a PRD for a feature/epic." | Use the `writing-prds` skill. Read `.kuru/charter.md`. Dispatch the **kuru-planner** subagent to draft `.kuru/prd/<feature>.md`. Argument = feature name. |
| `slice.md` | "Decompose a PRD into vertical slices with frozen contracts." | Use the `slicing-work` skill. Read the named PRD. For each slice: `kuru new-slice "<title>"`, then fill `slice.md` + `contract.yml`, then `kuru set-status <id> ready`. Argument = PRD/feature name. |
| `build.md` | "Build the next ready slice (or a named one) via the builder subagent." | Use `building-a-slice`. Resolve target via `kuru next` or `$1`. Dispatch **kuru-builder** subagent on exactly one slice. After it returns, show `kuru show <id>`. |
| `verify.md` | "Independently verify a built slice against its frozen contract." | Use `verifying-a-slice`. **Must** dispatch the **kuru-verifier** subagent (a different agent than built it). Argument = slice id (default: first `built`). |
| `review.md` | "Code-review a verified slice and mark it reviewed." | Run the existing `/code-review` (high effort) on the slice's diff; summarize findings; if clean, `kuru set-status <id> reviewed --by reviewer`. |
| `status.md` | "Show the delivery dashboard." | Run `kuru ls` and `kuru next`; summarize what's blocked / awaiting verification; surface any failing gate-results.json. |
| `next.md` | "Print and start the next actionable slice." | Run `kuru next`; based on its status, suggest the matching `/kuru:*` command. |
| `bearings.md` | "Get your bearings at the start of a session (context reset recovery)." | The session-startup ritual: read `.kuru/progress.md`, `kuru ls`, recent git log, run `kuru doctor`; summarize where things stand and the single next action. This is the antidote to context-reset amnesia. |

### 3.3 Subagents (`agents/*.md`)

Each is a markdown file with frontmatter (`name`, `description`, optional
`tools`, optional `model`) and a system prompt body. These encode the
**separation of concerns**. Specs:

**`kuru-planner.md`** — expands a charter into an ambitious-but-grounded PRD, and
PRDs into candidate slice boundaries.
- `description`: "Plans enterprise features: charter → PRD and PRD → vertical slice boundaries."
- `tools`: `Read, Grep, Glob, Bash, Write, Edit, WebFetch`
- System prompt requirements:
  - Read the charter and existing code to ground scope in reality.
  - Produce PRDs per the `writing-prds` skill (problem, users, scope, explicit
    non-goals, functional reqs, **NFRs**: security/perf/observability/a11y/i18n
    as relevant, data model deltas, dependencies, risks, acceptance shape).
  - Never invent requirements the charter doesn't support; flag gaps as open
    questions rather than guessing.

**`kuru-builder.md`** — the generator. Implements exactly one slice.
- `description`: "Implements a single Kurukuru slice end to end, then runs gates and updates the build log. Does NOT self-certify."
- `tools`: `Read, Grep, Glob, Bash, Write, Edit`
- System prompt requirements:
  1. Read the slice's `slice.md` and `contract.yml` **and treat the contract as
     frozen** — if it's wrong, stop and surface it, do not silently change scope.
  2. Read `.kuru/progress.md` and relevant code to match existing patterns.
  3. Implement a **vertical** change: every layer needed for the acceptance
     criteria, plus tests, plus observability hooks the NFRs require.
  4. Append to `build-log.md`: decisions, files touched, how each acceptance
     criterion is satisfied, anything deferred.
  5. Run `kuru gate <id>`. If red, fix and re-run. Only when green:
     `kuru set-status <id> built --by builder`.
  6. **You may not set `verified`.** End by telling the orchestrator the slice is
     ready for an independent verifier. Resist "context anxiety" — do not declare
     done early to save tokens; if you can't finish, set `blocked` with a note.

**`kuru-verifier.md`** — the evaluator/gatekeeper. The crucial independent check.
- `description`: "Independently gatekeeps a built slice against its frozen contract using concrete evidence. Adversarial, not collaborative."
- `tools`: `Read, Grep, Glob, Bash` (note: **no Write/Edit of source** — it
  judges, it does not fix; it only writes `verification.md` via Bash/heredoc or
  is given Write but instructed to touch only `verification.md`).
- System prompt requirements:
  1. You did not build this. Assume nothing the builder claims; verify it.
  2. Re-run `kuru gate <id>` yourself; record the result. Green gates are
     **necessary but not sufficient**.
  3. For **every** acceptance criterion in `contract.yml`, obtain **concrete
     evidence**: a passing test name + output, an actual HTTP response, a log
     line, a screenshot from driving the running app (use the `verify`/`run`
     skills or Playwright/Puppeteer MCP if available). Evidence must be a fact
     you observed, not a restatement of the criterion.
  4. Write `verification.md` from the template: per-criterion PASS/FAIL with the
     cited evidence, plus any out-of-contract bugs found (granular, like
     "`fillRectangle` exists but never fires on mouseUp").
  5. Verdict:
     - All criteria PASS + gates green → `kuru set-status <id> verified --by verifier`.
     - Otherwise → `kuru set-status <id> rejected --by verifier` with a note
       listing exactly what failed. Be specific enough that the builder can act
       without re-reading your whole report.
  6. Do not negotiate the contract down to make it pass. If the contract itself
     is wrong, reject and escalate to the planner.

### 3.4 Skills (`skills/*/SKILL.md`)

Each skill is `skills/<dir>/SKILL.md` with frontmatter `name` + `description`
(the description is the routing signal — write it as "Use when …"). Bodies are
the methodology. **This is the highest-value content in the plugin** — write it
carefully; the bullets below are requirements, not filler.

**`kuru-method/SKILL.md`** — the spine.
- `description`: "Use when working in a Kurukuru workspace (.kuru/) or running any /kuru:* command. Explains the pipeline, the slice state machine, the artifacts, and the rules."
- Body must cover: the pipeline diagram (§1); the state machine and the hard
  rules (§1); the artifact map (§2); the principle that **machine truth lives in
  `kuru.py`/JSON, narrative lives in markdown**; the **context-reset** discipline
  (each phase is a clean handoff — read artifacts, don't rely on prior chat); the
  **separation rule** (builder ≠ verifier, always); and a quick-reference of
  every `kuru.py` subcommand.

**`writing-prds/SKILL.md`**
- `description`: "Use when turning a charter into a PRD for an enterprise feature."
- Body must teach: start from the charter, never a blank page; a good PRD states
  the **problem and the user**, the **measurable success criteria**, explicit
  **non-goals**, functional requirements, and **non-functional requirements**
  appropriate to production (security/authz, privacy/data-handling,
  performance/SLOs, reliability/failure modes, observability, accessibility,
  i18n, migration/backfill, rollout/flagging). Require an **"acceptance shape"**
  section: the kinds of evidence that will prove the feature works, which the
  slicing step turns into per-slice acceptance criteria. PRDs describe *what/why*,
  not *how*. Flag unknowns as open questions, don't paper over them.

**`slicing-work/SKILL.md`** — arguably the most important skill.
- `description`: "Use when decomposing a PRD into vertical slices sized for a single agent session."
- Body must teach the two opposing constraints and how to balance them:
  - **Small enough**: one slice fits comfortably in one session's context with
    room for implementation + gates + a buffer (avoid context exhaustion mid-
    build). Rule of thumb: if you can't hold the whole change + its tests in your
    head, it's too big — split it.
  - **Complete enough**: a slice is **vertical** — it cuts through every layer
    needed to deliver one observably-true behavior (e.g. DB → API → UI → test),
    and it carries **all context inline** so the builder never has to guess
    (relevant files, the pattern to follow, the data contract, the gates). A
    horizontal slice ("add all the DB tables") is banned because it can't be
    independently verified.
  - Each slice must be **independently verifiable**: its acceptance criteria are
    checkable facts. If you can't write a concrete acceptance criterion, the
    slice boundary is wrong.
  - **Sequencing**: order slices so each leaves the app in a shippable/clean
    state; prefer a thin end-to-end "walking skeleton" slice first, then
    thicken. Note dependencies between slices in `slice.md`.
  - The **contract is frozen at `ready`**: changing scope mid-build means a new
    slice or an explicit re-`draft`, never silent drift.
  - Include a worked example: a PRD broken into 3–5 well-formed slices with their
    one-line acceptance criteria.

**`building-a-slice/SKILL.md`**
- `description`: "Use when implementing a single Kurukuru slice."
- Body mirrors the builder subagent's procedure (§3.3) but as reusable
  methodology: read frozen contract → match existing patterns → vertical change +
  tests + observability → update build-log with how each AC is met → `kuru gate`
  green → `set-status built`. Emphasize: never edit the contract to fit the code;
  never set `verified`; resist context-anxiety early-stopping (set `blocked` with
  a note instead of faking done).

**`verifying-a-slice/SKILL.md`**
- `description`: "Use when independently verifying a built slice."
- Body mirrors the verifier subagent (§3.3): adversarial stance; re-run gates;
  per-criterion concrete evidence (test output / real responses / driven-app
  screenshots / logs); write `verification.md`; verdict `verified`|`rejected`
  with specifics; never soften the contract to pass. Include the line: "Evidence
  is something you observed, not something you restated."

---

## 4. The engine (`scripts/kuru.py`) — already written

Do **not** rewrite it. It is the single source of machine truth. Subcommands
(call as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" <cmd>`):

| Command | Effect |
|---|---|
| `init [--force]` | Scaffold `.kuru/` in cwd from `templates/`. |
| `new-slice "<title>" [--epic E]` | Create `SL-NNNN` + its artifact files; ledger entry `draft`. |
| `ls [--status S]` | Table of slices. |
| `show <id>` | Slice JSON + artifact presence. |
| `next` | The next actionable slice, in pipeline order. |
| `set-status <id> <status> [--note ..] [--by ..]` | Guarded transition; enforces gate + role rules. |
| `gate <id>` | Run configured gates; write `gate-results.json`; exit non-zero on fail. |
| `check <id>` | Read-only: may this slice reach `verified`? |
| `doctor` | Validate the workspace. |

Templates referenced by `init`/`new-slice` (§5) **must exist** or these commands
error. That is why §5 templates are part of "done."

---

## 5. Template files (`templates/*`)

These are copied verbatim (with `{{PLACEHOLDER}}` substitution done by
`kuru.py`'s `render()`) into target repos. Placeholders available: `{{ID}}`,
`{{TITLE}}`, `{{DATE}}`, `{{EPIC}}`, `{{PROJECT}}`. Write each file to satisfy the
spec; exact JSON is given where structure matters to the engine.

### 5.1 `templates/config.json`
Engine reads `gates`. Provide a sensible Node/TS default with a comment-free JSON
(JSON has no comments). The `_help` keys document fields.

```json
{
  "project": "{{PROJECT}}",
  "_help": "Edit 'gates' to match THIS repo. Each gate runs in the repo root; required gates must exit 0 before a slice can be verified.",
  "gates": {
    "typecheck": { "cmd": "npm run -s typecheck", "required": true, "timeout": 600 },
    "lint":      { "cmd": "npm run -s lint",      "required": true, "timeout": 600 },
    "unit":      { "cmd": "npm test -- --run",    "required": true, "timeout": 1200 },
    "build":     { "cmd": "npm run -s build",     "required": true, "timeout": 1200 }
  },
  "session_budget_hint": "A slice should fit one agent session: ~1 vertical behavior, < ~10 files touched, tests included."
}
```

### 5.2 `templates/charter.md`
Outline (headings required): Title (`# Charter — {{PROJECT}}`), generated `{{DATE}}`;
sections: **Problem**, **Who it's for / stakeholders**, **Why now**, **Success
metrics (measurable)**, **Constraints** (tech, compliance, deadlines),
**Non-goals**, **Open questions**. Each as a short prompt the user fills.

### 5.3 `templates/progress.md`
The cross-session narrative handoff. Headings: `# Progress — {{PROJECT}}`,
**Current state** (one paragraph), **Last session did**, **Next action** (single
most important thing), **Known issues / landmines**, **How to run / verify**
(point at `init.sh` or the run skill). Include a note: "Update this at the END of
every session — it is what the next session reads first (`/kuru:bearings`)."

### 5.4 `templates/workspace-readme.md`
Short `# .kuru/ workspace` explainer: what each file/dir is, the pipeline, and
"machine truth = ledger.json + gate-results.json; everything else is narrative."

### 5.5 `templates/slice.md`
The vertical-slice spec. Headings: `# {{ID}} — {{TITLE}}` (epic `{{EPIC}}`, `{{DATE}}`);
sections: **Goal** (the one observable behavior), **Why this is one slice**
(vertical + session-sized justification), **Context the builder needs**
(files/patterns/data contracts/links — inline, no guessing), **In scope**,
**Out of scope**, **Dependencies** (other slice ids), **Acceptance criteria**
(numbered `AC-1…`, each a checkable fact), **Gates** (which config gates apply).
Add a banner: "Frozen at status `ready`. Scope change ⇒ new slice or re-draft."

### 5.6 `templates/contract.yml`
The frozen definition-of-done, machine-adjacent (read by the verifier). Provide a
filled example structure:

```yaml
slice: "{{ID}}"
title: "{{TITLE}}"
frozen: false            # set true when status -> ready
done_definition: >
  Replace with one or two sentences: what is unambiguously true when this slice
  is done.
acceptance_criteria:
  - id: AC-1
    statement: "Replace: a concrete, observable fact (e.g. POST /things returns 201 + the created id)."
    kind: automated      # automated | manual | observed
    evidence_required: "Replace: the exact proof (e.g. test test_create_thing_201; or a driven-app screenshot of X)."
gates: [typecheck, lint, unit, build]
out_of_scope:
  - "Replace: things explicitly NOT in this slice."
```

### 5.7 `templates/build-log.md`
Headings: `# Build log — {{ID}} {{TITLE}}`; running list of **Decisions**,
**Files touched**, **How each AC is satisfied** (AC-id → what was done),
**Deferred / follow-ups**. Append-only in spirit.

### 5.8 `templates/verification.md`
The verifier's evidence record. Headings: `# Verification — {{ID}} {{TITLE}}`,
verifier + `{{DATE}}`; **Gate run** (paste `kuru gate` summary), then a
**Per-criterion findings** table: `AC-id | PASS/FAIL | evidence (observed fact)`;
**Out-of-contract bugs found**; **Verdict** (`verified` | `rejected`) + rationale.

---

## 6. Top-level docs

### 6.1 `README.md` (overwrite the empty stub)
Spec — sections required:
1. **What kuru is** (1 paragraph) + the pipeline diagram.
2. **Why** — the three articles and the one-line lesson taken from each (§1).
3. **Install** — how to add as a Claude Code plugin (local plugin dir / add to a
   marketplace); requires `python3`.
4. **Quickstart** — in a target repo: `kuru init` → edit `config.json` gates →
   `/kuru:charter` → `/kuru:prd <feature>` → `/kuru:slice <feature>` →
   `/kuru:build` → `/kuru:verify` → `/kuru:review`.
5. **The state machine + hard rules** (§1).
6. **Files** — the two trees from §2.
7. **Design principles** — context resets, work/judgment separation, deterministic
   gates, frozen contracts, evidence over assertion.

---

## 7. Implementation slices (build in this order)

Eat our own dog food: build kuru as kuru would. Each slice below is vertical and
independently verifiable. Do them in order; don't start one until the prior one's
acceptance criteria pass.

**SL-1 — Engine + templates boot (walking skeleton).**
Files: `.claude-plugin/plugin.json`, all of `templates/*` (§5). `scripts/kuru.py`
already exists.
Acceptance:
- `python3 scripts/kuru.py init` in an empty temp dir creates `.kuru/` with
  `config.json`, `ledger.json`, `charter.md`, `progress.md`, `README.md` — no
  errors (proves every template `init` needs exists and renders).
- `kuru doctor` reports healthy.
- `kuru new-slice "demo"` creates `SL-0001/` with all four artifacts and a ledger
  entry in `draft`.
- `kuru ls` shows it; `kuru next` reports it needs a contract.

**SL-2 — Status + gate enforcement proven.**
No new files; this validates the engine rules against a throwaway repo.
Acceptance (run in a temp repo whose `config.json` gates are trivial, e.g.
`{"echo": {"cmd": "true", "required": true}}` and one `{"cmd": "false"}`):
- Drive the slice to `verifying` (`ready → in_progress → built → verifying`).
  From there, `set-status SL-0001 verified` fails with "no gate run".
- After `kuru gate SL-0001` with a failing gate, `set-status … verified` still
  fails ("gate FAILED").
- With all gates passing, `set-status SL-0001 verified --by verifier` succeeds;
  `verified --by builder` is refused.
- An illegal jump (`draft → done`, or `built → verified` skipping `verifying`) is
  refused.
Document these four checks in the README's design section as the guarantees.

**SL-3 — Skills (the methodology).**
Files: all five `skills/*/SKILL.md` (§3.4). Acceptance:
- Each has valid frontmatter (`name`, `description` starting with "Use when").
- `kuru-method` contains the pipeline, state machine, hard rules, artifact map,
  and the `kuru.py` subcommand reference.
- `slicing-work` contains the small-enough/complete-enough tension, the
  vertical-not-horizontal rule, the frozen-contract rule, and a worked example.
- `verifying-a-slice` contains "evidence is something you observed, not restated"
  and the reject-don't-soften rule.

**SL-4 — Commands.**
Files: all nine `commands/*.md` (§3.2). Acceptance:
- Each has `description` frontmatter and a body that calls the right
  `kuru.py`/skill/subagent per the §3.2 table.
- `build.md` dispatches **kuru-builder**; `verify.md` dispatches **kuru-verifier**
  (a different agent); `bearings.md` performs the startup ritual.

**SL-5 — Subagents.**
Files: `agents/kuru-planner.md`, `kuru-builder.md`, `kuru-verifier.md` (§3.3).
Acceptance:
- Builder prompt forbids self-certifying `verified` and editing the contract.
- Verifier prompt is adversarial, re-runs gates, requires per-AC evidence, and
  writes `verification.md`; its `tools` exclude editing source.

**SL-6 — README + end-to-end dry run.**
Files: `README.md` (§6.1). Acceptance:
- README satisfies §6.1.
- A scripted dry run passes: in a temp Node-ish repo, `init` → `new-slice` → fill
  a trivial contract → `set-status ready` → `set-status in_progress` →
  `set-status built` → `gate` (green) → `set-status verifying --by verifier` →
  `set-status verified --by verifier` → `reviewed --by reviewer` → `done`, with no
  engine errors and `kuru ls` ending all-`done`.

---

## 8. Conventions for the implementing model

- **Match the engine.** The templates and statuses must agree with `kuru.py`
  (statuses list, template filenames in `read_template()` calls:
  `config.json`, `charter.md`, `progress.md`, `workspace-readme.md`, `slice.md`,
  `contract.yml`, `build-log.md`, `verification.md`). If a template filename
  doesn't match what `kuru.py` reads, `init`/`new-slice` will crash — that's your
  fastest correctness signal.
- **Frontmatter must be valid YAML** in every command/agent/skill file.
- **Keep commands thin, skills deep.** Don't duplicate methodology into commands.
- **No new dependencies.** Python stdlib + the user's existing project tooling
  only.
- **Verify each slice before moving on** using its acceptance criteria. The whole
  point of this plugin is not trusting "it looks done" — hold yourself to it.
- When unsure about a Claude Code plugin authoring detail (command/agent/skill
  frontmatter, `${CLAUDE_PLUGIN_ROOT}`), consult the official Claude Code plugin
  docs rather than guessing.
```
