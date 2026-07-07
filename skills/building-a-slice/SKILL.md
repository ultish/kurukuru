---
name: building-a-slice
description: Use when implementing a single Kurukuru slice (you are the builder). Covers reading the frozen contract, matching existing patterns, making a vertical change with tests and observability, updating the build log, running gates, and the rule that you never self-certify verified.
---

# Building a slice

You are the **generator**. Your job is to make exactly one slice's acceptance
criteria true, in production-quality code, then hand off to an independent
verifier. You build; you do not judge your own work.

## Running `kuru`

Where this skill writes `kuru <cmd>`, run
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" <cmd>` — `kuru.py`
ships in the plugin, not on your `PATH`, so a bare `kuru` will not resolve. If
neither env var is set, fall back to `python3 "$(cat .kuru/engine)" <cmd>` from the
repo root. The `kuru-method` skill has the full resolution order.

## Procedure

1. **Read the frozen contract.** Open `slice.md` and `contract.yml` for the slice.
   The contract is **locked** — do not change scope to match what's convenient. If
   the contract is genuinely wrong or impossible, **stop**, set the slice
   `blocked` with a note, and escalate to re-slicing. Do not quietly redefine done.
2. **Load the deploy topology.** Run `kuru env <id>` to read the slice's target
   environment — deploy env, dependencies, air-gap constraints, and
   `verification_access` (how the running system and its deps are actually reachable
   here). This shapes the tests and observability you write: build them to run in
   **this** topology, and never write a harness that assumes a dependency is reachable
   in a way the environment forbids (e.g. an external integration test against an
   in-cluster-only service — it can't pass and the verifier will reject it). No env
   recorded → note it and prefer tests that don't depend on unstated reachability.
3. **Get oriented.** Read `.kuru/progress.md` and the code/patterns the slice
   names. Conventions are something you **adopt, not assert**: where the codebase
   already has them (naming, error handling, test style), match them instead of
   inventing your own; where the slice context names a tool, skill, or reference
   setup to use, *that* is the convention — use it. This holds **especially** on a
   greenfield or setup slice, where "there's nothing to copy yet" is not license to
   improvise an equivalent because you "know the parameters" — the named tooling
   exists precisely because the details (mirror URLs, plugin versions, layout) are
   easy to get wrong by hand. If the named tooling genuinely seems wrong or
   unnecessary, you don't silently skip it — set the slice `blocked` with a note and
   escalate.
   - **Consult the reuse index before you author.** If a `codebase-memory-mcp` index
     exists for this repo, query it *before* writing any shared/util/common code or a
     new instance of an existing pattern — this is how you avoid rebuilding something
     that already exists and how you match house conventions instead of inventing them.
     It's **best-effort**: probe once with
     `codebase-memory-mcp cli list_projects '{}'` (resolve the `<project>` name there);
     if the binary or index isn't present, skip this and move on — never block a build
     on it.
     - *Does this already exist?* — before authoring a helper, search by intent:
       `codebase-memory-mcp cli search_graph '{"project":"<project>","query":"<what it does>","limit":8}'`.
       If a hit already does the job, **import or extend it** instead of writing a
       duplicate; if it's close but private, prefer promoting it over copying.
     - *How do we do X here?* — same query for a pattern ("graphql mutation",
       "db save transaction", "audit log event") → copy the established shape rather
       than a generic one. `get_code_snippet '{"project":"<project>","qualified_name":"<qn>"}'`
       pulls the full definition of a hit.
     - The default `query` is lexical (BM25) and is the dependable workhorse —
       **run it first.** `semantic_query` (a **keyword array**, e.g.
       `["serialize","persist","json"]`, scored per-keyword by cosine) is a **fallback
       for the divergent-naming case**: reach for it only when BM25 comes back empty or
       off-target *and* the repo is large enough that the team plausibly named the thing
       differently than you'd search for it. It's noisy on small repos, so treat its
       hits as leads to confirm by reading the code, never as authority. There's no
       config switch — it's your judgment call, per query.
4. **Make a vertical change.** Implement every layer the acceptance criteria need —
   data, service, API, UI — plus:
   - **Tests** that correspond to the acceptance criteria (a verifier will look
     for them by name).
   - **Observability** the NFRs require (logs/metrics/audit events).
   - Error and edge-case handling, not just the happy path.
5. **Keep the build log current.** Append to `build-log.md`: decisions and
   tradeoffs, files touched, and for **each AC** how it's satisfied and where the
   proof lives (test name, endpoint). This is what the verifier reads first.
   - **Record the reuse lookup as one machine-readable line** so the feature is
     measurable across slices. Emit exactly one line into `build-log.md`:
     `REUSE-LOOKUP {"used":<bool>,"queries":<int>,"candidates":<int>,"reused":<bool>,"semantic":<bool>,"detail":"<one line>"}`
     — `used`/`queries`/`candidates`/`semantic` are **facts you observed** (did you
     query, how many searches, how many hits came back, did you fall back to
     `semantic_query`); `reused` + `detail` are your **honest report** of whether a
     hit replaced new code (e.g. `"extended formatMoney instead of a new formatter"`).
     If the index wasn't available, write `{"used":false,...}` with zeros. One line
     per slice; aggregate later with
     `grep -h '^REUSE-LOOKUP' .kuru/slices/*/build-log.md`.
6. **Run the gates yourself.** `kuru gate <id>`. If red, fix and re-run until
   green. Green gates are the floor, not the ceiling. `kuru gate` streams each
   gate's output live **and** writes it to `.kuru/slices/<id>/gate-<name>.log`, so a
   long build (gradle, etc.) is watchable with `tail -f` and never looks "stuck".
   When you run a long build/test command *outside* the gate, do the same — never
   send its output to `/dev/null`; tee it to a log so progress is visible.
7. **Hand off.** When gates are green and every AC is genuinely met:
   `kuru set-status <id> built --by builder`. Tell the orchestrator it's ready for
   an **independent** verifier. **You may not set `verified`** — the engine will
   refuse it, and so should you.

## Disciplines
- **Never edit the contract to fit the code.** Drift is the failure mode this
  whole harness prevents.
- **No premature done.** If you're running low on context, set `blocked` with a
  precise note about what's left — do not declare victory to wrap up the session.
  A blocked slice with a good note is recoverable; a fake-done slice is a
  landmine.
- **Build-log as you go**, not at the end, so a context reset mid-slice loses
  little.
- Before you finish, re-read the acceptance criteria and check each one honestly.
  If one isn't truly met, you're not `built`.
