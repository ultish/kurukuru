# ADR-002: Five-Place Documentation for Cross-Context Mechanisms

date: 2026-06-21
status: accepted
deciders: [[team:jxhui]]

## Context

The conventions → `setup-conformance` mechanism (where org-specific build conventions
from a profile are compiled into a `setup-conformance` gate in `config.json`) needs
to be understood by several different readers in separate, isolated contexts:

1. The **`/kuru:charter` command** — which interviews the user and writes `config.json`
2. The **`slicing-work` skill** — which teaches the builder how to encode conventions as checkable outcomes
3. The **`profile.example.json` template** — which ships into target repos alongside the workspace
4. The **`charter.md` template** — which structures the charter artifact in target repos
5. The **`slice.md` template** — which guides how slice contracts reference conventions

The critical constraint: **templates ship into target repos without the plugin docs**.
A user working in a target repo after a context reset only has `.kuru/` artifacts —
they cannot see `skills/` or `commands/` documentation. Similarly, a subagent loaded
with just the `slicing-work` skill has no access to `commands/charter.md`.

Centralising the documentation in one place (e.g. `kuru-method`) would leave most
readers without the information they need after a context reset.

## Decision

Document the conventions → `setup-conformance` mechanism in **all five places**
where a reader might encounter it cold, with each copy tailored to that reader's
perspective and context. The five places are:

- `commands/charter.md` — how to compile conventions into a gate during the charter session
- `skills/slicing-work` — how to encode a convention as a checkable artifact in a contract
- `templates/profile.example.json` — what the `conventions` block means and how it is processed
- `templates/charter.md` — where conventions appear in the charter artifact
- `templates/slice.md` — how a slice contract references a setup-conformance gate

If this mechanism changes, **all five places must be updated to agree**. This is
explicitly enforced in `CLAUDE.md` as a hard constraint.

## Consequences

- No reader is ever missing the information they need, regardless of which single
  artifact they encounter after a context reset.
- Maintenance cost: any change to the mechanism requires updating five files. The
  `CLAUDE.md` constraint makes this explicit so it isn't forgotten.
- The copies are not identical — each is written for its reader's context — so they
  can drift subtly. The hard constraint guards against semantic divergence.
- This pattern applies to any mechanism that crosses the plugin/template boundary or
  that multiple separated agents must understand independently.

## Alternatives considered

**Single canonical source in `kuru-method` skill, referenced everywhere else.**
Rejected because templates ship into target repos without the plugin's skills directory.
A user or subagent in a target repo after a context reset cannot load `kuru-method`
and would have no access to the documentation.

**Single canonical source with inline `@include` or similar.** Claude Code commands
support `@path` references, but agents and templates do not share the same mechanism.
A partial solution that works for commands but not templates would be worse than
full duplication because it would create false confidence that all readers are covered.

## See also
