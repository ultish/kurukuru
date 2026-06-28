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
- **Required tooling / conventions** — org-specific "how we build here" rules that
  are NOT gate commands: skills, generators, or reference setups a builder must use
  (e.g. "generate the Gradle build files with the `setup-gradle` skill"). For each,
  also establish the **checkable artifact** it produces (e.g. "the version catalog
  file exists; repos point at the internal mirror; `--offline assemble` passes").
  Record these in the charter's **Required tooling / conventions** section. State the
  *outcome*, not just the means — the harness can only ever enforce an outcome, never
  "the agent invoked skill X". Sort each checkable artifact by how it gets enforced:
  - **Deterministic** (a file exists, a string is/ isn't present, a command exits 0)
    → it becomes a **`setup-conformance` gate** in `config.json` (see the gates step
    below). This is machine-checked and the strongest form.
  - **Judgmental** (e.g. "layout follows the reference project") → it stays a
    convention the slicer turns into an acceptance criterion the verifier checks.

**If `.kuru/profiles/` holds any profiles** (the user ran `kuru init --profile
<dir|url>` with a profile catalog), treat them as a **catalog of guidance, not gospel** —
each is one single-stack flavor (a gradle profile, a pnpm profile, …), and the user
may have passed more than apply here. It's a head start on the fields above, not a
finished answer:
1. **Match profiles to the apps in this repo.** From what the user describes, decide
   which profile(s) apply: a Kotlin/JVM service → the gradle profile; a web app →
   the pnpm profile. A repo with one app uses one profile (single-app, flat
   `config.json`); a polyglot repo uses several, one **gate target** per app (see the
   monorepo section below). Profiles that match nothing are ignored — say so.
2. For each matched profile, **summarize back to the user** what it implies
   (language/version, build tool, deploy env, air-gap endpoints, reference project,
   its `config` gate commands, and any `conventions` it carries — the required
   tooling/skills and their checkable artifacts).
3. **Hunt for gaps** — fields a profile leaves blank, stale, or ambiguous for *this*
   project, and the per-app **dir** the profile can't know — and ask the user only
   those (use `AskUserQuestion`). Confirm the pre-filled values rather than assuming
   them. For each `conventions` entry, confirm the rule **and** pin down its checkable
   artifact if the profile didn't state one — a convention with no checkable outcome
   can't be enforced downstream.
Otherwise (no profiles), ask the whole group as one `AskUserQuestion` batch.

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
3. **Compile the deterministic conventions into a `setup-conformance` gate.** For
   every Required-tooling/convention whose checkable artifact is deterministic (a
   file exists, a string is/isn't present, a command exits 0), add ONE
   `setup-conformance` gate whose `cmd` asserts them with stdlib shell — e.g.
   `test -f gradle/libs.versions.toml && grep -q nexus.internal settings.gradle.kts && ! grep -q repo.maven.apache.org settings.gradle.kts`.
   Make it `required` with a short timeout. Two rules: it must be a **cheap check
   (grep/test), never a rebuild** — the `build` gate already owns the expensive
   offline-assemble — and it runs on **every** slice that uses its gate set, which is
   correct: these are invariants, so it doubles as a regression guard. Do NOT wire it
   to read the profiles; a profile is guidance, this gate is the authoritative,
   executable form. Omit the gate if no convention is deterministically checkable.
4. **Preserve the reuse gate if `init --reuse-check` seeded one.** `set-stack`
   rewrites `config.json` from a preset, dropping any `reuse` gate that `init` added.
   If the seeded config had a `gates.reuse` (`dupehound check`) entry, re-add it after
   seeding — `{"cmd": "dupehound check", "required": <block?true:false>, "timeout": 600}` —
   in each target's `gates`. It's a repo-wide invariant, so it belongs in every target.
5. Confirm with `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" doctor`.
If the build pipeline isn't a preset, write the gates by hand to match how this
repo actually typechecks / lints / tests / builds.

**Multiple apps / build flavors in one repo (monorepo).** If the charter reveals
more than one build pipeline — e.g. a gradle/kotlin service in `services/api` and a
pnpm web app in `apps/web` — do **not** force one global gate set. Define a **gate
target per app** in `config.json`: each target has its own working `dir` and `gates`
(and its own `setup-conformance` invariant). Seed each from its preset without
clobbering the others, then set each `dir` and tailor:
```
python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-stack gradle --target api
python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-stack pnpm   --target web
```
**If the repo already has a single-app (flat `gates`) config** — e.g. a prior `init` or a
`set-stack` without `--target` — the **first** `--target` call refuses rather than silently
discard it, because going multi-app makes that flat `gates` dead config. Ask the user which they
want (use `AskUserQuestion`), then re-run that call with one flag:
`--discard-flat-gates` (it was just the init default) or `--migrate-flat-gates-to <NAME>` (keep
the existing config as its own app, target `<NAME>`, `dir "."` — then set its real `dir`).
This yields:
```json
{ "project": "mono",
  "targets": {
    "api": { "dir": "services/api", "gates": { "build": { "cmd": "./gradlew :api:build", "required": true, "timeout": 1800 } } },
    "web": { "dir": "apps/web",     "gates": { "lint": { "cmd": "pnpm lint", "required": true, "timeout": 600 } } }
  } }
```
Each slice then declares which target it belongs to (the `/kuru:slice` step assigns
it with `--target`), and `kuru gate <id>` runs **only that target's gates, in that
target's dir**. A single-app repo needs none of this — a flat top-level `gates`
still works and behaves as one implicit target. `kuru doctor` flags a slice with no
target once more than one target exists.

When `.kuru/profiles/` holds several profiles, each **matched** profile becomes one
target: its `stack`/`config` seeds that target's gates, you ask the user for its
`dir` (the profile can't know where the app lives in this repo), and its
`environment`/`conventions` fold into the charter as that app's section. As with
everything else in a profile, it's guidance, not gospel.

**Resolve open questions here — don't punt them downstream.** The charter is the
cheapest place to catch ambiguity. Before you finish, review the **Open questions**
section and, for each one, **ask the user** (use `AskUserQuestion` for discrete
choices). Fold every answer back into the relevant charter section and remove it
from Open questions. Only items the user *explicitly* chooses to defer may remain —
mark each as `DEFERRED (non-blocking): <why>`. A charter should not advance to a PRD
with unresolved questions that would change scope.

Then summarize the shared understanding back to the user and point them to
`/kuru:prd <feature>` as the next step.
