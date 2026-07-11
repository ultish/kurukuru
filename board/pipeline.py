"""Per-slice build→verify→(review)→ship pipeline (engine-aligned routing)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from board.backends.base import AgentBackend
from board.events import EventWriter
from board.ledger import KuruError, Ledger
from board.prompts import stage_prompt_for, stage_role

if TYPE_CHECKING:
    from board.cancel import RunControl

NEEDS_BUILD = frozenset({"ready", "in_progress", "rejected"})
DEFAULT_MAX_NO_VERDICT = 2
DEFAULT_MAX_PIPELINE_ITERS = 20


EmitFn = Callable[..., dict]


@dataclass
class PipelineResult:
    slice_id: str
    outcome: str  # shipped | capped | stuck | blocked
    final_status: str
    tries: int = 0
    reason: str = ""
    build_count: int = 0
    verify_count: int = 0


@dataclass
class SliceRuntime:
    id: str
    title: str
    depends_on: list[str]
    mutex_target: str
    status: str
    tries: int = 0
    no_verdict_verifies: int = 0
    pipeline_iters: int = 0
    build_count: int = 0
    verify_count: int = 0
    # When skip_check is False, start unchecked so first clean build runs check.
    checked: bool = False


class SlicePipeline:
    def __init__(
        self,
        *,
        ledger: Ledger,
        backend: AgentBackend,
        events: EventWriter,
        run_dir: Path,
        review: bool,
        max_tries: int = 2,
        max_no_verdict: int = DEFAULT_MAX_NO_VERDICT,
        max_pipeline_iters: int = DEFAULT_MAX_PIPELINE_ITERS,
        skip_check: bool = True,
        control: "RunControl | None" = None,
    ):
        self.ledger = ledger
        self.backend = backend
        self.events = events
        self.run_dir = Path(run_dir)
        self.review = review
        self.max_tries = max_tries
        self.max_no_verdict = max_no_verdict
        self.max_pipeline_iters = max_pipeline_iters
        self.skip_check = skip_check
        self.control = control

    def _cancelled(self, rt: SliceRuntime) -> bool:
        return bool(self.control and self.control.is_cancelled(rt.id))

    def drive(self, rt: SliceRuntime) -> PipelineResult:
        sid = rt.id
        self.events.emit("slice.started", id=sid, target=rt.mutex_target)

        try:
            return self._drive_loop(rt)
        finally:
            if self.control:
                self.control.clear_slice(sid)

    def _drive_loop(self, rt: SliceRuntime) -> PipelineResult:
        sid = rt.id

        while True:
            if self._cancelled(rt):
                return self._finish(rt, "stuck", "cancelled")

            rt.pipeline_iters += 1
            if rt.pipeline_iters > self.max_pipeline_iters:
                return self._finish(rt, "stuck", "pipeline iteration cap")

            try:
                st = self.ledger.show(sid)["status"]
            except KuruError as e:
                return self._finish(rt, "stuck", f"ledger read failed: {e}")
            rt.status = st

            if st == "done":
                return self._finish(rt, "shipped", "already done")

            # Mid-run blocked → reset then rebuild (runner.py)
            if st == "blocked":
                try:
                    self.ledger.run(
                        "set-status",
                        sid,
                        "in_progress",
                        "--by",
                        "builder",
                        "--note",
                        "board: retry after failed build",
                    )
                    st = "in_progress"
                    rt.status = st
                except KuruError as e:
                    return self._finish(rt, "stuck", f"could not reset blocked: {e}")

            if st in NEEDS_BUILD:
                if not self.skip_check and not rt.checked and st in ("ready", "in_progress"):
                    chk = self._stage(rt, "check")
                    if self._cancelled(rt) or chk.note == "cancelled":
                        return self._finish(rt, "stuck", "cancelled")
                    if chk.note == "flagged":
                        # Light repair path: planner rewrite + re-check, capped by max_tries.
                        ok = False
                        for _ in range(self.max_tries):
                            self._stage(rt, "repair")
                            if self._cancelled(rt):
                                return self._finish(rt, "stuck", "cancelled")
                            rechk = self._stage(rt, "check")
                            if self._cancelled(rt) or rechk.note == "cancelled":
                                return self._finish(rt, "stuck", "cancelled")
                            if rechk.note != "flagged":
                                ok = True
                                break
                        if not ok:
                            return self._finish(
                                rt, "stuck", "contract flagged (repair exhausted)"
                            )
                    rt.checked = True

                if rt.tries >= self.max_tries:
                    return self._finish(rt, "capped", f"exhausted {self.max_tries} tries")
                rt.tries += 1
                rt.build_count += 1
                res = self._stage(rt, "build")
                if self._cancelled(rt) or res.note == "cancelled":
                    return self._finish(rt, "stuck", "cancelled")
                continue

            if st in ("built", "verifying"):
                rt.verify_count += 1
                res = self._stage(rt, "verify")
                if self._cancelled(rt) or res.note == "cancelled":
                    return self._finish(rt, "stuck", "cancelled")
                try:
                    after = self.ledger.show(sid)["status"]
                except KuruError as e:
                    return self._finish(rt, "stuck", f"ledger read after verify: {e}")
                rt.status = after
                if after == "verifying":
                    rt.no_verdict_verifies += 1
                    if rt.no_verdict_verifies >= self.max_no_verdict:
                        return self._finish(
                            rt,
                            "stuck",
                            f"no verify verdict after {rt.no_verdict_verifies} attempts "
                            f"(re-verify only — did not rebuild)",
                        )
                    continue  # re-verify, do NOT rebuild
                # rejected → next loop → NEEDS_BUILD; verified → review/ship
                continue

            if st == "verified":
                if self.review:
                    res = self._stage(rt, "review")
                    if self._cancelled(rt) or res.note == "cancelled":
                        return self._finish(rt, "stuck", "cancelled")
                    try:
                        after = self.ledger.show(sid)["status"]
                    except KuruError as e:
                        return self._finish(rt, "stuck", f"ledger read after review: {e}")
                    rt.status = after
                    if after == "verified":
                        return self._finish(rt, "stuck", "review recorded no verdict")
                    # rejected → rebuild; reviewed → ship next iter
                    continue
                res = self._stage(rt, "ship")
                if self._cancelled(rt) or res.note == "cancelled":
                    return self._finish(rt, "stuck", "cancelled")
                try:
                    after = self.ledger.show(sid)["status"]
                except KuruError as e:
                    return self._finish(rt, "stuck", f"ledger read after ship: {e}")
                if after == "done":
                    return self._finish(rt, "shipped", "")
                return self._finish(rt, "stuck", f"ship refused (left at {after})")

            if st == "reviewed":
                res = self._stage(rt, "ship")
                if self._cancelled(rt) or res.note == "cancelled":
                    return self._finish(rt, "stuck", "cancelled")
                try:
                    after = self.ledger.show(sid)["status"]
                except KuruError as e:
                    return self._finish(rt, "stuck", f"ledger read after ship: {e}")
                if after == "done":
                    return self._finish(rt, "shipped", "")
                return self._finish(rt, "stuck", f"ship refused (left at {after})")

            return self._finish(rt, "stuck", f"unexpected status {st}")

    def _stage(self, rt: SliceRuntime, stage: str):
        sid = rt.id
        if self._cancelled(rt):
            from board.backends.base import StageProcessResult

            return StageProcessResult(
                exit_code=130,
                elapsed_ms=0,
                pid=None,
                note="cancelled",
                role=stage_role(stage),
            )

        log_path = self.run_dir / sid / f"{stage}.log"
        # Claude: slash commands. Grok: skill-on-disk + kuru.py (no slash discovery).
        prompt = stage_prompt_for(
            getattr(self.backend, "name", "claude"),
            stage,
            sid,
            plugin_dir=getattr(self.backend, "plugin_dir", None),
            kuru_py=getattr(self.backend, "kuru_py", None),
        )
        t0_try = rt.tries
        self.events.emit("stage.started", id=sid, stage=stage, **{"try": t0_try})
        # Emit spawn *before* run_stage so the board can show a live agent row.
        # Pid is usually unknown until exit for blocking backends (mock/claude);
        # Grok uses Popen and returns pid on backend.exited.
        role = stage_role(stage)
        self.events.emit(
            "backend.spawn",
            id=sid,
            stage=stage,
            backend=self.backend.name,
            pid=None,
            role=role,
        )

        result = self.backend.run_stage(
            stage=stage,
            slice_id=sid,
            prompt=prompt,
            cwd=self.ledger.repo,
            log_path=log_path,
        )
        self.events.emit(
            "backend.exited",
            id=sid,
            stage=stage,
            pid=result.pid,
            exit_code=result.exit_code,
        )
        try:
            ledger_status = self.ledger.show(sid)["status"]
        except KuruError:
            ledger_status = "unknown"
        self.events.emit(
            "stage.finished",
            id=sid,
            stage=stage,
            ledger_status=ledger_status,
            exit_code=result.exit_code,
            elapsed_ms=result.elapsed_ms,
            note=result.note,
            **{"try": t0_try},
        )
        return result

    def _finish(self, rt: SliceRuntime, outcome: str, reason: str) -> PipelineResult:
        try:
            final = self.ledger.show(rt.id)["status"]
        except KuruError:
            final = rt.status
        rt.status = final
        self.events.emit(
            "slice.finished",
            id=rt.id,
            outcome=outcome,
            status=final,
            reason=reason,
            tries=rt.tries,
        )
        return PipelineResult(
            slice_id=rt.id,
            outcome=outcome,
            final_status=final,
            tries=rt.tries,
            reason=reason,
            build_count=rt.build_count,
            verify_count=rt.verify_count,
        )
