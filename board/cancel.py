"""Run-level cancel control: per-slice flags + active process tracking.

Used by the board TUI (`c` key), pipeline (stop further stages), and backends
(kill process group when a live Popen is registered).

Ledger is never mutated on cancel — the pipeline leaves status as-is and
reports outcome ``stuck`` with reason ``cancelled``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Callable


def kill_process_group(proc: subprocess.Popen, *, grace_s: float = 1.5) -> None:
    """SIGTERM the process group (if any), then SIGKILL. Best-effort."""
    if proc.poll() is not None:
        return
    pid = proc.pid
    if pid is None:
        return
    # Prefer process-group kill when the child was started with start_new_session.
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            return
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


class RunControl:
    """Thread-safe per-slice cancel flags and active subprocess registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel: dict[str, threading.Event] = {}
        self._procs: dict[str, subprocess.Popen] = {}

    def _event(self, slice_id: str) -> threading.Event:
        sid = slice_id.upper()
        with self._lock:
            ev = self._cancel.get(sid)
            if ev is None:
                ev = threading.Event()
                self._cancel[sid] = ev
            return ev

    def is_cancelled(self, slice_id: str) -> bool:
        sid = slice_id.upper()
        with self._lock:
            ev = self._cancel.get(sid)
            return bool(ev and ev.is_set())

    def cancel_check(self, slice_id: str) -> Callable[[], bool]:
        sid = slice_id.upper()
        return lambda: self.is_cancelled(sid)

    def request_cancel(self, slice_id: str) -> str:
        """Mark slice cancelled and kill its active process if known.

        Returns a short operator-facing status string.
        """
        sid = slice_id.upper()
        ev = self._event(sid)
        already = ev.is_set()
        ev.set()
        proc: subprocess.Popen | None
        with self._lock:
            proc = self._procs.get(sid)
        if proc is not None and proc.poll() is None:
            kill_process_group(proc)
            return f"cancel {sid}: signalled + killed pid={proc.pid}"
        if already:
            return f"cancel {sid}: already requested (no live process)"
        return f"cancel {sid}: requested (stops after current stage if no killable process)"

    def bind_process(self, slice_id: str, proc: subprocess.Popen) -> None:
        sid = slice_id.upper()
        with self._lock:
            self._procs[sid] = proc
            # If cancel already requested, kill immediately.
            ev = self._cancel.get(sid)
            should_kill = bool(ev and ev.is_set())
        if should_kill:
            kill_process_group(proc)

    def unbind_process(
        self, slice_id: str, proc: subprocess.Popen | None = None
    ) -> None:
        sid = slice_id.upper()
        with self._lock:
            cur = self._procs.get(sid)
            if proc is None or cur is proc:
                self._procs.pop(sid, None)

    def clear_slice(self, slice_id: str) -> None:
        """Drop cancel flag + process entry (e.g. after pipeline finishes)."""
        sid = slice_id.upper()
        with self._lock:
            self._cancel.pop(sid, None)
            self._procs.pop(sid, None)


def wait_or_cancel(
    proc: subprocess.Popen,
    *,
    cancel_check: Callable[[], bool] | None = None,
    timeout: float | None = None,
    poll_s: float = 0.2,
) -> tuple[int, str]:
    """Wait for *proc*, honouring cancel_check and optional timeout.

    Returns ``(exit_code, note)``. Cancel → exit 130, note ``cancelled``.
    Timeout → kill, exit 124.
    """
    t0 = time.monotonic()
    while True:
        if cancel_check is not None and cancel_check():
            kill_process_group(proc)
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            return 130, "cancelled"
        remaining: float | None = None
        if timeout is not None:
            elapsed = time.monotonic() - t0
            if elapsed >= timeout:
                kill_process_group(proc)
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
                return 124, f"timed out after {timeout}s"
            remaining = min(poll_s, max(0.05, timeout - elapsed))
        try:
            code = proc.wait(timeout=remaining if remaining is not None else poll_s)
            return code, f"exited {code}"
        except subprocess.TimeoutExpired:
            continue
