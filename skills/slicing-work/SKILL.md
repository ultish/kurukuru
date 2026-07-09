---
name: slicing-work
description: Use when decomposing a spec into vertical slices sized for a single agent session. Covers the small-enough/complete-enough tension, vertical (not horizontal) slicing, frozen contracts, sequencing for a walking skeleton, and writing checkable acceptance criteria. This is the highest-leverage step in the harness.
---

# Slicing a spec into vertical slices

A slice is the unit of work a builder agent picks up and finishes in **one
session**. Getting slice boundaries right is the highest-leverage decision in the
whole harness: too big and the agent runs out of context and fakes done; too
small or horizontal and it can't be independently verified. Every slice must
satisfy **two opposing constraints at once**.

## Precondition: no open questions

Before you cut a single slice, confirm the charter's and the spec's **Open
questions** are resolved (answered inline, or explicitly `DEFERRED (non-blocking)`
with the user's agreement). If any blocking question remains, **stop and resolve it
with the user first**, then update the charter/spec. Slicing freezes the spec into
contracts; an unanswered question becomes a guess locked inside one. Resolving it
later means re-`draft`ing slices — the drift this harness exists to prevent.

## Constraint 1 — small enough (fits one session)
- One slice ≈ **one observable behavior**, a handful of files, with its tests.
  Rule of thumb: if you cannot hold the whole change *and* its tests in your head
  at once, it's too big — split it.
- Leave headroom. A session must fit implementation **plus** gates **plus** a
  buffer. A slice that exactly fills the window will get truncated and the agent
  will start cutting corners as it nears the limit ("context anxiety").
- Symptoms of an oversized slice: the acceptance criteria list grows past ~5; the
  word "and" keeps appearing in the goal; you're touching three unrelated
  subsystems.

## Constraint 2 — complete enough (verifiable without guessing)
- **Vertical, not horizontal.** A slice cuts through *every layer* needed to make
  one behavior observably true — e.g. migration → repository → API → UI → test —
  not "all the database tables" or "all the endpoints". Horizontal slices are
  banned because nothing about them can be independently verified; they leave the
  app in a non-shippable in-between state.
- **Carries its context inline.** `slice.md` must give the builder the files to
  touch, the existing pattern to follow, the data/API contract, and links to the
  spec section — so the builder never reverse-engineers intent. A slice that
  assumes the builder "just knows" will drift.
- **Independently verifiable.** Each acceptance criterion is a **checkable fact**,
  not a quality adjective. If you cannot write a concrete AC for a slice, the
  boundary is wrong — re-cut it.

### Acceptance criteria: write facts
| Bad (unverifiable) | Good (checkable fact) |
|---|---|
| "Login works well" | "POST /login with valid creds returns 200 + a session cookie; with bad creds returns 401" |
| "The list is fast" | "GET /items?limit=50 returns in < 200ms p95 in the load test" |
| "Errors are handled" | "When the upstream returns 500, the UI shows the error state and logs an `upstream_error` event" |

### Each AC must be satisfiable AND verifiable *in this environment*

Two failure modes turn a frozen contract into an endless build→verify→build loop, both
caught by the pre-build **contract critic** (`/kuru:check-contract`) — but cheaper to
avoid while writing:

- **Built by something.** Every AC must check a thing *this* slice builds (its in-scope —
  leave it untagged, it's "built-here") **or** a thing an earlier, already-`done` slice
  built (a regression/extension check after this slice touches its area — tag that AC
  `built_by: SL-XXXX` so the critic counts it as already-built). An AC that references a
  component **no slice builds** can never pass — the verifier finds nothing to check; if
  an AC needs something unbuilt, add it to this slice's in-scope or move it to another
  slice. **Don't misuse `built_by`:** it is *only* for an earlier done slice's work, never
  for this slice's own new behavior, and a forward dependency on a not-yet-done slice is
  `--depends-on`, not `built_by`. (The critic flags both mistakes, but they're cheaper to
  avoid here.)
- **Verifiable by an available method.** State evidence the verifier can actually obtain
  in the deploy topology (run `kuru env <id>`; honor its `verification_access`). If
  mongo/kafka live in-cluster and aren't reachable externally, an AC whose only evidence
  is "connect from the test runner and assert" is unverifiable here — phrase it as
  evidence obtained the way the env allows (e.g. exec into the app pod). The slicer knows
  the topology (the charter's resolved profile); bake the *method* into `evidence_required`
  so the verifier doesn't improvise the wrong kind of test.

### Required tooling/conventions become checkable ACs, not "use skill X"

If the charter's **Required tooling / conventions** names a skill, generator, or
reference setup a builder must use (e.g. "generate the Gradle build files with the
`setup-gradle` skill"), do **not** write the instruction "use skill X" as an
acceptance criterion. A builder can — and will — rationalize its way around an
unverifiable means ("it's just a template, I know the params"), because the harness
only enforces outcomes. Instead:

- **Write the AC as the checkable artifact the tool produces.** Not "uses the
  `setup-gradle` skill" but "`gradle/libs.versions.toml` pins versions via the
  catalog; `settings.gradle.kts` repositories point at the internal mirror, not Maven
  Central; `./gradlew --offline assemble` exits 0." Now a hand-rolled wrong version
  fails a gate and gets rejected, regardless of how it was built.
- **Prefer a gate over an AC for the deterministic facts.** If charter compiled the
  convention's checkable artifacts into a `setup-conformance` gate in `config.json`
  (a `grep`/`test` assertion), the setup slice's done-ness is enforced by the engine,
  not agent judgment — the strongest form, and it keeps holding as a regression guard
  on later slices. In that case the slice's AC can simply point at the gate
  ("`setup-conformance` and `build` are green"). Reserve hand-written ACs for the
  **judgmental** facts a grep can't capture (e.g. "the module layout follows the
  reference project"), which the verifier checks by inspection.
- **Name the skill in the slice's context as the *cheapest path* to that AC**, with
  the consequence of skipping it spelled out: "Generate these via the `setup-gradle`
  skill — it encodes the air-gap mirror, catalog, and plugin set; hand-written
  versions fail the offline gate." This removes the "I know the params" escape hatch
  without pretending the harness can force a skill invocation (it can't).
- This applies **most** on a greenfield/setup slice, where there's no existing code
  to copy — the convention is the only thing standing between the builder and
  improvisation. Make those ACs concrete enough that improvising is the *slower* path.

## Sequencing slices
- **Walking skeleton first.** The first slice should be a thin end-to-end path
  (one real flow through all layers), then later slices thicken it. This keeps the
  app shippable from slice 1 and surfaces integration risk early.
- **Greenfield caveat:** on a fresh repo the configured gates may be unable to pass
  until the toolchain exists — making them green is part of the walking-skeleton
  slice's job (its ACs should include it). Use `"required": false` in `config.json`
  only as a temporary warn-only measure, and flip it back once the gate can run.
- Order so that **every slice leaves the app in a clean, shippable state**. Never
  leave a half-migration between slices.
- Record cross-slice **dependencies** in `slice.md`. The builder of a later slice
  should be able to assume earlier ones are `done`.

## Check the contract, then freeze it
Write each slice's `contract.yml` while the slice is still `draft` (leave
`frozen: false`), then **run the contract critic before freezing** —
`/kuru:check-contract --all`. It flags the two failure modes that otherwise survive to
verify time: an AC **nothing builds**, and one **not verifiable in this environment**.
Because the slice is still `draft`, fix a flagged contract **in place** (rewrite it and
re-check) — no status churn, nothing is locked yet. Only once a slice is `CONTRACT OK`
do you freeze it: set `frozen: true` and `kuru set-status <id> ready`.

From that freeze moment the definition-of-done and acceptance criteria are **locked**.
If you later discover the scope was wrong (or the loop's pre-build re-check flags a slice
frozen in a prior session):
- re-`draft` the slice and re-cut it — `ready -> draft` is a legal transition, so this is
  a sanctioned, ledger-recorded re-cut (the contract critic's repair loop uses exactly
  this path),
- create a new slice for the extra scope, or
- retire it: `kuru set-status <id> dropped --note "<why>"` — `next` and the loop
  ignore dropped slices, and `dropped -> draft` resurrects one for a re-write
  (same id, so other slices' dependencies on it stay valid).

Note: `frozen` is a **discipline marker** the planner/verifier honor — the engine does
not enforce it. That is exactly why the rule matters: never let a builder **silently**
change the contract to match what it built. A re-cut through `draft` is not silent (it's
recorded in the ledger history); an in-place edit at `built` is — and that's the drift
this harness exists to prevent.

## Assign a gate target (monorepo only)
If `config.json` defines multiple gate targets — one per app/build flavor in the
repo (a gradle service, a pnpm web app) — each slice must say which one it builds,
because that decides **which gates run and in which directory**. You know this up
front: the charter defined the targets and the spec says which app each piece of
work touches. Pass it at creation: `kuru new-slice "<title>" --target web` (or fix
later with `kuru set-target <id> <name>`). A slice that forgets its target is caught
by `kuru doctor` and can't be gated. Single-target repos ignore this entirely.

## Worked example
spec: "Users can save and revisit search filters."

Cut into vertical, session-sized slices:
1. **SL — Save a filter (walking skeleton).** Migration for `saved_filters` +
   `POST /filters` + a "Save" button that persists the current filter.
   AC-1: POST returns 201 with the new id. AC-2: the row exists in the DB with the
   owning user_id. AC-3: clicking Save on a filter then reloading shows it in the
   list.
2. **SL — List & apply saved filters.** `GET /filters` + a dropdown that, when a
   saved filter is chosen, applies it to the current search.
   AC-1: GET returns only the current user's filters. AC-2: selecting one updates
   the results to match the saved query.
3. **SL — Delete a saved filter.** `DELETE /filters/:id` + a delete control.
   AC-1: DELETE removes only the owner's filter; another user's id returns 403.
   AC-2: after delete it's gone from the list and the DB.
4. **SL — Authz + audit hardening (NFR slice).** Enforce ownership on every
   endpoint and emit an audit log per mutation.
   AC-1: cross-user access returns 403 on create/list/delete. AC-2: every mutation
   writes an `audit_log` row with actor, action, target.

Each is vertical, fits a session, and has facts you can check. Create each with
`kuru new-slice "<title>"`, fill `slice.md` + `contract.yml`, then mark `ready`.
