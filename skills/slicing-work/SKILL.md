---
name: slicing-work
description: Use when decomposing a PRD into vertical slices sized for a single agent session. Covers the small-enough/complete-enough tension, vertical (not horizontal) slicing, frozen contracts, sequencing for a walking skeleton, and writing checkable acceptance criteria. This is the highest-leverage step in the harness.
---

# Slicing a PRD into vertical slices

A slice is the unit of work a builder agent picks up and finishes in **one
session**. Getting slice boundaries right is the highest-leverage decision in the
whole harness: too big and the agent runs out of context and fakes done; too
small or horizontal and it can't be independently verified. Every slice must
satisfy **two opposing constraints at once**.

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
  PRD section — so the builder never reverse-engineers intent. A slice that
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

## Sequencing slices
- **Walking skeleton first.** The first slice should be a thin end-to-end path
  (one real flow through all layers), then later slices thicken it. This keeps the
  app shippable from slice 1 and surfaces integration risk early.
- Order so that **every slice leaves the app in a clean, shippable state**. Never
  leave a half-migration between slices.
- Record cross-slice **dependencies** in `slice.md`. The builder of a later slice
  should be able to assume earlier ones are `done`.

## Freeze the contract
When a slice is ready to build, set `frozen: true` in `contract.yml` and
`keel set-status <id> ready`. From that moment the definition-of-done and
acceptance criteria are **locked**. If you discover the scope was wrong:
- re-`draft` the slice and re-cut it, or
- create a new slice for the extra scope.

Never let a builder silently change the contract to match what it built — that's
the exact drift this harness exists to prevent.

## Worked example
PRD: "Users can save and revisit search filters."

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
`keel new-slice "<title>"`, fill `slice.md` + `contract.yml`, then mark `ready`.
