"""Unit tests for board.ui.viewmodel — no TTY required.

Invoked from scripts/board-selftest.sh:
  python3 -m board.ui.test_viewmodel
"""

from __future__ import annotations

import sys

from board.ui.viewmodel import (
    apply_event,
    apply_events,
    empty_state,
    overview_rows,
    pipeline_bar,
)


def _ev(typ: str, **kw):
    return {"type": typ, "ts": "2026-07-11T00:00:00Z", "run_id": "r_test", **kw}


def _plan_event() -> dict:
    return _ev(
        "run.planned",
        review=True,
        max_tries=2,
        actionable=[
            {
                "id": "SL-0001",
                "title": "API work",
                "status": "ready",
                "target": "api",
                "mutex_target": "api",
                "depends_on": [],
            },
            {
                "id": "SL-0002",
                "title": "Web work",
                "status": "ready",
                "target": "web",
                "mutex_target": "web",
                "depends_on": [],
            },
            {
                "id": "SL-0003",
                "title": "Mutex waiter",
                "status": "ready",
                "target": "api",
                "mutex_target": "api",
                "depends_on": [],
            },
        ],
        waiting_deps=[
            {
                "id": "SL-0004",
                "title": "Dep waiter",
                "status": "ready",
                "target": "worker",
                "mutex_target": "worker",
                "depends_on": ["SL-0001"],
                "unmet_deps": ["SL-0001"],
                "waiting_reason": "deps",
            },
        ],
        blocked_at_start=[],
        draft=[],
        done_ids=[],
        mutex_lanes={"api": ["SL-0001", "SL-0003"], "web": ["SL-0002"], "worker": ["SL-0004"]},
    )


def test_two_targets_busy_mutex_and_dep_waiters() -> None:
    st = empty_state()
    apply_events(
        st,
        [
            _plan_event(),
            _ev("run.started", backend="mock", review=True),
            # two targets busy in parallel
            _ev("slice.started", id="SL-0001", target="api"),
            _ev("slice.started", id="SL-0002", target="web"),
            # mutex waiter on api
            _ev("slice.waiting", id="SL-0003", reason="mutex", detail="api"),
            # dep waiter
            _ev("slice.waiting", id="SL-0004", reason="deps", detail=["SL-0001"]),
            # stages running on both
            _ev("stage.started", id="SL-0001", stage="build", **{"try": 1}),
            _ev("stage.started", id="SL-0002", stage="build", **{"try": 1}),
        ],
    )

    assert set(st.target_order) >= {"api", "web", "worker"}
    assert st.target_busy_slice("api") == "SL-0001"
    assert st.target_busy_slice("web") == "SL-0002"
    assert st.target_busy_slice("worker") is None

    s3 = st.slices["SL-0003"]
    assert s3.wait_reason == "mutex"
    assert s3.wait_detail == "api"

    s4 = st.slices["SL-0004"]
    assert s4.wait_reason == "deps"

    assert st.slices["SL-0001"].stages["build"].status == "running"
    assert st.slices["SL-0002"].stages["build"].status == "running"

    rows = overview_rows(st)
    labels = "\n".join(r.label for r in rows)
    assert "target:api" in labels
    assert "target:web" in labels
    assert "waiting (mutex" in labels
    assert "waiting (deps" in labels

    # waiting filter keeps blockers
    blocked = overview_rows(st, waiting_filter=True)
    ids = {r.slice_id for r in blocked if r.slice_id}
    assert "SL-0003" in ids
    assert "SL-0004" in ids
    # running slices filtered out
    assert "SL-0001" not in ids
    assert "SL-0002" not in ids


def test_agent_row_on_backend_spawn() -> None:
    st = empty_state()
    apply_events(
        st,
        [
            _plan_event(),
            _ev("run.started", backend="mock", review=True),
            _ev("slice.started", id="SL-0001", target="api"),
            _ev("stage.started", id="SL-0001", stage="verify", **{"try": 1}),
            _ev(
                "backend.spawn",
                id="SL-0001",
                stage="verify",
                backend="mock",
                pid=4421,
                role="verifier",
            ),
        ],
    )
    sl = st.slices["SL-0001"]
    stg = sl.stages["verify"]
    assert stg.status == "running"
    assert stg.agent is not None
    assert stg.agent.alive is True
    assert stg.agent.pid == 4421
    assert stg.agent.role == "verifier"
    assert stg.agent.backend == "mock"

    rows = overview_rows(st)
    agent_rows = [r for r in rows if r.kind == "agent"]
    assert agent_rows, "expected agent row in overview"
    assert "verifier" in agent_rows[0].label
    assert "4421" in agent_rows[0].label

    # exit clears live flag
    apply_event(
        st,
        _ev("backend.exited", id="SL-0001", stage="verify", pid=4421, exit_code=0),
    )
    assert st.slices["SL-0001"].stages["verify"].agent.alive is False


def test_ledger_status_from_stage_finished_only() -> None:
    st = empty_state()
    apply_events(
        st,
        [
            _plan_event(),
            _ev("slice.started", id="SL-0001", target="api"),
            _ev("stage.started", id="SL-0001", stage="build", **{"try": 1}),
            _ev(
                "stage.finished",
                id="SL-0001",
                stage="build",
                ledger_status="built",
                exit_code=0,
                elapsed_ms=12,
                note="ok",
                **{"try": 1},
            ),
        ],
    )
    assert st.slices["SL-0001"].ledger_status == "built"
    assert st.slices["SL-0001"].stages["build"].status == "done"
    assert "build ✓" in pipeline_bar(st.slices["SL-0001"])


def test_terminal_outcomes() -> None:
    st = empty_state()
    apply_events(
        st,
        [
            _plan_event(),
            _ev("slice.started", id="SL-0001", target="api"),
            _ev(
                "slice.finished",
                id="SL-0001",
                outcome="shipped",
                status="done",
                reason="",
                tries=1,
            ),
            _ev(
                "slice.finished",
                id="SL-0002",
                outcome="capped",
                status="rejected",
                reason="exhausted 2 tries",
                tries=2,
            ),
            _ev(
                "slice.finished",
                id="SL-0003",
                outcome="stuck",
                status="verifying",
                reason="no verify verdict",
                tries=1,
            ),
            _ev(
                "run.finished",
                shipped=["SL-0001"],
                capped=["SL-0002"],
                stuck=[{"id": "SL-0003", "reason": "no verify verdict"}],
                exit_code=1,
            ),
        ],
    )
    assert st.slices["SL-0001"].outcome == "shipped"
    assert st.slices["SL-0002"].outcome == "capped"
    assert st.slices["SL-0003"].outcome == "stuck"
    assert st.finished is True
    c = st.counts()
    assert c["shipped"] == 1
    assert c["capped"] == 1
    assert c["stuck"] >= 1


def test_failed_try_glyph() -> None:
    st = empty_state()
    apply_events(
        st,
        [
            _plan_event(),
            _ev("slice.started", id="SL-0001", target="api"),
            _ev("stage.started", id="SL-0001", stage="verify", **{"try": 1}),
            _ev(
                "stage.finished",
                id="SL-0001",
                stage="verify",
                ledger_status="rejected",
                exit_code=0,
                elapsed_ms=5,
                note="rejected (verify #1)",
                **{"try": 1},
            ),
        ],
    )
    assert st.slices["SL-0001"].stages["verify"].status == "failed"
    assert st.slices["SL-0001"].ledger_status == "rejected"


def main() -> int:
    tests = [
        test_two_targets_busy_mutex_and_dep_waiters,
        test_agent_row_on_backend_spawn,
        test_ledger_status_from_stage_finished_only,
        test_terminal_outcomes,
        test_failed_try_glyph,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok: {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {fn.__name__}: {e}")
    if failed:
        print(f"viewmodel tests: {len(tests) - failed} passed, {failed} failed")
        return 1
    print(f"viewmodel tests: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
