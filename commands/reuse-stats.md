---
description: Roll up builders' reuse-index lookups across slices (how often codebase-memory was consulted and led to reuse). Advisory, read-only.
---

Use the `kuru-method` skill for context (see the `reuse-stats` row in its engine command
reference, and `building-a-slice` §5 for the `REUSE-LOOKUP` line each build emits).

Run and present:
- `python3 "${KURU_PY:-${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py}" reuse-stats`

This rolls up the `REUSE-LOOKUP {json}` records the builder writes into each slice's
`build-log.md` — one per build — into a single view: how many built slices consulted the
[codebase-memory](https://github.com/DeusData/codebase-memory-mcp) reuse index, how often a
hit actually led to reuse (the reuse rate), semantic-fallback usage, and query/candidate
totals. It is **read-only** over the build-logs and touches no engine files.

Add `--json` if the user wants the machine-readable rollup (a `summary` object plus a
per-slice `reuse_lookup` record) instead of the text table.

Then give the user a short read of the numbers, not a dump:
- Coverage: how many built slices recorded a lookup, and call out any that emitted **no**
  `REUSE-LOOKUP` line (a built slice missing from the report means the builder skipped the
  step or the index was absent — the lookup is best-effort, so this is a gap to note, not a
  failure).
- The reuse rate (`led to reuse / index used`) and whether the semantic-query fallback is
  being leaned on.
- **Advisory only** — `reused`/`detail` are the builder's self-report; nothing here gates a
  slice. Frame it as a signal about whether the reuse index is earning its keep, not a
  verdict on any slice.
