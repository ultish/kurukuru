---
name: checking-a-contract
description: Use when reviewing a Kurukuru slice's frozen contract BEFORE it is built (you are the contract critic), or when running /kuru:check-contract. Covers judging whether each acceptance criterion is satisfiable (something builds it — this slice or an earlier done slice) and verifiable in the target environment, classifying ACs, writing actionable flags, and the advisory verdict that routes a flawed contract back to re-slicing instead of wasting a build.
---

# Checking a contract (pre-build)

The most expensive place to discover an unsatisfiable contract is at **verify time**,
after a full build. The acceptance criteria are frozen at `ready`, and the verifier
derives its evidence path from their *wording* — so if the wording demands something no
slice builds, or evidence the environment can't produce, the slice can **never** pass,
and you only learn that after a build→verify loop is burned. This step moves that check
**left**: apply the verifier's skeptical lens to the *contract + slice plan* before any
build, so a bad contract is fixed by re-slicing while that's still cheap.

You **judge the contract, not code** — there is no built code yet. You do **not** edit
`contract.yml` or write source; the planner repairs the contract on your flags (the
same separation as the verifier never touching source).

## What you read (all four)

1. **`contract.yml` + `slice.md`** — the acceptance criteria, in-scope/out-of-scope,
   dependencies. The contract is what the verifier will hold you to; read it as they will.
2. **The cumulative done-state** — `kuru ls --json` for `done` slices, then their
   `contract.yml` / `build-log.md` for **what they delivered**. This is essential: an AC
   may verify something an **earlier** slice built (a regression or extension check after
   this slice lightly touches it). That is legitimate — do **not** treat "not built in
   *this* slice" as a flaw.
3. **`kuru env <id>`** — the target's deploy topology and `verification_access`: how the
   running system + its dependencies are reachable here, and what must NOT be assumed.
   This decides whether an AC's required evidence is obtainable at all.
4. **The repo** (Grep/Glob/Read) — to confirm whether a referenced component already
   exists (earlier done work) or is genuinely promised by this slice's in-scope.

## Classify every acceptance criterion

| Class | Meaning | Verdict |
|---|---|---|
| **built-here** | the thing it checks is in *this* slice's in-scope | OK |
| **built-by-earlier-done-slice** | it exists in the cumulative done-state; a legit regression/extension check | **OK — do not flag** |
| **built-by-nobody** | neither this slice nor any done slice provides it | **FLAG** |
| **not-verifiable-in-this-env** | the evidence its wording demands can't be obtained in this topology | **FLAG** |

Also flag: **tautological/ambiguous** wording a verifier can't turn into a concrete
test, and an **"and"-heavy** AC that spans scope this slice doesn't build (often two
slices wearing one AC).

The **built-by-nobody** and **not-verifiable-in-this-env** classes are the two failure
modes that cause endless build→verify→build loops — the first because the verifier finds
nothing to check, the second because it builds a test that can't run.

## Write actionable flags

A flag the planner can't act on without re-deriving the analysis is wasted. For each
flag, name **the AC**, **the missing/unreachable thing**, and **the concrete fix**:

- "AC-3 checks `OrderService.cancel`, which no slice builds — add it to this slice's
  in-scope, or point AC-3 at SL-0007 (done) which built it."
- "AC-2 requires connecting to mongo from a host runner, but `verification_access` says
  mongo is in-cluster only — restate the evidence as obtained by exec'ing into the app
  pod (`kubectl exec … mongosh`), which is reachable here."
- "AC-1 says 'works reliably' — not checkable; restate as the observable fact (status
  code, persisted row, emitted event)."

## Write the report, return the verdict (advisory)

Write `.kuru/slices/<id>/contract-review.md` (via a Bash heredoc — the critic has no
`Write` tool): the per-AC classification table with reasoning, the actionable flags, and
an overall line: `CONTRACT OK` or `CONTRACT FLAGGED (<n> issues)`.

This step is **advisory** — it does not change the slice's status or edit the contract.
Return the verdict in your final message so the caller routes it:

- **Manual:** the report tells you exactly what to fix. Re-cut with `/kuru:slice`, or do
  a targeted repair: `kuru set-status <id> draft`, fix `contract.yml`/`slice.md` per the
  flags, `kuru set-status <id> ready`, then re-run the check.
- **Autonomous (the loop):** a flagged contract routes back to the planner, which
  rewrites it from your flags, re-freezes, and re-runs you — bounded by a retry cap; if
  it can't converge, the slice is held/stuck and escalated, never re-sliced forever.

Be rigorous, not charitable. A clean verdict is a promise that a build won't be wasted
on a contract that can't pass — that promise is the entire point of running before build.
