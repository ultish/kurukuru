---
name: verifying-a-slice
description: Use when independently verifying a built slice (you are the verifier/gatekeeper). Covers the adversarial stance, re-running gates, obtaining concrete per-criterion evidence by exercising the running app, writing verification.md, and the verdict rule — reject, don't soften the contract.
---

# Verifying a slice

You are the **evaluator**. You did not build this slice and you trust nothing the
builder claims. Your job is to decide — on **concrete evidence** — whether the
slice's frozen contract is actually satisfied. Building is collaborative;
verification is adversarial. That separation is the point.

## Stance
- Assume the builder is wrong until a fact proves otherwise.
- **Evidence is something you observed, not something you restated.** "AC-1 says
  POST returns 201, and it does" is not evidence. The 201 status line from an
  actual request you made, or the named passing test and its output, is evidence.
- Green gates are **necessary but not sufficient**. Tests can pass while the
  behavior is wrong, missing, or untested. Exercise the real thing.

## Running `kuru`

Where this skill writes `kuru <cmd>`, run
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" <cmd>` — `kuru.py`
ships in the plugin, not on your `PATH`, so a bare `kuru` will not resolve. If
neither env var is set, fall back to `python3 "$(cat .kuru/engine)" <cmd>` from the
repo root. The `kuru-method` skill has the full resolution order.

## Procedure

1. **Read the contract, not the build log first.** Open `contract.yml` and
   `slice.md`. Know the acceptance criteria before you read what the builder says
   it did — so its narrative doesn't anchor you.
2. **Re-run the gates yourself.** `kuru gate <id>`. Record the result. If red,
   the verdict is already `rejected`.
3. **Get concrete evidence for EVERY acceptance criterion.** Use the strongest
   evidence available for its `kind`:
   - **automated** — run the named test; capture the pass line and name. If the
     builder claims a test exists, confirm it actually exercises the behavior, not
     a tautology.
   - **observed / manual** — **drive the running application**. `Bash` covers most
     of it: `curl`/`http` the real endpoint, `kubectl`/`docker` against a deployed
     service, `psql`/`redis-cli` to read persisted state, `logs` for audit entries.
     Make the real request, read the real state, capture the actual output. For UI
     states that truly need a screenshot (empty, loading, error, success), use a
     browser-automation MCP **if your tools include one**; otherwise verify via the
     HTTP/API layer and cite that. Inspect the database/state where the AC is about
     persistence.
   - For NFRs (authz, audit, error handling): actively try to break them — call as
     the wrong user, trigger the failure path — and confirm the specified
     behavior.
4. **Hunt for out-of-contract bugs.** While exercising it, note granular defects
   beyond the ACs (e.g. "the fill tool's `fillRectangle` exists but never fires on
   mouseUp", "endpoint returns 422 on the documented payload"). These go in the
   report even when all ACs pass.
5. **Write `verification.md`** from the template: the gate summary, a per-criterion
   PASS/FAIL table with the **observed evidence** for each, the out-of-contract
   bugs, and the verdict.

## Verdict
The slice is in status `verifying` while you work (the `/kuru:verify` command
claims it out of `built` before you start). The engine only allows `verified` or
`rejected` from `verifying` — that is where your verdict lands:

- **All criteria PASS and gates green** → `kuru set-status <id> verified --by verifier`.
- **Anything fails** → `kuru set-status <id> rejected --by verifier` with a note
  listing exactly what failed and why, specific enough that the builder can fix it
  without re-reading the whole report.

**Recording the verdict is the deliverable — a prose verdict is not a verdict.** You
are not done until `kuru show <id>` reports `verified` or `rejected`. It is easy, after
a long run of integration scripts that all pass, to declare "verified" in your summary
and stop — but if you never ran `set-status`, the slice is still `verifying`: the gate
hasn't moved and your whole run will be redone. Make the `set-status` call the last
thing you do, then read `kuru show <id>` and confirm the status changed before you
report. This is the harness's core rule applied to you: facts that gate progress live
in the ledger, never in narration.

## The cardinal rule
**Never soften the contract to make a slice pass.** If you find yourself
reinterpreting an acceptance criterion charitably, stop — that's the failure mode.
If the *contract itself* is wrong (impossible, contradictory, or missing something
critical), reject and escalate to re-slicing; do not quietly pass it. A verifier
that rubber-stamps is worse than no verifier, because it manufactures false
confidence.
