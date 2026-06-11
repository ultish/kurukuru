# {{ID}} — {{TITLE}}

_Epic: {{EPIC}} · Created {{DATE}}_

> **Frozen at status `ready`.** Once this slice is `ready`, the contract below is
> locked. A scope change means a NEW slice or an explicit re-`draft` — never
> silent drift during a build.

## Goal
<!-- The ONE observable behavior this slice delivers. If you need "and" to
describe it, consider whether it's two slices. -->

## Why this is one slice
<!-- Justify both constraints:
  - Vertical: it cuts through every layer needed for the behavior (e.g.
    data -> API -> UI -> test), so it is independently verifiable.
  - Session-sized: it fits one agent session (~1 behavior, few files, tests). -->

## Context the builder needs (inline — no guessing)
<!-- Everything required to build WITHOUT exploring blind:
  - Files/modules to touch and the existing pattern to follow.
  - The data contract / API shape / types involved.
  - Required tooling/conventions: any skill, generator, or reference setup the
    builder MUST use (from the charter's Required tooling / conventions). Name it as
    the cheapest path to the relevant AC, and spell out the consequence of skipping
    it — e.g. "generate the Gradle build files with the `setup-gradle` skill; it
    encodes the air-gap mirror/catalog/plugins, and hand-written versions fail the
    offline build gate." Do NOT rely on this alone — the matching AC must be the
    checkable artifact, so a builder that ignores the skill still gets caught.
  - Links to the relevant PRD section and any prior slice it builds on.
A builder should not have to reverse-engineer intent from this section. -->

## In scope
<!-- Bullet the concrete changes. -->

## Out of scope
<!-- Bullet what this slice deliberately does NOT do. -->

## Dependencies
<!-- Other slice ids that must be done first, or "none". -->

## Acceptance criteria
<!-- Numbered, each a CHECKABLE FACT (not "works well"). These become the
contract. If you can't state one concretely, the slice boundary is wrong. -->
1. **AC-1** — <observable, checkable fact>
2. **AC-2** — <observable, checkable fact>

## Gates
<!-- Which config.json gates apply to this slice (default: all). -->
typecheck, lint, unit, build
