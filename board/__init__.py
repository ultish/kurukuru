"""Kurukuru board runner — agent-agnostic multi-slice orchestrator.

``python3 -m board plan|run|status|logs`` drives per-slice pipelines (mock /
claude / grok / cmd backends) and writes ``.kuru/runs/*/events.ndjson``.
Progress UI is ``plain`` (default) or ``json``; interactive hierarchical board
is the Ratatui binary (``scripts/board-tui.sh`` / ``kuru-board-tui``).
"""

__version__ = "0.1.0"
