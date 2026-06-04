---
description: Run a discovery session and write the shared-understanding charter.
argument-hint: "[optional: feature/topic to focus the discovery]"
---

Use the `kuru-method` skill for context.

First ensure a Kurukuru workspace exists. If there is no `.kuru/` directory in the
repo, tell the user and offer to run:
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" init`

Then run a **discovery conversation** with the user to build shared understanding
BEFORE any PRD. Focus: $ARGUMENTS

**How to ask:** whenever you have more than one question, use the interactive
`AskUserQuestion` UI (batch up to 4 related questions per call) rather than a wall
of prose — it's faster for the user and keeps answers structured. Fall back to
free text only for genuinely open-ended prompts.

Interview for, and do not assume:
- The problem and who has it; what it costs today.
- Stakeholders and sign-offs (eng, product, security, compliance, ops).
- Why now (the forcing function).
- **Measurable** success metrics.
- Constraints (compliance regime, SLOs, deadlines, budget).
- Non-goals.
- Open questions.

Ask follow-ups where answers are vague — a charter full of guesses is worthless.

**Technical environment — cover this as ONE group toward the END of the discussion.**
It's a separate topic from the problem/why-now above: it's *how* we build and
deploy, and it drives `.kuru/config.json`. The fields to establish:
- **Language & version** (e.g. TypeScript/Node 20, Kotlin 2.0 / JDK 21, Go 1.22,
  Rust). Pin versions where they matter (JDK target, Node major).
- **Build pipeline / tool** (npm, pnpm, gradle, maven, cargo, go) — selects the gate
  preset.
- **Deploy environment** (Kubernetes, Docker, VM, serverless) and, if Kubernetes,
  **deployment artifacts** (Helm charts, raw k8s YAML, the container registry).
- **Air-gapped / restricted?** — and, if so, the concrete **internal endpoints**:
  package registry/mirror URLs (`.npmrc` registry, Maven/Gradle mirror, `.cargo`
  source), the container registry, and any offline flags. **These endpoints are the
  one skippable part:** if the user wants to provide them later, record them in the
  charter as `TBD — to provide` and don't block. Everything else here should be
  answered.
- **Existing template or reference project** to copy conventions from. If one
  exists, read its build config and mirror its gate commands, registry settings, and
  layout instead of inventing them.

**If `.kuru/profile.json` exists** (the user ran `kuru init --profile <file>`),
treat it as **guidance, not gospel** — it's a head start on the fields above, not a
finished answer:
1. Read it and **summarize back to the user** what it implies for each field above
   (language/version, build tool, deploy env, air-gap endpoints, reference project,
   and any `config` gate commands it suggests).
2. **Hunt for gaps** — fields the profile leaves blank, stale, or ambiguous for
   *this* project — and ask the user only those (use `AskUserQuestion`). Confirm the
   pre-filled values rather than assuming them.
Otherwise (no profile), ask the whole group as one `AskUserQuestion` batch.

Write/update `.kuru/charter.md` (use its template sections, including **Technical
environment**), folding in every answer — including the air-gap endpoints and any
environment detail that doesn't belong in the gates. The charter is where the
*rest* of the profile lives.

**Then write the gates for this stack into `.kuru/config.json`.** This is where the
authoritative gate config gets set — the profile only informed it:
1. Seed it from the matching preset:
   `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-stack <tool>` — one of
   `node` `pnpm` `gradle` `maven` `go` `python` `cargo` (rewrites `config.json` from
   `templates/config.<tool>.json`).
2. Then **tailor** the gate commands to this repo: exact task/script names, the
   JDK/Node version, monorepo subpaths, and any air-gapped flags (`--offline` for
   gradle, `-o` for maven, `--offline`/`.npmrc` for pnpm, vendored crates for cargo).
   If the profile carried a `config` block, use its gate commands as the starting
   point here. If the user named a reference project, copy its commands.
3. Confirm with `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" doctor`.
If the build pipeline isn't a preset, write the four gates by hand to match how this
repo actually typechecks / lints / tests / builds.

**Resolve open questions here — don't punt them downstream.** The charter is the
cheapest place to catch ambiguity. Before you finish, review the **Open questions**
section and, for each one, **ask the user** (use `AskUserQuestion` for discrete
choices). Fold every answer back into the relevant charter section and remove it
from Open questions. Only items the user *explicitly* chooses to defer may remain —
mark each as `DEFERRED (non-blocking): <why>`. A charter should not advance to a PRD
with unresolved questions that would change scope.

Then summarize the shared understanding back to the user and point them to
`/kuru:prd <feature>` as the next step.
