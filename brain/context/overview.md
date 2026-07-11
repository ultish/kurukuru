# Overview

## Purpose
kurukuru exists because AI coding agents are great at implementing things but poor
at staying honest across sessions. Without a harness, an agent will narrate its way
to "done" — claiming gates pass, claiming scope was met — without any machine check.
kurukuru enforces the discipline: facts that gate progress live in files the engine
controls, not in what the agent says.

The thesis: charter → spec → small vertical slices with frozen contracts → build
(one agent) → independent verify (a different agent) → review → done. A tiny stdlib Python
engine owns all state transitions and gate records. Agents can reason and write
prose; they cannot talk past the engine.

## Responsibilities
- Providing the `/kuru:*` Claude Code slash commands that drive the delivery pipeline
- The deterministic state + gate engine (`scripts/kuru.py`) that owns ledger.json and gate-results.json
- Three separated subagents (planner / builder / verifier) that encode the work/judgment split
- Methodology skills (kuru-method, writing-specs, slicing-work, checking-a-contract, building-a-slice, verifying-a-slice, reviewing-a-slice, loop-workflow)
- Templates that scaffold a `.kuru/` workspace inside any target repo
- A standalone headless driver (`runner.py`) for unattended autonomous runs

## Not responsible for
- The actual project being built — kurukuru is the harness, not the cargo
- Providing a Playwright MCP server (the verifier can use one if the user wires it up, but kuru doesn't bundle it — air-gap friendly)
- Third-party dependency management — stdlib only, forever
- The `.kuru/` workspace state (that belongs to the target repo, never committed into this plugin repo)

## Key stakeholders
- **Jimmy (jxhui)** — sole author and maintainer
- **Enterprise dev teams** using Claude Code who want production-quality delivery discipline across many sessions
- **The plugin ecosystem** — kurukuru is a Claude Code plugin; Claude Code's plugin discovery mechanism (`commands/`, `agents/`, `skills/`) is the integration point
