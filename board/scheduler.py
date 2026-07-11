"""Target-mutex scheduler: one live pipeline per mutex key; deps unlock dependents."""

from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from board.backends.base import AgentBackend
from board.cancel import RunControl
from board.events import EventWriter
from board.ledger import Ledger
from board.models import BoardPlan, mutex_key
from board.pipeline import PipelineResult, SlicePipeline, SliceRuntime


@dataclass
class RunResult:
    shipped: list[str] = field(default_factory=list)
    capped: list[str] = field(default_factory=list)
    stuck: list[dict[str, str]] = field(default_factory=list)
    blocked_at_start: list[str] = field(default_factory=list)
    results: dict[str, PipelineResult] = field(default_factory=dict)

    def exit_code(self) -> int:
        if self.capped or self.stuck:
            return 1
        return 0

    def to_summary(self) -> dict[str, Any]:
        return {
            "shipped": list(self.shipped),
            "capped": list(self.capped),
            "stuck": list(self.stuck),
            "blocked_at_start": list(self.blocked_at_start),
            "results": {
                k: {
                    "outcome": v.outcome,
                    "final_status": v.final_status,
                    "tries": v.tries,
                    "reason": v.reason,
                    "build_count": v.build_count,
                    "verify_count": v.verify_count,
                }
                for k, v in self.results.items()
            },
        }


class Scheduler:
    def __init__(
        self,
        *,
        ledger: Ledger,
        backend: AgentBackend,
        events: EventWriter,
        run_dir: Path,
        review: bool,
        max_tries: int = 2,
        max_workers: int | None = None,
        pause_event: threading.Event | None = None,
        control: RunControl | None = None,
        skip_check: bool = True,
    ):
        self.ledger = ledger
        self.backend = backend
        self.events = events
        self.run_dir = Path(run_dir)
        self.review = review
        self.max_tries = max_tries
        self.max_workers = max_workers or 8
        self._lock = threading.Lock()
        # When set, do not start *new* pipelines (in-flight continue). Optional;
        # interactive pause lives in Ratatui if wired later.
        self.pause_event = pause_event
        self.control = control
        self.skip_check = skip_check

    def run(self, plan: BoardPlan) -> RunResult:
        out = RunResult(
            blocked_at_start=[r.id for r in plan.blocked_at_start],
        )
        done = set(plan.done_ids)
        # Live roster: actionable + waiting_deps (not blocked-at-start, not draft)
        roster: dict[str, SliceRuntime] = {}
        for row in plan.actionable + plan.waiting_deps:
            roster[row.id] = SliceRuntime(
                id=row.id,
                title=row.title,
                depends_on=list(row.depends_on or row.unmet_deps),
                mutex_target=row.mutex_target or mutex_key(row.target),
                status=row.status,
            )

        if not roster:
            self.events.emit("run.finished", **out.to_summary(), exit_code=0)
            return out

        busy: set[str] = set()
        running: dict[str, Any] = {}  # id -> Future
        capped: set[str] = set()
        stuck: set[str] = set()

        def is_live(sid: str) -> bool:
            return (
                sid in roster
                and sid not in done
                and sid not in capped
                and sid not in stuck
            )

        def deps_done(rt: SliceRuntime) -> bool:
            return all(d in done for d in rt.depends_on)

        def dep_dead(rt: SliceRuntime) -> bool:
            for d in rt.depends_on:
                if d in capped or d in stuck:
                    return True
                if d not in done and d not in roster and d not in plan.done_ids:
                    # dep not in this run and not already done
                    return True
            return False

        def drive_one(rt: SliceRuntime) -> PipelineResult:
            pipe = SlicePipeline(
                ledger=self.ledger,
                backend=self.backend,
                events=self.events,
                run_dir=self.run_dir,
                review=self.review,
                max_tries=self.max_tries,
                skip_check=self.skip_check,
                control=self.control,
            )
            return pipe.drive(rt)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while True:
                # Start every runnable free-target slice (unless pause_event is set)
                paused = bool(self.pause_event is not None and self.pause_event.is_set())
                with self._lock:
                    seen_targets: set[str] = set()
                    for sid, rt in roster.items():
                        if not is_live(sid) or sid in running:
                            continue
                        # Cancel before start (TUI `c` on a waiting slice)
                        if self.control and self.control.is_cancelled(sid):
                            stuck.add(sid)
                            out.stuck.append({"id": sid, "reason": "cancelled"})
                            out.results[sid] = PipelineResult(
                                slice_id=sid,
                                outcome="stuck",
                                final_status=rt.status,
                                reason="cancelled",
                            )
                            self.events.emit(
                                "slice.finished",
                                id=sid,
                                outcome="stuck",
                                reason="cancelled",
                            )
                            continue
                        if dep_dead(rt):
                            stuck.add(sid)
                            out.stuck.append(
                                {"id": sid, "reason": "a dependency cannot ship in this run"}
                            )
                            self.events.emit(
                                "slice.finished",
                                id=sid,
                                outcome="stuck",
                                reason="dep dead",
                            )
                            continue
                        if not deps_done(rt):
                            self.events.emit(
                                "slice.waiting",
                                id=sid,
                                reason="deps",
                                detail=rt.depends_on,
                            )
                            continue
                        t = rt.mutex_target
                        if t in busy or t in seen_targets:
                            self.events.emit(
                                "slice.waiting",
                                id=sid,
                                reason="mutex",
                                detail=t,
                            )
                            continue
                        if paused:
                            # Deps met + target free but operator paused new starts
                            self.events.emit(
                                "slice.waiting",
                                id=sid,
                                reason="paused",
                                detail="operator pause",
                            )
                            continue
                        busy.add(t)
                        seen_targets.add(t)
                        fut = pool.submit(drive_one, rt)
                        running[sid] = fut

                if not running:
                    if paused:
                        # Idle while paused: wait for operator to resume
                        time.sleep(0.2)
                        continue
                    break

                done_futs, _ = wait(
                    list(running.values()),
                    return_when=FIRST_COMPLETED,
                    timeout=0.5 if paused else None,
                )
                for sid, fut in list(running.items()):
                    if fut not in done_futs:
                        continue
                    del running[sid]
                    rt = roster[sid]
                    with self._lock:
                        busy.discard(rt.mutex_target)
                    try:
                        result = fut.result()
                    except Exception as e:
                        result = PipelineResult(
                            slice_id=sid,
                            outcome="stuck",
                            final_status=rt.status,
                            reason=f"pipeline exception: {e}",
                        )
                    out.results[sid] = result
                    if result.outcome == "shipped":
                        done.add(sid)
                        out.shipped.append(sid)
                    elif result.outcome == "capped":
                        capped.add(sid)
                        out.capped.append(sid)
                    else:
                        stuck.add(sid)
                        out.stuck.append({"id": sid, "reason": result.reason or result.outcome})

        # Any live leftovers (shouldn't happen unless never scheduled)
        for sid, rt in roster.items():
            if sid not in done and sid not in capped and sid not in stuck:
                out.stuck.append({"id": sid, "reason": f"left unscheduled at {rt.status}"})

        self.events.emit("run.finished", **out.to_summary(), exit_code=out.exit_code())
        return out
