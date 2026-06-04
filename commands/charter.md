---
description: Run a discovery session and write the shared-understanding charter.
argument-hint: "[optional: feature/topic to focus the discovery]"
---

Use the `kuru-method` skill for context.

First ensure a Kurukuru workspace exists. If there is no `.kuru/` directory in the
repo, tell the user and offer to run:
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" init`

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

**Technical environment (this drives `.kuru/config.json`).** First, **if
`.kuru/profile.json` exists** (the user ran `kuru init --profile <file>`), read it —
it pre-answers most of this. Confirm the values with the user instead of re-asking,
and use its `config`/`stack` for the gates and its `environment` block to fill the
charter. Otherwise interview for:
- **Language & version** (e.g. TypeScript/Node 20, Kotlin 2.0 / JDK 21, Go 1.22,
  Java 21, Rust). Pin versions where they matter (JDK target, Node major).
- **Build pipeline / tool** (npm, pnpm, gradle, maven, cargo, go) — this selects
  the config preset.
- **Deploy environment** (Kubernetes, Docker, VM, serverless) and, if Kubernetes,
  **deployment artifacts** (Helm charts, raw k8s YAML, the container registry).
- **Air-gapped / restricted?** — whether there's internet during build and whether
  internal package registries are in play. Keep it high-level here; the **exact
  endpoint URLs are asked last and are skippable** (see below).
- **Existing template or reference project** to copy conventions from (a path or
  repo). If one exists, read its build config and mirror its gate commands,
  registry settings, and project layout instead of inventing them.

Ask follow-ups where answers are vague — a charter full of guesses is worthless.
When you have enough, write/update `.kuru/charter.md` (use its template sections,
including **Technical environment**).

**Then configure the gates for this stack.** Translate the build pipeline into
`.kuru/config.json`:
1. Seed it from the matching preset:
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" set-stack <tool>` where `<tool>`
   is one of `node` `pnpm` `gradle` `maven` `go` `python` `cargo` (this rewrites
   `.kuru/config.json` from `templates/config.<tool>.json`).
2. Then **tailor** the gate commands to this repo's reality: exact task/script
   names, the JDK/Node version, monorepo subpaths, and any air-gapped flags
   (`--offline` for gradle, `-o` for maven, `--offline`/`.npmrc` for pnpm, vendored
   crates for cargo). If the user pointed at a reference project, copy its commands.
3. Confirm with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py" doctor`.
If the build pipeline isn't one of the presets, write the four gates by hand to
match how this repo actually typechecks / lints / tests / builds.

**Air-gapped endpoints — ask LAST, and let the user skip.** If the environment is
air-gapped/restricted, then toward the *end* of the discussion ask (via
`AskUserQuestion`) for the concrete internal endpoints: package registry/mirror
URLs (`.npmrc` registry, Maven/Gradle mirror, `.cargo` source), the container
registry, and any required offline flags. **The user may skip these for later** —
if they do, record them in the charter's Technical environment as
`TBD — to provide` and do **not** block on them. If they're given, save them in the
charter (and they inform `.kuru/init.sh` and the builder). This is the only part of
the tech environment allowed to remain unresolved into slicing.

**Resolve open questions here — don't punt them downstream.** The charter is the
cheapest place to catch ambiguity. Before you finish, review the **Open questions**
section and, for each one, **ask the user** (use `AskUserQuestion` for discrete
choices). Fold every answer back into the relevant charter section and remove it
from Open questions. Only items the user *explicitly* chooses to defer may remain —
mark each as `DEFERRED (non-blocking): <why>`. A charter should not advance to a PRD
with unresolved questions that would change scope.

Then summarize the shared understanding back to the user and point them to
`/kuru:prd <feature>` as the next step.
