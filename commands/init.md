---
description: Scaffold a Kurukuru workspace (.kuru/) in the current repo.
argument-hint: "[--stack node|pnpm|python|go|gradle|maven|cargo] [--profile DIR|URL] [--reuse-check off|warn|block] [--force]"
---

Use the `kuru-method` skill for context.

## What this does

Runs `kuru init` to scaffold a `.kuru/` workspace in the current directory — creating
`config.json`, `ledger.json`, `charter.md`, `progress.md`, `README.md`, and `init.sh`.
It does **not** ask discovery questions; that is `/kuru:charter`'s job.

## Steps

1. **Parse $ARGUMENTS** for known flags:
   - `--stack <tool>` — one of `node` `pnpm` `python` `go` `gradle` `maven` `cargo`
   - `--profile <DIR|URL>` — a **catalog** of reusable single-stack environment
     profile JSONs (one file per build flavor). Point it at a local **directory**
     of `*.json` files, a single `.json` file, or an http(s) **URL** to a hosted
     catalog (GitHub/GitLab/Bitbucket). `/kuru:charter` matches each profile to an
     app. Stashed under `.kuru/profiles/`.
   - `--reuse-check off|warn|block` — seed a `dupehound check` duplicate-code gate
     into `config.json` top-level `repo_gates` (default `off`), so it runs repo-wide for
     every slice and survives the charter's conversion to a multi-app config. `warn` is
     advisory (WARN, never blocks); `block` is required (must be green or `kuru gate
     --waive`'d to verify). Needs the `dupehound` binary on PATH at gate time. Only pass
     it if the user asks for it.
   - `--force` — re-scaffold files even if `.kuru/` already exists

2. **Check if `.kuru/` already exists.** If it does and `--force` was not passed,
   tell the user and stop — do not overwrite silently.

3. **If `--profile` is a URL, materialize it to a local directory first.** A hosted
   catalog (GitLab/GitHub/Bitbucket) often sits behind auth.
   - **Prefer a skill.** Look for a user/project skill that knows how to fetch from
     that host (it owns the access tokens, SSO, base URLs, etc.). If one exists,
     use it to download the catalog's `*.json` profiles into a temp directory, then
     pass **that directory** as `--profile <tempdir>` below.
   - **Otherwise, hand the URL straight to the engine.** `kuru.py` can list a
     GitHub *contents* API or GitLab *repository-tree* API URL itself and reads
     `GITHUB_TOKEN` / `GITLAB_TOKEN` for private repos. (It has no Bitbucket
     fetcher — Bitbucket needs a skill, or download the profiles yourself.)
   For a local directory/file, skip this step.

4. **Build the command** from the parsed flags and run it:

   ```
   python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" init [--stack STACK] [--profile DIR|URL] [--reuse-check off|warn|block] [--force]
   ```

   Only include a flag if the user supplied it. `--profile` takes a single
   location (use the temp directory from step 3 if you materialized a URL).

5. **Show the output** verbatim so the user can see what was created.

6. **Point to next steps:**
   - If a profile was loaded: "Run `/kuru:charter` — it will pre-fill from your
     profile, confirm the values, then write the authoritative `config.json`."
   - If a `--stack` was given but no profile: "Run `/kuru:charter` to tailor the
     gate commands to this repo, then `/kuru:spec <feature>` to start your first
     feature."
   - Otherwise: "Run `/kuru:charter` to set up the technical environment and write
     the first charter."

   Also remind the user of the optional `KURU_PY` env-var tip printed by the engine.
