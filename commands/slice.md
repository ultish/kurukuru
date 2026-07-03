---
description: Decompose a PRD into vertical slices with frozen contracts.
argument-hint: "<prd-id>  (e.g. prd-3 — becomes each slice's epic tag)"
---

Use the `slicing-work` skill.

**First, gate on open questions.** Read both `.kuru/charter.md` and
`.kuru/prd/$ARGUMENTS.md`, including their **Open questions** sections. If any
question is still unresolved (not answered inline and not explicitly marked
`DEFERRED (non-blocking)`), **STOP — do not slice.** Surface each one to the user,
get the answer (use `AskUserQuestion` for discrete choices), update the charter/PRD
to fold the answers in and clear the question, and only then continue. Slicing on
top of unresolved questions freezes guesses into contracts — exactly what this
harness exists to prevent.

Once Open questions are resolved, read `.kuru/prd/$ARGUMENTS.md` and propose a set
of **vertical** slices — each one observable behavior, session-sized, carrying its
context inline, with checkable acceptance criteria, sequenced as a walking skeleton
first. Show the proposed boundaries and acceptance criteria to the user and refine
before materializing.

For each agreed slice:
1. `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" new-slice "<title>" --epic $ARGUMENTS`
   (add `--depends-on SL-000X,SL-000Y` for any slice that must wait on earlier
   ones — the engine then refuses to start it until those are `done`, and orders
   the loop safely. Sequence the walking-skeleton slice first, with no deps.)
   **If `config.json` defines multiple gate targets** (a monorepo with several
   apps), add `--target <name>` to say which app this slice belongs to — that
   decides which gates run, and in which directory. The PRD already says which app
   each piece of work touches, so you know this up front; `kuru doctor` flags a
   slice that forgot its target. (Single-target repos need no `--target`.)
2. Fill its `slice.md` (goal, why-one-slice, inline context, in/out of scope,
   dependencies, numbered acceptance criteria).
3. Fill its `contract.yml` (done_definition, acceptance_criteria with
   evidence_required, out_of_scope) but **leave `frozen: false` and the slice in
   `draft` for now** — you check the contract *before* freezing it (next), so a flaw is
   fixed in place with no freeze/unfreeze churn.

**Then check the contracts before freezing — this is the next step, not optional.** A
contract whose acceptance criteria reference something **no slice builds**, or demand
evidence the **deploy environment can't produce**, can never pass — and you'd only
discover it after a build is wasted. Catch it now, while the slices are still `draft`:

4. Run **`/kuru:check-contract --all`** — it dispatches the `kuru-contract-critic` over
   every `draft` slice you just cut. The critic reads each contract, the cumulative
   done-state, and each slice's target environment (`kuru env <id>`), and classifies
   every AC (built-here / built-by-an-earlier-done-slice — a legit regression check /
   built-by-nobody / not-verifiable-in-this-env), writing
   `.kuru/slices/<id>/contract-review.md`.
5. **For every `CONTRACT FLAGGED` slice, fix it in place and re-check** — this is the
   slice → check → slice → check loop. The slice is still `draft`, so just rewrite
   `slice.md`/`contract.yml` from the report's flags (dispatch **kuru-planner** if you
   want it automated) and re-run `/kuru:check-contract <id>`. No status change needed —
   nothing is frozen yet. Repeat until **every** slice is `CONTRACT OK`.
6. **Only once a slice is `CONTRACT OK`, freeze it:** set `frozen: true` in its
   `contract.yml`, then
   `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-status <id> ready --note "contract frozen"`.
   Do **not** freeze a flagged contract.

Finish with `kuru ls` and tell the user the slices are contract-checked, frozen, and
ready — run `/kuru:build` (or `/kuru:loop`). (The loops also re-check before a slice's
first build as a backstop; a flag there — e.g. on a slice frozen in a prior session — is
repaired via the sanctioned `ready → draft → rewrite → ready` cycle. But slicing,
pre-freeze, is where a flawed contract should be caught.)
