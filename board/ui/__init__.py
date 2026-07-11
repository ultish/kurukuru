"""Board run UIs — plain event stream (CI / headless). Hierarchical board is Ratatui."""

from board.ui.plain import PlainUI, make_run_ui

__all__ = [
    "PlainUI",
    "make_run_ui",
]
