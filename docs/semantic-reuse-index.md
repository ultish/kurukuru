# Semantic reuse index — design exploration

> **Status:** exploration / not built. This is a continuation artifact — it captures
> where a design conversation landed so a fresh session can resume without
> re-deriving the context. The *reactive* half (a dupehound gate) is already shipped
> in this repo; the *proactive semantic index* described here is the open work.

## Why this exists — the problem

A coding-agent session's only working memory is its context window. Nothing persists
between sessions, and nothing is shared between one developer's session and a
teammate's. So every session starts amnesiac and can go wrong two ways:

1. **It doesn't know what exists** → it rebuilds a utility/function that's already there.
2. **It doesn't know the conventions** → it builds something that drifts from how the
   codebase does things.

This doc is about (1): preventing reinvention at scale. You can't paste the whole
codebase into context on every task — too big, too slow, too stale. You need a
smaller, queryable representation of "what already exists," and a way to query it by
*intent* ("is there anything that formats currency?") rather than by exact name.

## Two reframes that shape everything

1. **Index the public surface, not every function.** Indexing every function is huge,
   stale instantly, and mostly noise. What you want to prevent duplication of is the
   *reusable* surface: exported symbols, public module interfaces, deep-module APIs.
   That shrinks the problem 10–100x and aligns with good architecture — the index *is*
   your public-API catalog. If something reusable isn't on the public surface, the gap
   is a signal to **promote it**, not to copy it.

2. **It's generated from code, not remembered.** What exists and its signature is
   *derivable from the source*, so it should be a **build artifact**, not a memory an
   agent hand-writes (which rots). This is distinct from the brain/ADR layer:
   - **Brain / ADRs** = *non-derivable* knowledge (why we standardized on one HTTP
     client; the decision not to add a second auth path). Hand-written, human-refreshed.
   - **Reuse index** = *derivable* knowledge (what exists, its signature, its purpose).
     Machine-generated, refreshed by a build step.

   A reuse check uses **both**: the index to find *candidates*, brain/ADRs to know the
   *policy* ("we use X, don't add Y").

## The two-tier model

Reuse prevention has a **proactive** half (at write-time: "does something already do
this?") and a **reactive** half (at review-time: "did this new code duplicate
something?"). They are complementary, operate at different points in the lifecycle,
and use different mechanisms.

| Tier | Mechanism | Lifecycle | Scales to | Where it lives |
|------|-----------|-----------|-----------|----------------|
| **Committed manifest** | Generated `API_MAP.json` (symbol, import path, signature, one-line purpose, tags), read by the agent; sharded by domain | Proactive (read before write) | Small/medium repos (~hundreds of public symbols) | **git** — committed, refreshed in CI on merge. Git *is* the shared memory; no service. |
| **Clone detector (dupehound)** | Normalized AST + winnowing fingerprints (MOSS algorithm); rename-resistant; flags structural duplicates | Reactive (catch in CI / verify, or edit-time via its MCP mode) | Any size | A binary; gate runs in the target repo. **Already wired in this repo.** |
| **Semantic index** ← *this doc* | Embeddings over `purpose` + `signature`, vector search, exposed as an MCP tool | Proactive *and* intent-based | Huge / polyglot | A **shared service** (vector DB CI populates on merge) — embeddings don't live in git. The one place you need central infra. |

### Decision rule

- **Small/medium repo → committed manifest.** Git syncs the team for free; every
  checkout has the current map; CI refreshes on merge. No infra.
- **Huge / polyglot → semantic index service.** CI keeps it warm. This is what the
  "can't scan the whole codebase" problem actually demands at scale, and it handles
  synonym/intent matching the manifest can't.
- **Either way, the ADR/brain policy layer is also git-committed**, so policy
  knowledge shares the same way.

## The semantic index (the open work)

> Embeddings over `purpose` + `signature`, exposed as an MCP search tool:
> *"anything that does X?"*. This is what the "can't scan the whole codebase" problem
> actually demands at large/polyglot scale, and it handles synonym matching the
> manifest can't. Cost: it needs a shared service (a vector DB CI populates on merge)
> because embeddings don't live in git. That's the one place you do need central infra.

### What it indexes

Per public symbol, the same small entry the manifest uses — never the implementation:

```json
{
  "symbol":      "formatMoney",
  "import_path": "@acme/shared/currency",
  "signature":   "(cents: number, currency: Currency) => string",
  "purpose":     "Render an integer cent amount as a localized currency string",
  "tags":        ["money", "currency", "format", "i18n"]
}
```

The `purpose` line is load-bearing. Reuse failures are **semantic, not lexical**: an
agent writes `currencyToString` because it searched for "currency" and the existing
symbol was `formatMoney`. The embedding over `purpose` + `signature` is what bridges
*intent → existing code*. Garbage descriptions = useless index — invest there.

### Why a service, not git

The manifest is JSON and lives in git, so it syncs with the repo for free. **Embeddings
don't** — they're large, opaque float vectors that don't diff, and you want approximate
nearest-neighbor search over them, which needs a vector DB. So the semantic tier needs:

- A **vector store** (the shared service / central infra).
- **CI populates it on merge to main** — diff-driven, only re-embedding changed/added
  public symbols (incremental, so it's cheap and never stale).
- An **MCP search tool** the agent calls: free-text intent query → top-k candidate
  symbols with their import paths and purposes.

### What it catches that the other tiers can't

The clone taxonomy:
- dupehound (AST/winnowing) catches **Type 1–3**: exact, renamed, lightly-edited clones.
- It does **not** catch **Type 4**: *same behavior, genuinely different implementation*
  (different control flow/algorithm). An agent that reimplements currency formatting
  from scratch with different code sails right through a structural detector.

The semantic index is the thing that catches the **Type-4 / intent** case, because it
matches on *what the code is for*, not *how it's written*. It's also the only tier that
does true **pre-write prevention** — answering "does something already do this?" *before*
the agent writes the duplicate, saving the wasted generation.

## What's already built in this repo (the reactive half)

So a fresh session doesn't redo it — the dupehound integration shipped here:

- `kuru init --reuse-check off|warn|block` seeds a `dupehound check` gate into
  `config.json`. `warn` = advisory (WARN, never blocks); `block` = required.
- `kuru gate --waive NAME[=REASON]` moves a failing required gate forward for one run,
  recording the reason in `gate-results.json` (per-run, not persisted).
- The charter preserves the gate across `set-stack` rewrites.

That covers the **reactive, structural** half. The semantic index is the **proactive,
intent-based** half that's still open.

## How it would tie into kurukuru

The same "machine-checked gate, facts not narration" model applies:

- **Build-time (prevention):** the builder, before writing a new public symbol, calls
  the MCP search tool with intent ("I need to format currency") → gets candidates →
  imports/extends instead of authoring new.
- **Verify-time (the machine gate):** for each new exported symbol in the diff, query
  the index for near-duplicates above a similarity threshold → flag *"possible duplicate
  of `formatMoney`"* for the verifier. A flag for review, **not** an auto-reject
  (semantic similarity has false positives).

## Open questions — to explore next session

1. **Embedding model & hosting.** Local model vs hosted API? Cost per symbol, latency,
   and the privacy constraint of sending code/signatures to a third party. Does it stay
   stdlib-friendly enough to live near the kuru engine, or is it strictly target-repo infra?
2. **Vector store choice.** Lightweight/embeddable (sqlite-vec, LanceDB) vs a real
   service (pgvector, Qdrant). The "shared service" requirement vs the repo's
   "stdlib-only, no third-party deps" constraint — note this lives in the *target* repo's
   CI, not in `kuru.py`, same as dupehound.
3. **Staleness & freshness.** Incremental re-embedding on merge — keyed on what? Symbol
   hash? How to evict deleted symbols. How stale can it get before it misleads.
4. **Precision/recall tuning.** Similarity threshold for the verify-time flag; how to
   keep false positives low enough that the signal is trusted. Per-language calibration?
5. **Purpose generation.** Pull from docstring/JSDoc when present (forces devs to write
   them — good), fall back to an LLM pass over symbols that lack one, cached by hash.
   Quality of `purpose` determines the whole index's value.
6. **Overlap with dupehound.** dupehound already has structural dedup + an MCP mode.
   Where exactly does the semantic tier add value vs. duplicate effort? Likely:
   pre-write intent search + Type-4 catch only.
7. **Manifest → semantic graduation path.** Can the committed `API_MAP.json` be the
   *source* the CI job embeds, so a repo starts on the manifest tier and "graduates" to
   the service tier without re-instrumenting extraction?
8. **Polyglot extraction.** Tree-sitter to extract the public surface across languages
   (TS compiler API, Python `ast`, `go doc`, ctags fallback). Same extractor feeds both
   the manifest and the embeddings.
9. **Team/central memory model.** The manifest syncs via git; the semantic service is
   the one shared piece of infra. Confirm the ADR/brain layer stays git-committed and
   how the three layers (manifest, semantic index, brain) compose at query time.

## One-line summary

Small/medium → committed `API_MAP.json` manifest (git syncs the team for free).
Huge/polyglot → semantic index service (embeddings over purpose+signature in a vector
DB, CI-warmed, MCP-queried by intent — the one place you need central infra). dupehound
already handles the reactive structural half; the semantic index is the proactive,
intent-based, Type-4-catching half that's still to build.
