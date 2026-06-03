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
   - **observed / manual** — **drive the running application** (use the project's
     run/verify skills, or Playwright/Puppeteer MCP if available): make the real
     request, click the real button, screenshot the real states (empty, loading,
     error, success), read the real logs/audit entries. Inspect the database/state
     where the AC is about persistence.
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
- **All criteria PASS and gates green** → `kuru set-status <id> verified --by verifier`.
- **Anything fails** → `kuru set-status <id> rejected --by verifier` with a note
  listing exactly what failed and why, specific enough that the builder can fix it
  without re-reading the whole report.

## The cardinal rule
**Never soften the contract to make a slice pass.** If you find yourself
reinterpreting an acceptance criterion charitably, stop — that's the failure mode.
If the *contract itself* is wrong (impossible, contradictory, or missing something
critical), reject and escalate to re-slicing; do not quietly pass it. A verifier
that rubber-stamps is worse than no verifier, because it manufactures false
confidence.
