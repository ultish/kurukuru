---
name: reviewing-a-slice
description: Use when code-reviewing a verified Kurukuru slice (the review step, /kuru:review — on by default). Covers what review is FOR versus verify, the standards-source hierarchy (charter conventions first, gates already cover the rest, Fowler's code smells as the fallback baseline), scoping to the slice diff, and the verdict rule — reviewed, or reject back to the builder.
---

# Reviewing a slice

Review is the **quality axis**, and it is **on by default** (`kuru init` seeds it;
`kuru set-review off` disables it per workspace, and then a verified slice ships
straight to `done`). By the time a slice reaches here it is already `verified` — the
`kuru-verifier` proved, on concrete evidence, that the frozen contract is satisfied
(the *spec axis*). Review asks a different question: **is the code any good?**
Well-named, non-duplicated, maintainable, secure. A slice can be fully correct and
still be a mess worth fixing before it becomes part of the codebase other slices
build on. In the loop, a review rejection sends the slice back to the builder (it
counts as that slice's next build→verify→review try).

Do not re-run verification here. If you find yourself re-checking acceptance
criteria, you're in the wrong step — that was the verifier's job and it's done.

## Running `kuru`

Where this skill writes `kuru <cmd>`, run
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" <cmd>` — `kuru.py`
ships in the plugin, not on your `PATH`, so a bare `kuru` will not resolve. If
neither env var is set, fall back to `python3 "$(cat .kuru/engine)" <cmd>` from the
repo root. The `kuru-method` skill has the full resolution order.

## Scope to the slice's diff

Review **only what this slice changed** — read its `build-log.md` for the files it
touched, and review that diff, not the whole repo. A slice is one vertical change;
its review is bounded the same way. Reach outside the diff only to understand a
pattern the changed code should have matched.

## Standards-source hierarchy

Apply standards in this order — each layer overrides the one below it:

1. **The repo's documented standards win.** Primary source is the charter's
   **Required tooling / conventions** section and the resolved `profile.conventions`
   (run `kuru env <id>` to see the target's profile). "How we build here" rules are
   authoritative — a Fowler smell that the repo's own convention endorses is not a
   finding.
2. **Skip anything the gates already enforce.** Typecheck, lint, format, and tests
   ran as gates before this slice was `built`, and they'll run again. Do not spend
   review on style nits or lint-catchable issues — review earns its keep on what
   automated gates *can't* see: design, naming, duplication, security, and intent.
3. **Fall back to the code-smell baseline** (below) when no documented convention
   speaks to a case. It gives you a shared, citable vocabulary instead of "I'd have
   done it differently."

Always add the two things gates and smells both miss: **correctness under inputs
the tests didn't cover**, and **security** (authz, injection, secrets, unsafe
deserialization) — flag these whenever you see them, regardless of the hierarchy.

## Code-smell baseline (Fowler, *Refactoring* ch. 3)

Cite these by name so findings are unambiguous:

| Smell | The tell | Fix |
|---|---|---|
| **Mysterious Name** | A name doesn't say what it is/does | Rename until it does |
| **Duplicated Code** | Same logic in more than one place | Extract shared function |
| **Feature Envy** | A method uses another object's data more than its own | Move it to that object |
| **Data Clumps** | The same few fields travel together everywhere | Bundle into a type |
| **Primitive Obsession** | Strings/ints standing in for a domain concept | Introduce a domain type |
| **Repeated Switches** | The same switch/if-chain on a type appears repeatedly | Polymorphism or a shared map |
| **Shotgun Surgery** | One change forces edits across many files | Co-locate the scattered logic |
| **Divergent Change** | One module changes for many unrelated reasons | Split it by reason-to-change |
| **Speculative Generality** | Abstraction added for a need that never arrived | Delete the unused hook |
| **Message Chains** | `a.b().c().d()` navigation | Hide the walk behind one method |
| **Middle Man** | A class that only delegates | Call the target directly |
| **Refused Bequest** | A subclass ignores most of what it inherits | Prefer composition |

The baseline is a floor, not a ceiling — a real design problem that isn't on this
list is still a finding.

## Verdict — reviewed, or reject

A finding is worth acting on only if it would make the code meaningfully harder to
change, understand, or trust. Note trivia for the record, but don't gate on it.

- **Clean, or only trivial notes** → mark it reviewed:
  `kuru set-status <id> reviewed --by reviewer --note "<summary>"`
- **Real problems** → do NOT soften them into a note. Reject the slice back to the
  builder:
  `kuru set-status <id> rejected --by reviewer --note "<what to fix>"`
  From `rejected` it flows back through `/kuru:build <id>`, and the rejection counts
  toward the retry cap like a verifier rejection. (The engine allows
  `verified → rejected`; there is no `verified → in_progress`.)

Rejecting is cheap and honest; shipping a slice you know is bad is neither.
