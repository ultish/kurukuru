---
description: Review a slice's frozen contract for satisfiability + verifiability BEFORE building it (advisory). Run after slicing to catch unbuildable/unverifiable acceptance criteria cheaply.
argument-hint: "[slice-id | --all]"
---

Use the `checking-a-contract` skill for context.

This runs **after slicing, before building**. It catches a contract no build could
satisfy — an acceptance criterion that nothing builds, or one that can't be verified in
this deploy topology — so it's fixed by re-slicing *before* a build→verify loop is
wasted. It is **advisory**: it writes a report and returns a verdict; it does **not**
change a slice's status or edit the contract.

**Resolve the target(s):**
- `$ARGUMENTS` is a slice id → check that one.
- `$ARGUMENTS` is `--all` (or empty right after a `/kuru:slice` run) → check every slice
  in status `draft` or `ready` (`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}"
  ls --status ready`, then `... ls --status draft`). This is the post-slicing batch.

It does **not** require a build, so a `draft` or `ready` slice is the normal input.

**Dispatch the kuru-contract-critic subagent** for each target slice. The critic:
- reads `contract.yml` + `slice.md`, the cumulative **done-state** (`kuru ls --json`
  plus done slices' contracts/build-logs), the target environment (`kuru env <id>`),
  and the repo,
- classifies every acceptance criterion (built-here / built-by-an-earlier-done-slice —
  a legit regression check — / built-by-nobody / not-verifiable-in-this-env),
- writes `.kuru/slices/<id>/contract-review.md` with actionable flags,
- returns `CONTRACT OK` or `CONTRACT FLAGGED (<n>)` with the essence of each flag.

**On the verdict:**
- **CONTRACT OK** → the slice is safe to build; point to `/kuru:build <id>` (or let the
  loop pick it up).
- **CONTRACT FLAGGED** → the contract needs a fix, and that means **re-slicing** (the
  contract is frozen at `ready`, so the fix goes through `draft`). Surface the flags and
  offer the two paths:
  - re-cut with `/kuru:slice` if the boundaries themselves are wrong, or
  - targeted repair: `kuru set-status <id> draft`, rewrite `contract.yml`/`slice.md`
    from the flags (dispatch **kuru-planner** to do this if you want it automated),
    `kuru set-status <id> ready`, then re-run `/kuru:check-contract <id>` until clean.

Report each slice's verdict plainly. A flagged contract that's built anyway will almost
certainly bounce at verify — that's the loop this step exists to prevent.
