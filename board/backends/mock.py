"""Deterministic mock backend — advances the ledger via kuru.py (no model)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from board.backends.base import StageProcessResult
from board.ledger import Ledger, KuruError

# Per-slice scenario keys (optional; fall back to "default" then built-in ok)
#   build: "ok" | "blocked"
#   build_block_times: int  — block first N builds, then ok
#   verify: "ok" | "rejected" | "no_verdict"
#   verify_fail_times: int — reject first N verifies, then ok
#   review: "ok" | "rejected" | "no_verdict"
#   review_fail_times: int
#   ship: "ok" | "refuse"  — refuse leaves status unchanged
#   check: "ok" | "flagged"  — Phase 1 treats flagged as ok-with-note (no repair yet)


ROLE = {
    "check": "critic",
    "repair": "planner",
    "build": "builder",
    "verify": "verifier",
    "review": "reviewer",
    "ship": "ship",
}


class MockBackend:
    name = "mock"

    def __init__(self, ledger: Ledger, scenarios: dict[str, Any] | None = None):
        self.ledger = ledger
        self.scenarios = scenarios or {}
        self._counts: dict[str, dict[str, int]] = {}  # sid -> {build, verify, review}

    def _sc(self, slice_id: str) -> dict[str, Any]:
        slices = self.scenarios.get("slices") or {}
        if slice_id in slices:
            return {**(self.scenarios.get("default") or {}), **slices[slice_id]}
        return dict(self.scenarios.get("default") or {})

    def _count(self, sid: str, key: str) -> int:
        bag = self._counts.setdefault(sid, {})
        bag[key] = bag.get(key, 0) + 1
        return bag[key]

    def run_stage(
        self,
        *,
        stage: str,
        slice_id: str,
        prompt: str,
        cwd: Path,
        log_path: Path,
        timeout: float | None = None,
    ) -> StageProcessResult:
        t0 = time.monotonic()
        sid = slice_id.upper()
        sc = self._sc(sid)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        note = ""
        code = 0
        try:
            if stage == "check":
                note = self._do_check(sid, sc)
            elif stage == "build":
                note = self._do_build(sid, sc)
            elif stage == "verify":
                note = self._do_verify(sid, sc)
            elif stage == "review":
                note = self._do_review(sid, sc)
            elif stage == "ship":
                note = self._do_ship(sid, sc)
            elif stage == "repair":
                # Phase 1: no real repair — mark ready if draft
                note = self._do_repair(sid, sc)
            else:
                note = f"unknown stage {stage}"
                code = 2
        except KuruError as e:
            note = str(e)
            code = e.returncode or 1
        except Exception as e:
            note = f"mock error: {e}"
            code = 1

        elapsed = int((time.monotonic() - t0) * 1000)
        log_path.write_text(
            f"mock {stage} {sid}\nprompt: {prompt[:200]}\nnote: {note}\nexit: {code}\n",
            encoding="utf-8",
        )
        return StageProcessResult(
            exit_code=code,
            elapsed_ms=elapsed,
            pid=None,
            note=note,
            role=ROLE.get(stage, stage),
        )

    def _status(self, sid: str) -> str:
        return self.ledger.show(sid)["status"]

    def _set(self, sid: str, status: str, *, by: str = "human", note: str = "", no_commit: bool = False) -> None:
        args = ["set-status", sid, status, "--by", by, "--note", note or f"mock->{status}"]
        if no_commit and status == "done":
            args.append("--no-commit")
        self.ledger.run(*args)

    def _do_check(self, sid: str, sc: dict[str, Any]) -> str:
        v = sc.get("check", "ok")
        if v == "flagged":
            return "flagged"
        return "ok"

    def _do_repair(self, sid: str, sc: dict[str, Any]) -> str:
        st = self._status(sid)
        if st == "draft":
            self._set(sid, "ready", by="planner", note="mock repair")
        return "repaired"

    def _do_build(self, sid: str, sc: dict[str, Any]) -> str:
        n = self._count(sid, "build")
        block_times = int(sc.get("build_block_times") or 0)
        mode = sc.get("build", "ok")
        if mode == "blocked" or (block_times and n <= block_times):
            st = self._status(sid)
            if st == "ready":
                self._set(sid, "in_progress", by="builder", note="mock start")
            self._set(sid, "blocked", by="builder", note="mock build blocked")
            return f"blocked (build #{n})"

        st = self._status(sid)
        if st == "ready":
            self._set(sid, "in_progress", by="builder", note="mock start")
        elif st == "blocked":
            # pipeline should have reset; if not, try
            self._set(sid, "in_progress", by="builder", note="mock unblock")
        st = self._status(sid)
        if st in ("in_progress", "rejected"):
            if st == "rejected":
                self._set(sid, "in_progress", by="builder", note="mock retry after reject")
            self._set(sid, "built", by="builder", note="mock built")
            return f"built (build #{n})"
        # already built?
        return f"build noop at {st}"

    def _do_verify(self, sid: str, sc: dict[str, Any]) -> str:
        n = self._count(sid, "verify")
        fail_times = int(sc.get("verify_fail_times") or 0)
        mode = sc.get("verify", "ok")
        st = self._status(sid)
        if st == "built":
            self._set(sid, "verifying", by="verifier", note="mock claim verify")
            st = "verifying"
        if st != "verifying":
            return f"verify noop at {st}"

        if mode == "no_verdict":
            # leave verifying — engine-aligned re-verify path
            return f"no_verdict (verify #{n})"

        if mode == "rejected" or (fail_times and n <= fail_times):
            self._set(sid, "rejected", by="verifier", note="mock verify rejected")
            return f"rejected (verify #{n})"

        # ok path: gates then verified
        g = self.ledger.run("gate", sid, check=False)
        if g.returncode != 0:
            # still try verified — may fail; surface gate noise
            pass
        self._set(sid, "verified", by="verifier", note="mock verified")
        return f"verified (verify #{n})"

    def _do_review(self, sid: str, sc: dict[str, Any]) -> str:
        n = self._count(sid, "review")
        fail_times = int(sc.get("review_fail_times") or 0)
        mode = sc.get("review", "ok")
        st = self._status(sid)
        if st != "verified":
            return f"review noop at {st}"

        if mode == "no_verdict":
            return f"no_verdict (review #{n})"

        if mode == "rejected" or (fail_times and n <= fail_times):
            self._set(sid, "rejected", by="reviewer", note="mock review rejected")
            return f"rejected (review #{n})"

        self._set(sid, "reviewed", by="reviewer", note="mock reviewed")
        return f"reviewed (review #{n})"

    def _do_ship(self, sid: str, sc: dict[str, Any]) -> str:
        mode = sc.get("ship", "ok")
        st = self._status(sid)
        if mode == "refuse":
            return f"refuse ship at {st}"
        if st in ("verified", "reviewed"):
            self._set(sid, "done", by="human", note="mock ship", no_commit=True)
            return "shipped"
        return f"ship noop at {st}"


def load_mock_scenarios(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"default": {"build": "ok", "verify": "ok", "review": "ok", "check": "ok"}}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("mock scenario file must be a JSON object")
    return data
