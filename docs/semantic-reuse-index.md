# Semantic reuse index — design exploration

> **Status:** exploration / not built. This is a continuation artifact — it captures
> where a design conversation landed so a fresh session can resume without
> re-deriving the context. The *reactive* half (a dupehound gate) is already shipped
> in this repo; the *proactive semantic index* described here is the open work.
>
> **Hard environment constraints:**
> 1. **Air-gapped** — no internet, no SaaS, no hosted embedding APIs, no data egress.
>    See [Air-gapped constraints](#air-gapped-constraints-hard-requirement).
> 2. **Polyrepo microservices, ~20 devs, partial clones** — no developer (or agent
>    session) has every service on disk. This makes the reuse problem fundamentally
>    **cross-repository** and is the dominant design driver. See
>    [Cross-service reuse](#cross-service-reuse-the-dominant-constraint).
> 3. **Stack: GitLab self-managed** — GitLab CI pipelines are the populate-on-merge
>    mechanism, and GitLab's own org-wide exact code search (Zoekt) is a strong candidate
>    for the central tier (likely already owned).

## Goal (north-star)

**Give every LLM session — across different sessions and different team members — a shared,
current understanding of the project: its structure, shared libraries, utilities, and
published surfaces.** So the whole team (humans + agents) builds *toward something common*
and stops rebuilding things that already exist. Reuse-prevention is the mechanism; **shared
project understanding is the goal.** That understanding must live in artifacts that any
session can load on demand — not in any one person's head or any one agent's context.

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
| **Committed manifest** | Generated `API_MAP.json` (symbol, import path, signature, one-line purpose, tags), read by the agent; sharded by domain | Proactive (read before write) | Small/medium repos (~hundreds of public symbols) | **git** — committed, refreshed in CI on merge. Git *is* the shared memory; no service. *(Monorepo assumption — superseded under partial clones; see [Cross-service reuse](#cross-service-reuse-the-dominant-constraint).)* |
| **Clone detector (dupehound)** | Normalized AST + winnowing fingerprints (MOSS algorithm); rename-resistant; flags structural duplicates | Reactive (catch in CI / verify, or edit-time via its MCP mode) | Any size | A binary; gate runs in the target repo. **Already wired in this repo.** |
| **Semantic index** ← *this doc* | Embeddings over `purpose` + `signature`, vector search, exposed as an MCP tool | Proactive *and* intent-based | Huge / polyglot | **Self-hosted** infra (vector DB CI populates on merge) — embeddings don't live in git. The one place you need a service. *Per-dev/local needs none (on-device embeddings); only the team-shared form needs a server — air-gapped means self-hosted, never cloud.* |

### Decision rule

- **Small/medium repo → committed manifest.** Git syncs the team for free; every
  checkout has the current map; CI refreshes on merge. No infra.
- **Huge / polyglot → semantic index service.** CI keeps it warm. This is what the
  "can't scan the whole codebase" problem actually demands at scale, and it handles
  synonym/intent matching the manifest can't.
- **Either way, the ADR/brain policy layer is also git-committed**, so policy
  knowledge shares the same way.

> **This rule is overridden by the polyrepo/partial-clone reality below.** It assumes a
> monorepo or a fully-cloned working tree. When no one has every service on disk, the
> "small → manifest + grep" tier can't see across services, and a **central self-hosted
> index becomes required, not optional** — independent of repo size. Read the next
> section before applying the rule above.

## Cross-service reuse (the dominant constraint)

**Setup:** ~20 developers, polyrepo microservices, partial clones — a given dev has 2–3
of N services checked out, never all of them. This is the single most important fact for
the design, because it **breaks the local-only tiers**:

- **Agentic grep is bounded by what's cloned.** It can only search the services on disk,
  so it structurally cannot find that *another* service already exposes the thing. The
  ">90% of RAG without a vector DB" finding (below) assumes a full local checkout — it
  does **not** hold under partial clones.
- **A per-repo committed manifest has the same blind spot** — a dev only has the
  manifests of their cloned repos.
- **dupehound has it too** — it scans the local working tree, so cross-service structural
  clones living in uncloned repos are invisible to it.

The duplication you most care about is therefore **cross-service**: service A reimplements
a helper that service B's shared lib already exposes, or two teams independently build the
same thing. None of the local-only tools can catch it. **So you need a central, shared,
org-wide index that any dev/agent can query regardless of what's cloned** — and that is no
longer "premature optimization for huge scale," it's required on day one.

### What the central index should index

In microservices the reusable surface is sharply defined — index the **inter-service
public surface**, not every internal function:

1. **Published shared-library exports** — the utilities meant to be reused.
2. **Service API contracts** — OpenAPI / protobuf / gRPC definitions (the "do we already
   have an endpoint that does X?" case, which is half of microservice duplication).

### Shape

A three-**layer** hierarchy, **exact-first** (per [the agentic-grep analysis](#does-the-agentic-grep-argument-survive-here)):

1. **Local agentic grep** — keep it, within whatever service is cloned. Cheapest, most precise.
2. **Central exact search, agent-queried via MCP** — the cross-service reach. Two
   complementary forms, both lexical (no GPU, air-gap fine, self-hosted):
   - *Org-wide exact code search* over all services — **GitLab self-managed Zoekt** (likely
     already in your stack) or self-hosted Sourcegraph. Brute, high-precision; you must know
     the token.
   - *Curated public-surface registry* — each service's **GitLab pipeline** extracts its
     public surface (shared-lib exports + API contracts) on merge into one queryable place,
     intent-taggable. Scoped to what's *meant* to be reused; this is the artifact that most
     directly serves the [shared-understanding goal](#goal-north-star).
3. **Local-embedding semantic search** — the tail, for cross-team **naming divergence**
   (nomic-embed-code on a self-hosted vector DB). Add only when exact-search recall isn't
   enough; the case is *stronger* here than in a monorepo, since 20 people across teams name
   things more divergently than one team does.

All of it self-hosted inside the network — air-gapped is fine — and exposed to agents via
MCP so any session can query it regardless of local clones.

- **Cross-service structural dedup** (dupehound's job, but org-wide) needs a **central scan
  over a mirror of all services** — a GitLab CI job running dupehound against the aggregate,
  since no local checkout sees the whole corpus.

### Existing solutions for the cross-repo angle (possibly buy, not build)

Org-wide search across repos you don't have locally is a solved category — evaluate these
before building:

- **GitLab self-managed exact code search (Zoekt)** ✅ air-gap deployable — you **likely
  already own this in-stack**: org-wide ("global") code search across all projects since
  GitLab 16.11, runs fully offline. The most direct "buy/already-have" answer for the
  central *exact* tier, and it matches the exact-over-fuzzy preference. GitLab **Duo**
  semantic/AI search does **not** work air-gapped (needs cloud), so lean on Zoekt-exact.
  Your **GitLab pipelines** are the populate-on-merge mechanism for the curated registry.
- **Sourcegraph (self-hosted / Enterprise)** ✅ air-gap capable — its whole value prop is
  searching **across all repositories centrally**, on-prem, independent of local clones.
  The most direct fit for "find reuse across uncloned services." Recent versions favor
  keyword/structural over embeddings, which is fine (precision over fuzzy recall).
- **Backstage (Spotify, self-hosted)** ✅ — software catalog + **API registry**; catalogs
  services and their published API contracts. Covers the contract-surface half. Air-gappable.
- **Home-grown central registry** — only if you want it scoped tightly to reuse-gating +
  an MCP query tool that the kuru builder/verifier can call.

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

**Local vs shared (and what air-gap changes).** "Needs a service" is only true for the
*team-shared* form. A *per-developer* index can run **fully on-device** — embeddings
from a local model, a local vector store, no server, no egress (see codebase-memory-mcp
below). Air-gapped does **not** forbid a shared index; it forbids *cloud*. A shared index
is fine if the vector DB and CI runners sit **inside the network** and the embedding
model is **local weights**. So the earlier "central infra" really means "self-hosted
infra," and only for the team-shared tier.

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

## Existing solutions (2026 survey)

Semantic search over a codebase is now **commoditized**; the *public-surface
reuse-gate* framing is **not** (every tool below indexes all code chunks for context
retrieval, none scopes to the public API surface for dedup). Adopting one gives you the
retrieval engine for free; the reuse-gate wrapper (build-time intent query + verify-time
dup flag, scoped to exported symbols) stays yours — and it's the small, novel part.

Air-gap legend: ✅ runs fully local/offline · ⚠️ local-capable with config · ❌ cloud/SaaS only.

**Drop-in MCP servers** (wire into a target repo like `dupehound mcp` would):
- **codebase-memory-mcp** (DeusData) ✅ — knowledge graph + **on-device `nomic-embed-code`
  embeddings compiled into a single static binary** (no API key, no Docker, no service,
  no egress), 158 languages, sub-ms queries, ~120x fewer tokens claimed. The standout for
  air-gapped: zero infra, fully offline, per-dev.
- **Claude Context / Code Context** (zilliztech) ⚠️ — hybrid BM25 + dense-vector search
  built for Claude Code; needs a vector DB (Milvus/Zilliz) + an embedding model.
  Self-hostable with **local Milvus + a local/Ollama embedder**, but defaults lean on
  hosted pieces — verify the fully-local path.
- **CodeGrok MCP / DeepContext MCP** ⚠️ — AST parsing + vector embeddings pitched as
  "replace grep for agents." Check whether the embedder can be pinned fully local.

**Editor-grade** (prove the pattern; mostly not air-gappable):
- **Cursor** ❌ — Turbopuffer **cloud** vector store + hosted embedding model. Not air-gappable.
- **GitHub Copilot `@workspace`** ❌ — hosted.
- **Sourcegraph Cody** ⚠️ — enterprise can self-host on-prem; note recent versions have
  **deprecated embeddings in favor of keyword/agentic retrieval** (see evidence below).
- **GitLab Duo semantic code search** ❌ air-gap — needs cloud connectivity; does **not**
  run air-gapped. For GitLab, use **self-managed exact code search (Zoekt)** ✅ instead — see
  [Cross-service reuse](#cross-service-reuse-the-dominant-constraint).
- **Continue.dev** ✅ — open-source; `@codebase` indexing supports **local embedding models
  (Ollama/transformers) + a local vector store**. Air-gappable; good base for the
  team-shared tier if self-hosted.

**DIY building blocks** (all self-hostable / offline):
- Local embedding models: **nomic-embed-code**, UniXcoder/CodeBERT, Qodo-Embed, jina-code
  (local weights — **no** Voyage/OpenAI API in an air-gap).
- Local vector stores: **LanceDB**, **sqlite-vec**, **Qdrant** (self-hosted), **pgvector**,
  Milvus (self-hosted). **Not** Pinecone/Turbopuffer (cloud).
- Extraction: tree-sitter / LSP / `go doc` / Python `ast` — one extractor feeds both the
  manifest and the embeddings.

## Air-gapped constraints (hard requirement)

Target environment is **air-gapped**: no internet, no SaaS, no hosted embedding APIs, no
data egress. Two consequences:

1. **Air-gapped ≠ no infra.** A *shared, team-synced* index is still possible — just
   **self-hosted inside the network**: an internal vector DB (Qdrant/pgvector/Milvus)
   populated by internal CI runners. What's forbidden is the **cloud** (Turbopuffer,
   Pinecone, hosted Cursor/Copilot) and **hosted embedding APIs** (OpenAI, Voyage).
2. **Embeddings must come from local model weights** (nomic-embed-code, UniXcoder, …),
   run on-device or on an internal GPU box — never an API call out.

**Rules IN:** ✅ manifest + agentic grep (zero infra, zero egress — most attractive) ·
✅ dupehound (already offline/deterministic — perfect fit) · ✅ codebase-memory-mcp
(on-device, per-dev, no service) · ✅ Continue.dev or a self-hosted vector DB + local
embedder (for the team-shared tier).
**Rules OUT:** ❌ Cursor, Copilot, hosted Sourcegraph/GitLab, and any hosted-embedding-API
path.

## Build-vs-buy evidence (2026)

Two findings argue for **manifest + agentic grep first, embeddings only at the tail** —
which is doubly true air-gapped, since less running infra is less to self-host offline:

- **Claude Code deliberately rejected RAG/embeddings for agentic grep.** Its creator
  reported early versions used a local vector DB but agentic search consistently won on
  **precision** (grep is exact; embeddings add fuzzy positives), **freshness** (a prebuilt
  index drifts during editing), **zero infra**, and **privacy** (nothing leaves the machine).
- **Amazon Science (Feb 2026): agentic keyword search reaches >90% of RAG performance
  with no vector DB.** RAG's remaining edge is *specifically* conceptual search across
  **large repos with inconsistent naming** — which is exactly the Type-4 / synonym-reuse
  tail flagged above as the semantic index's unique value.

So the empirical picture *backs the two-tier rule* **for a monorepo / fully-cloned tree**:
a committed manifest + agentic grep covers ~90% of reuse prevention with no infra; the
semantic index earns its keep only at large scale with inconsistent naming.

**Caveat that dominates this project:** both findings assume the code is *on disk to
grep*. Under [polyrepo partial clones](#cross-service-reuse-the-dominant-constraint),
grep's recall is capped at what's cloned, so the ">90%" becomes ">90% of what's local" —
which misses precisely the cross-service duplication you most care about. There, a central
self-hosted index is the main event, not the tail. Don't build the *embedding* pipeline
first — but you do need the *central lexical registry* first.

### Does the agentic-grep argument survive here?

The Claude Code argument is really **"agentic *exact* search beats prebuilt *fuzzy* RAG"** —
not "never use a central index." Tested against partial-clone polyrepo, pillar by pillar:

- **Precision** — still true, but its *scope collapses* to the local clones; you can't grep
  a service that isn't on disk, so precision is **N/A for cross-service discovery**.
- **Freshness** — *weakens*. The drift concern is about uncommitted local edits. A central
  registry indexed **on merge** holds the published surface other services actually consume;
  there's no edit-drift, and it's *fresher* than a teammate's stale clone of another service.
- **Zero infra** — **breaks**: its premise is "the code is already local," which is false
  across N partially-cloned services. No zero-infra option sees across services.
- **Privacy** — about *cloud* embedding APIs; a **self-hosted air-gapped** index keeps
  everything in-network, satisfying the same goal.

So the *exact-over-fuzzy* preference survives; the *zero-infra / local-only* assumption is
exactly what this environment violates. **Reconciliation:** keep local agentic grep within a
cloned service, and give the agent an MCP tool that runs **exact search against a central
index** (GitLab Zoekt / Sourcegraph) — still agentic, still exact, just no longer capped at
local disk. Embeddings stay the tail. **The one place the tradeoff genuinely flips toward
embeddings:** cross-team **naming divergence** — exact search finds `formatMoney` only if you
search the right token; across 20 devs on separate services, divergent naming lowers
exact-search recall and raises the embedding tier's value (more than in Claude Code's
single-team monorepo context).

## Open questions — to explore next session

1. **Local embedding model choice.** Air-gapped rules out hosted APIs entirely, so this is
   purely a *local-weights* question: nomic-embed-code vs UniXcoder vs Qodo-Embed, run
   per-dev on-device vs on an internal GPU box. Latency/throughput for CI re-embedding.
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
9. **Central registry hosting.** Under partial clones the manifest **cannot** sync via
   per-repo git (you don't have the other repos) — it must be centrally aggregated. A
   dedicated GitLab project / package registry the pipelines push to, vs a small search
   service? Where does the ADR/brain layer live, and how do the layers (exact search,
   curated registry, embeddings, brain) compose at query time?
10. **Buy vs build the central tier.** Does self-hosted **GitLab Zoekt** global code search
    alone cover enough cross-service discovery to defer the curated registry and embeddings
    entirely? Pilot Zoekt first before building anything.
11. **Contract-surface extraction.** How to harvest OpenAPI / protobuf / gRPC across services
    into the registry — **Backstage** catalog vs a custom extractor — and what's the
    authoritative contract source per service.

## One-line summary

Because it's **polyrepo microservices with partial clones**, local-only tools (agentic
grep, per-repo manifest, local dupehound) can't see across uncloned services — so the real
answer is a **central, self-hosted, org-wide index of the inter-service public surface**
(shared-lib exports + API contracts), populated by each service's CI, queryable by any
agent via MCP regardless of clones. Evaluate **self-hosted Sourcegraph** (+ **Backstage**
for the API-contract half) before building — both are air-gap capable. Start lexical;
add **local-embedding** semantic search (nomic-embed-code on a self-hosted vector DB) for
the cross-team inconsistent-naming tail — air-gapped means **self-hosted, never cloud**.
Keep **dupehound** per-repo for within-service structural dedup (offline, shipped here),
and run it centrally over a mirror for the cross-service structural case. The novel piece
no tool provides is the **public-surface reuse-gate wrapper** that turns any of these into
a build-time/verify-time gate.

## Sources

- Claude Code Doesn't Index Your Codebase — https://vadim.blog/claude-code-no-indexing/
- codebase-memory-mcp (DeusData) — https://github.com/DeusData/codebase-memory-mcp
- Claude Context MCP (zilliztech) — https://www.augmentcode.com/mcp/claude-context-mcp-server
- Code Context MCP — https://www.pulsemcp.com/servers/code-context
- CodeGrok MCP — https://hackernoon.com/codegrok-mcp-semantic-code-search-that-saves-ai-agents-10x-in-context-usage
- DeepContext MCP — https://skywork.ai/skypage/en/deepcontext-mcp-server-ai-engineers/1980841962807820288
- How Cursor Actually Indexes Your Codebase — https://towardsdatascience.com/how-cursor-actually-indexes-your-codebase/
- Securely indexing large codebases (Cursor) — https://cursor.com/blog/secure-codebase-indexing
- Semantic & Agentic Search (Cursor Docs) — https://cursor.com/docs/agent/tools/search
- GitLab Duo semantic code search — https://docs.gitlab.com/user/gitlab_duo/semantic_code_search/
- GitLab exact code search (Zoekt) — https://docs.gitlab.com/user/search/exact_code_search/
- GitLab Zoekt integration / air-gapped deployment — https://docs.gitlab.com/integration/zoekt/
- Code search with Zoekt (GitLab Handbook) — https://handbook.gitlab.com/handbook/engineering/architecture/design-documents/code_search_with_zoekt/
- Backstage (software catalog + API registry) — https://backstage.io/
