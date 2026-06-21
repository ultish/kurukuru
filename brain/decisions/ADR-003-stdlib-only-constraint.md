# ADR-003: stdlib-Only Python — No Third-Party Dependencies

date: 2026-06-21
status: accepted
deciders: [[team:jxhui]]

## Context

`scripts/kuru.py` is the deterministic state and gate engine at the core of the
harness. It must run in any environment where a user wants to run kurukuru:
enterprise CI systems, air-gapped networks, minimal Docker images, developer
laptops with locked-down package management, and cloud agents that may not have
access to PyPI.

If `kuru.py` required third-party packages (e.g. `pyyaml`, `click`, `rich`), users
would need to run `pip install` before using the harness — and in air-gapped
environments, that install may be impossible without a private mirror. Any
dependency also becomes a version-pinning and security-audit burden.

`runner.py`, the standalone headless driver, shares the same constraint since it is
intended to work out of the box alongside `kuru.py`.

## Decision

`kuru.py` and `runner.py` must use **Python 3 stdlib only**. No third-party packages,
no `pip install`, ever. This is a hard constraint, not a preference.

Any feature that would naturally reach for a third-party library (YAML parsing,
rich terminal output, HTTP fetching for profile catalogs) must be implemented using
only stdlib primitives (`json`, `re`, `urllib.request`, `subprocess`, etc.) or must
be left to the caller (e.g. the slash commands) rather than the engine.

## Consequences

- The engine runs on any machine with `python3` — no setup step beyond what ships
  with the OS or a base Docker image.
- Air-gapped deployments work without a private PyPI mirror.
- Zero dependency surface: no CVE exposure from upstream packages, no version
  conflicts with the user's project tooling.
- Implementation is more verbose in places (e.g. YAML is parsed with regex heuristics
  or the engine avoids YAML-structured output entirely in favour of JSON; HTTP
  fetching for profile catalogs is handled with `urllib.request` rather than `requests`).
- Contributors cannot reach for convenient libraries. The constraint must be
  re-stated in `CLAUDE.md` so it is enforced in every session.

## Alternatives considered

**Allow a curated allowlist of stdlib-adjacent packages** (e.g. `tomllib` backport
for Python < 3.11). Rejected — "curated allowlist" is not a stable boundary; every
exception invites the next one. Hard "no third-party" is unambiguous.

**Ship a `requirements.txt` with a `pip install` step in `kuru init`.** Rejected —
this breaks the air-gap guarantee and adds a setup failure mode before the user can
run a single command.

**Use a bundler to inline dependencies at release time.** Rejected — adds a build
step, complicates auditing, and provides no benefit over just writing stdlib code in
a tool of this scope.

## See also
