---
description: Scaffold a Kurukuru workspace (.kuru/) in the current repo.
argument-hint: "[--stack node|pnpm|python|go|gradle|maven|cargo] [--profile FILE] [--force]"
---

Use the `kuru-method` skill for context.

## What this does

Runs `kuru init` to scaffold a `.kuru/` workspace in the current directory — creating
`config.json`, `ledger.json`, `charter.md`, `progress.md`, `README.md`, and `init.sh`.
It does **not** ask discovery questions; that is `/kuru:charter`'s job.

## Steps

1. **Parse $ARGUMENTS** for known flags:
   - `--stack <tool>` — one of `node` `pnpm` `python` `go` `gradle` `maven` `cargo`
   - `--profile <path>` — path to a reusable environment profile JSON
   - `--force` — re-scaffold files even if `.kuru/` already exists

2. **Check if `.kuru/` already exists.** If it does and `--force` was not passed,
   tell the user and stop — do not overwrite silently.

3. **Build the command** from the parsed flags and run it:

   ```
   python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" init [--stack STACK] [--profile PROFILE] [--force]
   ```

   Only include a flag if the user supplied it.

4. **Show the output** verbatim so the user can see what was created.

5. **Point to next steps:**
   - If a profile was loaded: "Run `/kuru:charter` — it will pre-fill from your
     profile, confirm the values, then write the authoritative `config.json`."
   - If a `--stack` was given but no profile: "Run `/kuru:charter` to tailor the
     gate commands to this repo, then `/kuru:prd <feature>` to start your first
     feature."
   - Otherwise: "Run `/kuru:charter` to set up the technical environment and write
     the first charter."

   Also remind the user of the optional `KURU_PY` env-var tip printed by the engine.
