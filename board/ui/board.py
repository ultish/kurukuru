"""Stdlib ANSI hierarchical board TUI (Grok-like, sparse, keyboard-first).

Consumes the event stream via on_event → viewmodel.apply_event.
Non-TTY callers should use PlainUI instead (CLI falls back automatically).
"""

from __future__ import annotations

import os
import queue
import select
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, TextIO

from board.ui.viewmodel import (
    BoardState,
    TreeRow,
    apply_event,
    empty_state,
    overview_rows,
    row_detail,
    selected_log_path,
)

# Optional termios (Unix). Windows falls back to line mode / plain.
try:
    import termios
    import tty
except ImportError:  # pragma: no cover
    termios = None  # type: ignore
    tty = None  # type: ignore


HELP_TEXT = """\
 keybinds
  j/k  ↓/↑     move selection
  h / ←        collapse target (or slice stage list)
  → / space    expand target (or slice stage list)
  Enter        drill into slice / stage (log tail)
  Esc          back to overview
  l            open selected stage log in $PAGER (or print path)
  w            toggle waiting / blocker filter
  p            pause starting new pipelines (in-flight continue)
  c            cancel stage (not yet — Phase 4)
  q            quit (confirm if run still active)
  ?            this help
"""


class BoardUI:
    """Interactive hierarchical board. Thread-safe on_event from worker threads."""

    def __init__(
        self,
        *,
        run_dir: Path | None = None,
        stream: TextIO | None = None,
        input_stream: TextIO | None = None,
        pause_event: threading.Event | None = None,
        tick_s: float = 0.15,
        log_tail_lines: int = 14,
    ):
        self.run_dir = Path(run_dir) if run_dir else None
        self.stream = stream or sys.stdout
        self.input_stream = input_stream or sys.stdin
        self.pause_event = pause_event  # set() => pause new starts
        self.tick_s = tick_s
        self.log_tail_lines = log_tail_lines

        self.state = empty_state()
        self._q: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._lock = threading.Lock()
        self._cursor = 0
        self._mode = "overview"  # overview | drill | help | confirm_quit
        self._waiting_filter = False
        self._drill_slice: str | None = None
        self._drill_stage: str | None = None
        self._status_msg = ""
        self._quit = False
        self._force_quit = False
        self._paused = False
        self._rows: list[TreeRow] = []
        self._old_term: list | None = None
        self._started = False

    # ── event ingress ──────────────────────────────────────────────────────

    def on_event(self, ev: dict[str, Any]) -> None:
        self._q.put(ev)

    def close(self) -> None:
        self._q.put(None)

    # ── main loop ──────────────────────────────────────────────────────────

    def run_loop(self, *, done: Callable[[], bool] | None = None) -> None:
        """Block until quit or (done() and drain complete). Restores terminal."""
        self._setup_terminal()
        self._started = True
        try:
            while not self._quit:
                self._drain_events()
                self._redraw()
                if done is not None and done() and self._q.empty() and self.state.finished:
                    # keep board up briefly so operator can inspect; wait for q
                    if self._wait_key(timeout=0.5) is None:
                        # still allow keys until q or auto-exit after idle
                        # stay until user presses q or another 30s max after finish
                        pass
                key = self._wait_key(timeout=self.tick_s)
                if key is not None:
                    self._handle_key(key)
                if done is not None and done() and self.state.finished and self._force_quit:
                    break
            # final drain + paint
            self._drain_events()
            self._redraw()
        finally:
            self._restore_terminal()
            self._started = False

    def drain_and_paint_once(self) -> None:
        """Non-interactive: apply queued events and print a static snapshot."""
        self._drain_events()
        text = self.render_text()
        print(text, file=self.stream, flush=True)

    # ── keys ───────────────────────────────────────────────────────────────

    def _handle_key(self, key: str) -> None:
        if self._mode == "help":
            if key in ("\x1b", "q", "?", " ", "\n", "\r"):
                self._mode = "overview"
            return
        if self._mode == "confirm_quit":
            if key.lower() in ("y", "\n", "\r"):
                self._quit = True
                self._force_quit = True
            else:
                self._mode = "overview"
                self._status_msg = "quit cancelled"
            return

        if key in ("?",):
            self._mode = "help"
            return
        if key in ("q", "Q"):
            if self.state.finished or self._force_quit:
                self._quit = True
                self._force_quit = True
            else:
                self._mode = "confirm_quit"
            return
        if key == "\x1b" and self._mode == "drill":
            self._mode = "overview"
            return
        if key == "\x1b":
            return

        if self._mode == "drill":
            self._handle_drill_key(key)
            return

        # overview
        # Note: plan lists both h/l expand and `l` open-log — we bind:
        #   h / ← collapse, → / space expand-toggle, l open log (footer: "l log")
        if key in ("j", "\x1b[B"):  # down
            self._cursor = min(self._cursor + 1, max(0, len(self._rows) - 1))
        elif key in ("k", "\x1b[A"):  # up
            self._cursor = max(self._cursor - 1, 0)
        elif key == "\x1b[C":  # right → expand
            self._expand(True)
        elif key == " ":  # space → toggle expand
            row = self._selected_row()
            if row and row.kind == "target":
                self._expand(not bool((row.meta or {}).get("expanded", True)))
            elif row and row.slice_id:
                sl = self.state.slices.get(row.slice_id)
                if sl:
                    sl.expanded = not sl.expanded
        elif key in ("h", "\x1b[D"):  # collapse
            self._expand(False)
        elif key == "l":
            self._open_log()
        elif key in ("\n", "\r"):
            self._enter_drill()
        elif key == "w":
            self._waiting_filter = not self._waiting_filter
            self._cursor = 0
            self._status_msg = (
                "filter: waiting/blockers only" if self._waiting_filter else "filter: all"
            )
        elif key == "p":
            self._toggle_pause()
        elif key == "c":
            self._status_msg = "cancel not yet supported (Phase 4)"

    def _handle_drill_key(self, key: str) -> None:
        if key in ("l", "L"):
            self._open_log()
        elif key == "c":
            self._status_msg = "cancel not yet supported (Phase 4)"
        elif key == "p":
            self._toggle_pause()
        elif key in ("j", "\x1b[B"):
            # cycle stages in drill
            self._drill_cycle_stage(1)
        elif key in ("k", "\x1b[A"):
            self._drill_cycle_stage(-1)

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self.pause_event is not None:
            if self._paused:
                self.pause_event.set()
                self._status_msg = "paused — no new pipelines will start"
            else:
                self.pause_event.clear()
                self._status_msg = "resumed — scheduler may start new pipelines"
        else:
            self._status_msg = (
                "pause requested (not plumbed to scheduler — in-flight continue)"
                if self._paused
                else "pause cleared"
            )

    def _selected_row(self) -> TreeRow | None:
        if not self._rows:
            return None
        if self._cursor < 0:
            self._cursor = 0
        if self._cursor >= len(self._rows):
            self._cursor = len(self._rows) - 1
        return self._rows[self._cursor]

    def _expand(self, expand: bool) -> None:
        row = self._selected_row()
        if not row:
            return
        with self._lock:
            if row.kind == "target" and row.target:
                t = self.state.targets.get(row.target)
                if t:
                    t.expanded = expand
            elif row.slice_id:
                sl = self.state.slices.get(row.slice_id)
                if sl:
                    sl.expanded = expand

    def _enter_drill(self) -> None:
        row = self._selected_row()
        if not row or not row.slice_id:
            if row and row.kind == "target":
                self._expand(not (row.meta or {}).get("expanded", True))
            return
        self._drill_slice = row.slice_id
        self._drill_stage = row.stage
        if not self._drill_stage:
            sl = self.state.slices.get(row.slice_id)
            if sl:
                active = sl.active_stage() or sl.last_stage()
                self._drill_stage = active.name if active else "build"
        self._mode = "drill"

    def _drill_cycle_stage(self, delta: int) -> None:
        from board.ui.viewmodel import STAGE_ORDER

        if not self._drill_slice:
            return
        sl = self.state.slices.get(self._drill_slice)
        if not sl:
            return
        names = [n for n in STAGE_ORDER if n in sl.stages] or list(STAGE_ORDER)
        cur = self._drill_stage or names[0]
        try:
            i = names.index(cur)
        except ValueError:
            i = 0
        self._drill_stage = names[(i + delta) % len(names)]

    def _open_log(self) -> None:
        if self._mode == "drill" and self._drill_slice:
            path = selected_log_path(
                self.state,
                TreeRow(
                    kind="stage",
                    key="x",
                    depth=0,
                    label="",
                    slice_id=self._drill_slice,
                    stage=self._drill_stage,
                ),
                str(self.run_dir) if self.run_dir else None,
            )
        else:
            path = selected_log_path(
                self.state,
                self._selected_row(),
                str(self.run_dir) if self.run_dir else None,
            )
        if not path:
            self._status_msg = "no log path for selection"
            return
        p = Path(path)
        if not p.is_file():
            self._status_msg = f"log not found: {path}"
            return
        pager = os.environ.get("PAGER") or ""
        self._restore_terminal()
        try:
            if pager:
                os.system(f"{pager} {shlex_quote(path)}")  # noqa: S605 — operator tool
            else:
                print(f"log: {path}", file=self.stream)
                try:
                    print(p.read_text(encoding="utf-8", errors="replace")[-8000:])
                except OSError as e:
                    print(f"(read error: {e})")
                print("[press enter]", end="", flush=True)
                try:
                    self.input_stream.readline()
                except Exception:
                    pass
        finally:
            self._setup_terminal()
        self._status_msg = f"log: {path}"


    # ── events ─────────────────────────────────────────────────────────────

    def _drain_events(self) -> None:
        while True:
            try:
                ev = self._q.get_nowait()
            except queue.Empty:
                break
            if ev is None:
                continue
            with self._lock:
                apply_event(self.state, ev)

    # ── render ─────────────────────────────────────────────────────────────

    def render_text(self) -> str:
        with self._lock:
            state = self.state
            if self._mode == "help":
                return self._render_help(state)
            if self._mode == "confirm_quit":
                body = self._render_overview(state)
                return body + "\n\n  quit? run still active — y confirm / any other key cancel\n"
            if self._mode == "drill":
                return self._render_drill(state)
            return self._render_overview(state)

    def _header(self, state: BoardState) -> str:
        rev = "review on" if state.review else "review off"
        rid = state.run_id or "?"
        backend = state.backend or "?"
        elapsed = ""
        if state.started_ts:
            elapsed = f"  ·  {state.started_ts}"
        fin = "  ·  DONE" if state.finished else ""
        pause = "  ·  PAUSED" if self._paused else ""
        return (
            f" kuru-board  ·  {backend}  ·  {rev}  ·  run {rid}{elapsed}{fin}{pause}"
        )

    def _footer(self, state: BoardState, detail: str) -> str:
        c = state.counts()
        keys = "j/k select · enter drill · l log · w filter · p pause · ? help · q quit"
        counts = (
            f"{c['running']} running · {c['waiting']} waiting · "
            f"{c['shipped']} shipped · {c['capped']} capped · {c['stuck']} stuck"
        )
        msg = self._status_msg or detail
        lines = [
            "─" * min(72, _term_width()),
            f" {counts}",
            f" {msg}" if msg else "",
            f" {keys}",
        ]
        return "\n".join(lines)

    def _render_overview(self, state: BoardState) -> str:
        self._rows = overview_rows(state, waiting_filter=self._waiting_filter)
        if self._cursor >= len(self._rows):
            self._cursor = max(0, len(self._rows) - 1)
        lines = [self._header(state), "─" * min(72, _term_width())]
        if not self._rows:
            lines.append("  (no slices in plan yet)")
        for i, row in enumerate(self._rows):
            prefix = " " * (row.depth * 2)
            # target rows already have chevron; others indent
            mark = "›" if i == self._cursor else " "
            lines.append(f"{mark}{prefix}{row.label}")
        detail = row_detail(
            state,
            self._selected_row(),
            str(self.run_dir) if self.run_dir else None,
        )
        lines.append(self._footer(state, detail))
        return "\n".join(lines) + "\n"

    def _render_drill(self, state: BoardState) -> str:
        sid = self._drill_slice or ""
        sl = state.slices.get(sid)
        lines = [self._header(state), "─" * min(72, _term_width())]
        if not sl:
            lines.append(f"  (slice {sid} not found)")
            lines.append(self._footer(state, ""))
            return "\n".join(lines) + "\n"

        deps = ",".join(sl.depends_on) or "none"
        lines.append(
            f" {sl.id}  {sl.title or ''}  ·  target={sl.mutex_target}  "
            f"·  deps={deps}  ·  try {sl.tries}/{sl.max_tries}"
        )
        lines.append(f" ledger: {sl.ledger_status or '—'}  ·  {format_outcome(sl)}")
        from board.ui.viewmodel import pipeline_bar

        lines.append(f" pipeline: {pipeline_bar(sl)}")
        lines.append("─" * min(72, _term_width()))

        # stage list
        from board.ui.viewmodel import STAGE_ORDER, stage_glyph

        stage = self._drill_stage
        for name in STAGE_ORDER:
            st = sl.stages.get(name)
            if st is None and name in ("check", "repair"):
                continue
            g = stage_glyph(st)
            cur = "›" if name == stage else " "
            ls = f"  → {st.ledger_status}" if st and st.ledger_status else ""
            el = f"  {st.elapsed_ms}ms" if st and st.elapsed_ms is not None else ""
            note = f"  {st.note}" if st and st.note else ""
            lines.append(f"{cur} {g} {name}{ls}{el}{note}")

        # agent
        ag = None
        if stage and sl.stages.get(stage) and sl.stages[stage].agent:
            ag = sl.stages[stage].agent
        else:
            ag = sl.live_agent()
        if ag:
            pid_s = str(ag.pid) if ag.pid is not None else "—"
            lines.append(
                f" agent: role={ag.role}  backend={ag.backend}  pid={pid_s}"
                + ("  ● live" if ag.alive else "")
            )

        logp = selected_log_path(
            state,
            TreeRow(
                kind="stage",
                key="x",
                depth=0,
                label="",
                slice_id=sid,
                stage=stage or "build",
            ),
            str(self.run_dir) if self.run_dir else None,
        )
        lines.append(f" log: {logp or '—'}")
        lines.append("─" * min(72, _term_width()))
        # log tail
        if logp and Path(logp).is_file():
            try:
                text = Path(logp).read_text(encoding="utf-8", errors="replace")
                tail = text.splitlines()[-self.log_tail_lines :]
                for ln in tail:
                    lines.append(f" │ {ln[:120]}")
            except OSError as e:
                lines.append(f" │ (log read error: {e})")
        else:
            lines.append(" │ (no log yet)")
        lines.append("─" * min(72, _term_width()))
        lines.append(" [esc] back  [l] pager  [j/k] stage  [c] cancel  [q] quit")
        if self._status_msg:
            lines.append(f" {self._status_msg}")
        return "\n".join(lines) + "\n"

    def _render_help(self, state: BoardState) -> str:
        return self._header(state) + "\n" + HELP_TEXT + "\n [any key] back\n"

    def _redraw(self) -> None:
        text = self.render_text()
        # Clear screen + home cursor when interactive TTY
        if self.stream.isatty() and self._started:
            self.stream.write("\033[H\033[J")
        self.stream.write(text)
        self.stream.flush()

    # ── terminal ───────────────────────────────────────────────────────────

    def _setup_terminal(self) -> None:
        if termios is None or not self.input_stream.isatty():
            return
        fd = self.input_stream.fileno()
        self._old_term = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    def _restore_terminal(self) -> None:
        if termios is None or self._old_term is None:
            return
        try:
            fd = self.input_stream.fileno()
            termios.tcsetattr(fd, termios.TCSADRAIN, self._old_term)
        except Exception:
            pass
        self._old_term = None

    def _wait_key(self, timeout: float = 0.1) -> str | None:
        if not self.input_stream.isatty():
            time.sleep(min(timeout, 0.05))
            return None
        fd = self.input_stream.fileno()
        try:
            r, _, _ = select.select([fd], [], [], timeout)
        except (ValueError, OSError):
            time.sleep(timeout)
            return None
        if not r:
            return None
        try:
            ch = os.read(fd, 1).decode("utf-8", errors="ignore")
        except OSError:
            return None
        if not ch:
            return None
        # escape sequences (arrows)
        if ch == "\x1b":
            # peek rest
            try:
                r2, _, _ = select.select([fd], [], [], 0.02)
            except (ValueError, OSError):
                return "\x1b"
            if not r2:
                return "\x1b"
            rest = os.read(fd, 8).decode("utf-8", errors="ignore")
            return "\x1b" + rest
        return ch


def format_outcome(sl) -> str:
    from board.ui.viewmodel import format_wait

    return format_wait(sl)


def _term_width() -> int:
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).columns
    except Exception:
        return 80


def shlex_quote(s: str) -> str:
    import shlex

    return shlex.quote(s)


def board_available() -> bool:
    """True if we can run an interactive board on this process."""
    return bool(
        sys.stdout.isatty()
        and sys.stdin.isatty()
        and termios is not None
    )


def make_run_ui(
    ui_name: str,
    *,
    run_dir: Path | None = None,
    pause_event: threading.Event | None = None,
):
    """Factory used by CLI. Falls back to PlainUI when board is not viable."""
    from board.ui.plain import PlainUI

    if ui_name == "json":
        return None
    if ui_name == "board":
        if board_available():
            return BoardUI(run_dir=run_dir, pause_event=pause_event)
        # non-TTY → plain with a note on stderr
        print(
            "note: --ui board requires a TTY; falling back to plain",
            file=sys.stderr,
        )
        return PlainUI()
    return PlainUI()
