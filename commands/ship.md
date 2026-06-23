---
description: Ship a verified (or reviewed) slice — set its status to done, which auto-commits (use --no-commit to defer the commit to the caller).
argument-hint: "<slice-id> [--no-commit]"
---

Use the `kuru-method` skill for context.

The terminal transition. Mark a slice `done` once it has passed verification (and
optional review). The engine only allows `done` from `verified` or `reviewed`, so this
is a no-op of judgment — the gate is whether the slice is already `verified`/`reviewed`,
not anything you decide here.

Resolve the target: use the slice id in `$ARGUMENTS` if given, otherwise run
`python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" ls --status verified` (then
`... ls --status reviewed`) and take the first one. If nothing is `verified`/`reviewed`,
STOP — there is nothing to ship; say so.

Then ship it:

```
python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" set-status <id> done
```

By default `set-status … done` **auto-commits** the whole working tree (slice code +
`.kuru/` artifacts + ledger) as one commit, so run it only when the tree is quiescent — no
other slice mid-edit.

**`--no-commit`:** if `$ARGUMENTS` includes `--no-commit`, pass it through —
`set-status <id> done --no-commit` — which flips the slice to `done` in the ledger but
makes **no git commit**, leaving that to the caller. This is what `/kuru:loop-workflow`'s
ship step always uses: many slices ship into one shared tree during a parallel run, so the
workflow defers to a single commit after the run rather than committing mid-flight. Without
the flag, the default (commit) holds — the right behavior for a human shipping one slice by
hand.

If the engine refuses (the slice is not `verified`/`reviewed`), that is a real signal: the
slice still needs `/kuru:verify` (or was already shipped). Do not hand-edit `ledger.json`
to force it.

When it returns, report the slice's new status (`show <id>`) and, unless `--no-commit`, the
commit. This command exists so an automated driver — `/kuru:loop-workflow` — has a single
`/kuru:*` verb for the ship step; humans can equally run the `set-status … done` directly.
