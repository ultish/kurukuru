---
description: Decompose a PRD into vertical slices with frozen contracts.
argument-hint: "<feature-name>"
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
   evidence_required, out_of_scope) and set `frozen: true`.
4. `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-status <id> ready --note "contract frozen"`

Finish with `kuru ls` and tell the user to run `/kuru:build`.
