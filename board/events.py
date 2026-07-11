"""NDJSON event stream + per-run directory layout."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


def new_run_id() -> str:
    # Short, sortable-ish: r_ + first 8 of uuid4 hex
    return f"r_{uuid.uuid4().hex[:10]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class EventWriter:
    """Append-only NDJSON writer for a single run."""

    def __init__(self, run_dir: Path, run_id: str, listeners: list | None = None):
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "events.ndjson"
        self._fh: TextIO = self.path.open("a", encoding="utf-8")
        self._listeners = list(listeners or [])
        self._lock = threading.Lock()

    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event: dict[str, Any] = {
            "ts": utc_now_iso(),
            "run_id": self.run_id,
            "type": event_type,
            **payload,
        }
        with self._lock:
            self._fh.write(json.dumps(event, sort_keys=False) + "\n")
            self._fh.flush()
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event)
            except Exception:
                pass
        return event

    def write_json(self, name: str, data: Any) -> Path:
        path = self.run_dir / name
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path

    def close(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> EventWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def default_run_dir(repo: Path, run_id: str) -> Path:
    """In-tree machine-local run dir (must be gitignored via .kuru/runs/)."""
    return Path(repo).resolve() / ".kuru" / "runs" / run_id
