"""Streaming plain-text event UI (CI / default headless).

Interactive hierarchical board UI lives in the Ratatui binary (`kuru-board-tui` /
`scripts/board-tui.sh`), not in this package. Board run always uses plain or json
so it never fights the TTY when the Rust TUI is watching events.ndjson.
"""

from __future__ import annotations

import sys
from typing import Any, TextIO


def format_event(ev: dict[str, Any]) -> str | None:
    """Return a one-line summary, or None to skip noisy events."""
    t = ev.get("type", "")
    if t == "run.planned":
        n = len(ev.get("actionable") or [])
        w = len(ev.get("waiting_deps") or [])
        return f"planned: {n} actionable, {w} waiting on deps, review={'on' if ev.get('review') else 'off'}"
    if t == "run.started":
        return f"run started  {ev.get('run_id', '')}"
    if t == "run.finished":
        return (
            f"run finished  shipped={ev.get('shipped')}  "
            f"capped={ev.get('capped')}  stuck={ev.get('stuck')}"
        )
    if t == "slice.started":
        return f"  ▶ {ev.get('id')} start  (target={ev.get('target')})"
    if t == "slice.waiting":
        return f"  · {ev.get('id')} waiting ({ev.get('reason')}: {ev.get('detail')})"
    if t == "slice.finished":
        return (
            f"  ■ {ev.get('id')} {ev.get('outcome')}  "
            f"status={ev.get('status')}  {ev.get('reason') or ''}"
        ).rstrip()
    if t == "stage.started":
        return f"    {ev.get('id')} {ev.get('stage')} …  try={ev.get('try')}"
    if t == "stage.finished":
        return (
            f"    {ev.get('id')} {ev.get('stage')} → {ev.get('ledger_status')}  "
            f"({ev.get('elapsed_ms')}ms) {ev.get('note') or ''}"
        ).rstrip()
    if t == "commit.started":
        return f"commit: {ev.get('message')}"
    if t == "commit.finished":
        return f"commit: {'ok' if ev.get('ok') else 'skip/fail'} {ev.get('detail') or ''}"
    return None


class PlainUI:
    def __init__(self, stream: TextIO | None = None):
        self.stream = stream or sys.stdout

    def on_event(self, ev: dict[str, Any]) -> None:
        line = format_event(ev)
        if line is not None:
            print(line, file=self.stream, flush=True)


def make_run_ui(ui_name: str) -> PlainUI | None:
    """Factory used by CLI. ``json`` → no listener; anything else → PlainUI."""
    if ui_name == "json":
        return None
    return PlainUI()
