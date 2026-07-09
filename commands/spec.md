---
description: Turn the charter into a production spec for a feature/epic.
argument-hint: "[optional: feature/topic — the spec file is auto-numbered spec-N]"
---

Use the `writing-specs` skill.

**Assign the spec id — never ask the user to name the file.** Specs are numbered
sequentially. Compute the next id by scanning `.kuru/spec/`:

```
n=$(ls .kuru/spec/ 2>/dev/null | grep -Eo '^spec-[0-9]+\.md$' | sed -E 's/spec-([0-9]+)\.md/\1/' | sort -n | tail -1); echo "spec-$(( ${n:-0} + 1 ))"
```

Call the result **`<id>`** (e.g. `spec-1`, `spec-2`, …). It is the filename stem and
the epic tag every slice under this spec will carry — nothing reads it for logic
(it's display-only in `kuru ls`), so it stays an opaque sequential handle by design.

**Establish the topic — ask which source, don't guess it.** The spec's *content*
comes from one of two places; `$ARGUMENTS`, if given, only *seeds* it and never names
the file. Pick the source explicitly:

- If the session holds no feature-relevant context yet (a cold `/kuru:spec`), skip the
  prompt and just discuss what to build.
- Otherwise **ask the user first** (use `AskUserQuestion`), before drafting anything:
  - **Use current session context** — synthesize the topic and requirements from what
    we've already discussed this session.
  - **Discuss the spec** — run a fresh discovery conversation to establish what this spec
    should cover, setting the session chatter aside as the basis.

Whichever source: **reflect the understood scope back and confirm it, then resolve
gaps as Open questions before drafting.** The session (or `$ARGUMENTS`) is a starting
draft, never the final word — a subagent will freeze this into contracts, so inferred
intent must be *confirmed*, not assumed. Never ask the user to name the file.

Read `.kuru/charter.md` first. Then dispatch the **kuru-planner** subagent to
draft the spec, grounded in the actual codebase. The spec must cover problem &
user, measurable success criteria, non-goals, functional requirements, the
applicable **non-functional** requirements (security/authz, privacy & audit,
reliability/failure modes, performance/SLOs, observability, a11y/i18n,
migration/rollout), data & interface deltas, dependencies & risks, and an explicit
**acceptance shape**.

Write it to `.kuru/spec/<id>.md` (the auto-assigned id — e.g. `spec-3.md` — not the
topic text). Tell the planner that exact path when you dispatch it.

**Gate: resolve open questions before slicing.** When the planner returns, walk the
user through **every** open question it surfaced — ask them directly (use
`AskUserQuestion` for discrete choices). Fold each answer back into the spec (and, if
it's a charter-level gap, update `.kuru/charter.md` too) and clear it from the Open
questions list. If a question is genuinely out of scope for now, keep it only with
the user's explicit agreement, marked `DEFERRED (non-blocking): <why>`.

Do **not** point the user to `/kuru:slice` while any blocking open question is
unresolved — slicing on top of unanswered questions bakes guesses into frozen
contracts. Only once Open questions are answered (or explicitly deferred) tell them
to run `/kuru:slice <id>` (the auto-assigned id, e.g. `/kuru:slice spec-3`).
