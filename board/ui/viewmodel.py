"""Pure event → hierarchical board state.

Overview and drill-in are projections of the same BoardState.
Never invent ledger status — only use values from events.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

# Pipeline stage order for glyph rows (check optional in Phase 1).
STAGE_ORDER = ("check", "repair", "build", "verify", "review", "ship")

# Stages shown on the compact pipeline bar by default.
PIPELINE_BAR = ("check", "build", "verify", "review", "ship")


@dataclass
class AgentState:
    role: str = ""
    backend: str = ""
    pid: int | None = None
    stage: str = ""
    alive: bool = False
    started_ts: str | None = None


@dataclass
class StageState:
    name: str
    # pending | running | done | failed
    status: str = "pending"
    try_n: int = 0
    ledger_status: str | None = None
    elapsed_ms: int | None = None
    note: str = ""
    started_ts: str | None = None
    finished_ts: str | None = None
    agent: AgentState | None = None


@dataclass
class SliceState:
    id: str
    title: str = ""
    mutex_target: str = "default"
    raw_target: str | None = None
    depends_on: list[str] = field(default_factory=list)
    ledger_status: str = ""
    # None | "deps" | "mutex"
    wait_reason: str | None = None
    wait_detail: Any = None
    # None | "shipped" | "capped" | "stuck" | "blocked"
    outcome: str | None = None
    outcome_reason: str = ""
    tries: int = 0
    max_tries: int = 2
    stages: dict[str, StageState] = field(default_factory=dict)
    started: bool = False
    finished: bool = False
    started_ts: str | None = None
    finished_ts: str | None = None
    # UI: expand stage list under slice in overview
    expanded: bool = False

    def ensure_stage(self, name: str) -> StageState:
        if name not in self.stages:
            self.stages[name] = StageState(name=name)
        return self.stages[name]

    def active_stage(self) -> StageState | None:
        for name in reversed(STAGE_ORDER):
            st = self.stages.get(name)
            if st and st.status == "running":
                return st
        return None

    def last_stage(self) -> StageState | None:
        for name in reversed(STAGE_ORDER):
            st = self.stages.get(name)
            if st and st.status != "pending":
                return st
        return None

    def live_agent(self) -> AgentState | None:
        for st in self.stages.values():
            if st.agent and st.agent.alive:
                return st.agent
        # fall back to last agent on last stage
        last = self.last_stage()
        if last and last.agent:
            return last.agent
        return None


@dataclass
class TargetState:
    key: str
    expanded: bool = True
    slice_ids: list[str] = field(default_factory=list)


@dataclass
class BoardState:
    run_id: str = ""
    review: bool | None = None
    backend: str = ""
    max_tries: int = 2
    scope: list[str] | None = None
    started_ts: str | None = None
    finished: bool = False
    exit_code: int | None = None
    shipped: list[str] = field(default_factory=list)
    capped: list[str] = field(default_factory=list)
    stuck: list[Any] = field(default_factory=list)
    blocked_at_start: list[str] = field(default_factory=list)
    # insertion-ordered target keys
    target_order: list[str] = field(default_factory=list)
    targets: dict[str, TargetState] = field(default_factory=dict)
    slices: dict[str, SliceState] = field(default_factory=dict)
    last_event_type: str = ""
    last_detail: str = ""
    # commit status
    commit_message: str = ""
    commit_ok: bool | None = None

    def ensure_target(self, key: str) -> TargetState:
        if key not in self.targets:
            self.targets[key] = TargetState(key=key)
            self.target_order.append(key)
        return self.targets[key]

    def counts(self) -> dict[str, int]:
        running = waiting = done = capped = stuck = 0
        for sl in self.slices.values():
            if sl.finished:
                if sl.outcome == "shipped":
                    done += 1
                elif sl.outcome == "capped":
                    capped += 1
                else:
                    stuck += 1
            elif sl.started and sl.wait_reason is None:
                running += 1
            elif sl.wait_reason or (not sl.started and not sl.finished):
                waiting += 1
        return {
            "running": running,
            "waiting": waiting,
            "shipped": done,
            "capped": capped,
            "stuck": stuck,
            "total": len(self.slices),
        }

    def target_busy_slice(self, key: str) -> str | None:
        t = self.targets.get(key)
        if not t:
            return None
        for sid in t.slice_ids:
            sl = self.slices.get(sid)
            if sl and sl.started and not sl.finished and sl.wait_reason is None:
                return sid
        return None


def empty_state() -> BoardState:
    return BoardState()


def apply_event(state: BoardState, event: dict[str, Any]) -> BoardState:
    """Apply one NDJSON event. Mutates and returns *state* (pure of I/O)."""
    t = event.get("type") or ""
    state.last_event_type = t
    if event.get("run_id"):
        state.run_id = str(event["run_id"])

    if t == "run.planned":
        _on_planned(state, event)
    elif t == "run.started":
        state.started_ts = event.get("ts") or state.started_ts
        if event.get("backend"):
            state.backend = str(event["backend"])
        if "review" in event:
            state.review = bool(event["review"])
        state.last_detail = f"run started ({state.backend or '?'})"
    elif t == "run.finished":
        state.finished = True
        state.exit_code = event.get("exit_code")
        state.shipped = list(event.get("shipped") or state.shipped)
        state.capped = list(event.get("capped") or state.capped)
        state.stuck = list(event.get("stuck") or state.stuck)
        state.blocked_at_start = list(
            event.get("blocked_at_start") or state.blocked_at_start
        )
        c = state.counts()
        state.last_detail = (
            f"run finished  shipped={c['shipped']} capped={c['capped']} "
            f"stuck={c['stuck']}"
        )
    elif t == "slice.started":
        sl = _slice(state, str(event.get("id") or ""))
        if sl:
            sl.started = True
            sl.started_ts = event.get("ts")
            sl.wait_reason = None
            sl.wait_detail = None
            if event.get("target"):
                _rehome_slice(state, sl, str(event["target"]))
            state.last_detail = f"{sl.id} started (target={sl.mutex_target})"
    elif t == "slice.waiting":
        sl = _slice(state, str(event.get("id") or ""))
        if sl:
            sl.wait_reason = str(event.get("reason") or "wait")
            sl.wait_detail = event.get("detail")
            state.last_detail = (
                f"{sl.id} waiting ({sl.wait_reason}: {sl.wait_detail})"
            )
    elif t == "slice.finished":
        sl = _slice(state, str(event.get("id") or ""))
        if sl:
            sl.finished = True
            sl.finished_ts = event.get("ts")
            sl.outcome = str(event.get("outcome") or "stuck")
            sl.outcome_reason = str(event.get("reason") or "")
            if event.get("status"):
                sl.ledger_status = str(event["status"])
            if event.get("tries") is not None:
                sl.tries = int(event["tries"])
            sl.wait_reason = None
            # mark any running stage done-ish
            for st in sl.stages.values():
                if st.status == "running":
                    st.status = "done" if sl.outcome == "shipped" else "failed"
                if st.agent:
                    st.agent.alive = False
            state.last_detail = (
                f"{sl.id} {sl.outcome}"
                + (f"  {sl.outcome_reason}" if sl.outcome_reason else "")
            )
    elif t == "stage.started":
        sl = _slice(state, str(event.get("id") or ""))
        stage = str(event.get("stage") or "")
        if sl and stage:
            st = sl.ensure_stage(stage)
            st.status = "running"
            st.started_ts = event.get("ts")
            st.try_n = int(event.get("try") or sl.tries or 0)
            if event.get("try") is not None:
                sl.tries = max(sl.tries, int(event["try"]))
            sl.wait_reason = None
            state.last_detail = f"{sl.id} {stage} …  try={st.try_n}"
    elif t == "stage.finished":
        sl = _slice(state, str(event.get("id") or ""))
        stage = str(event.get("stage") or "")
        if sl and stage:
            st = sl.ensure_stage(stage)
            ledger = event.get("ledger_status")
            if ledger is not None:
                st.ledger_status = str(ledger)
                # Never invent — only set slice ledger from event field
                sl.ledger_status = str(ledger)
            st.elapsed_ms = event.get("elapsed_ms")
            st.note = str(event.get("note") or "")
            st.finished_ts = event.get("ts")
            if event.get("try") is not None:
                st.try_n = int(event["try"])
            # failed try heuristics from note / non-zero exit (not ledger invent)
            note_l = st.note.lower()
            failed = bool(
                event.get("exit_code") not in (None, 0)
                or "reject" in note_l
                or "blocked" in note_l
                or "no_verdict" in note_l
                or "flagged" in note_l
            )
            st.status = "failed" if failed else "done"
            if st.agent:
                st.agent.alive = False
            state.last_detail = (
                f"{sl.id} {stage} → {st.ledger_status or '?'}  "
                f"({st.elapsed_ms or 0}ms) {st.note}"
            ).rstrip()
    elif t == "backend.spawn":
        sl = _slice(state, str(event.get("id") or ""))
        stage = str(event.get("stage") or "")
        if sl and stage:
            st = sl.ensure_stage(stage)
            if st.status == "pending":
                st.status = "running"
            st.agent = AgentState(
                role=str(event.get("role") or stage),
                backend=str(event.get("backend") or state.backend or ""),
                pid=event.get("pid"),
                stage=stage,
                alive=True,
                started_ts=event.get("ts"),
            )
            state.last_detail = (
                f"{sl.id} agent {st.agent.role} pid={st.agent.pid} "
                f"{st.agent.backend}"
            )
    elif t == "backend.exited":
        sl = _slice(state, str(event.get("id") or ""))
        stage = str(event.get("stage") or "")
        if sl and stage:
            st = sl.ensure_stage(stage)
            if st.agent:
                st.agent.alive = False
                if event.get("pid") is not None:
                    st.agent.pid = event.get("pid")
            state.last_detail = (
                f"{sl.id} {stage} agent exited "
                f"code={event.get('exit_code')}"
            )
    elif t == "commit.started":
        state.commit_message = str(event.get("message") or "")
        state.last_detail = f"commit: {state.commit_message}"
    elif t == "commit.finished":
        state.commit_ok = bool(event.get("ok"))
        state.last_detail = (
            f"commit: {'ok' if state.commit_ok else 'skip/fail'} "
            f"{event.get('detail') or ''}"
        ).rstrip()

    return state


def apply_events(state: BoardState, events: Iterable[dict[str, Any]]) -> BoardState:
    for ev in events:
        apply_event(state, ev)
    return state


def clone_state(state: BoardState) -> BoardState:
    return deepcopy(state)


# ── event helpers ──────────────────────────────────────────────────────────


def _on_planned(state: BoardState, event: dict[str, Any]) -> None:
    if "review" in event:
        state.review = bool(event["review"])
    if event.get("max_tries") is not None:
        state.max_tries = int(event["max_tries"])
    state.scope = event.get("scope")
    state.blocked_at_start = [
        (r.get("id") if isinstance(r, dict) else r)
        for r in (event.get("blocked_at_start") or [])
    ]

    bags = (
        ("actionable", None),
        ("waiting_deps", "deps"),
        ("blocked_at_start", "blocked_at_start"),
        ("draft", "draft"),
    )
    for bag_name, default_wait in bags:
        for raw in event.get(bag_name) or []:
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("id") or "")
            if not sid:
                continue
            mt = str(raw.get("mutex_target") or raw.get("target") or "default")
            if mt in ("", "None", "null"):
                mt = "default"
            sl = SliceState(
                id=sid,
                title=str(raw.get("title") or ""),
                mutex_target=mt,
                raw_target=raw.get("target"),
                depends_on=list(raw.get("depends_on") or raw.get("unmet_deps") or []),
                ledger_status=str(raw.get("status") or ""),
                max_tries=state.max_tries,
            )
            if default_wait == "deps" or raw.get("waiting_reason") == "deps":
                sl.wait_reason = "deps"
                sl.wait_detail = list(raw.get("unmet_deps") or raw.get("depends_on") or [])
            elif default_wait == "blocked_at_start":
                sl.wait_reason = "blocked_at_start"
                sl.outcome = "blocked"
            elif default_wait == "draft":
                sl.wait_reason = "draft"
            state.slices[sid] = sl
            tgt = state.ensure_target(mt)
            if sid not in tgt.slice_ids:
                tgt.slice_ids.append(sid)

    n_act = len(event.get("actionable") or [])
    n_wait = len(event.get("waiting_deps") or [])
    state.last_detail = (
        f"planned: {n_act} actionable, {n_wait} waiting on deps, "
        f"review={'on' if state.review else 'off'}"
    )


def _slice(state: BoardState, sid: str) -> SliceState | None:
    if not sid:
        return None
    sid = sid.upper() if sid.upper().startswith("SL-") else sid
    if sid in state.slices:
        return state.slices[sid]
    # create placeholder if events arrive before plan (defensive)
    sl = SliceState(id=sid, max_tries=state.max_tries)
    state.slices[sid] = sl
    tgt = state.ensure_target("default")
    if sid not in tgt.slice_ids:
        tgt.slice_ids.append(sid)
    return sl


def _rehome_slice(state: BoardState, sl: SliceState, new_target: str) -> None:
    new_target = new_target or "default"
    if sl.mutex_target == new_target:
        return
    old = state.targets.get(sl.mutex_target)
    if old and sl.id in old.slice_ids:
        old.slice_ids.remove(sl.id)
    sl.mutex_target = new_target
    tgt = state.ensure_target(new_target)
    if sl.id not in tgt.slice_ids:
        tgt.slice_ids.append(sl.id)


# ── projections ────────────────────────────────────────────────────────────


@dataclass
class TreeRow:
    """One selectable row in the overview tree."""

    kind: str  # target | slice | stage | agent
    key: str  # stable id for selection
    depth: int
    label: str
    # back-refs
    target: str | None = None
    slice_id: str | None = None
    stage: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def stage_glyph(st: StageState | None) -> str:
    if st is None or st.status == "pending":
        return "·"
    if st.status == "running":
        return "●"
    if st.status == "failed":
        return "✗"
    return "✓"


def pipeline_bar(sl: SliceState, stages: Iterable[str] = PIPELINE_BAR) -> str:
    parts = []
    for name in stages:
        # skip check if never started (common Phase 1)
        st = sl.stages.get(name)
        if name == "check" and (st is None or st.status == "pending"):
            continue
        if name == "repair" and (st is None or st.status == "pending"):
            continue
        parts.append(f"{name} {stage_glyph(st)}")
    return "  ".join(parts) if parts else "·"


def slice_status_glyph(sl: SliceState) -> str:
    if sl.finished:
        if sl.outcome == "shipped":
            return "■"
        if sl.outcome == "capped":
            return "▴"
        return "✗"
    if sl.started and sl.wait_reason is None:
        return "●"
    if sl.wait_reason:
        return "·"
    return "·"


def format_wait(sl: SliceState) -> str:
    if sl.finished:
        if sl.outcome == "shipped":
            return "shipped"
        if sl.outcome == "capped":
            return f"capped{(': ' + sl.outcome_reason) if sl.outcome_reason else ''}"
        return f"{sl.outcome or 'stuck'}{(': ' + sl.outcome_reason) if sl.outcome_reason else ''}"
    if sl.wait_reason == "mutex":
        detail = sl.wait_detail
        return f"waiting (mutex: {detail})"
    if sl.wait_reason == "deps":
        deps = sl.wait_detail if sl.wait_detail is not None else sl.depends_on
        if isinstance(deps, list):
            deps = ",".join(str(d) for d in deps)
        return f"waiting (deps: {deps})"
    if sl.wait_reason:
        return f"waiting ({sl.wait_reason})"
    if sl.started:
        return f"try {sl.tries}/{sl.max_tries}"
    return "queued"


def overview_rows(
    state: BoardState,
    *,
    waiting_filter: bool = False,
) -> list[TreeRow]:
    """Project overview tree. waiting_filter keeps blockers / non-running only."""
    rows: list[TreeRow] = []
    for tkey in state.target_order:
        tgt = state.targets[tkey]
        slice_ids = list(tgt.slice_ids)
        if waiting_filter:
            slice_ids = [
                sid
                for sid in slice_ids
                if _is_blocker(state.slices.get(sid))
            ]
            if not slice_ids:
                continue

        busy = state.target_busy_slice(tkey)
        lane = f"BUSY · {busy}" if busy else "IDLE"
        chev = "▼" if tgt.expanded else "▶"
        rows.append(
            TreeRow(
                kind="target",
                key=f"t:{tkey}",
                depth=0,
                label=f"{chev} target:{tkey}    {lane}",
                target=tkey,
                meta={"busy": busy, "expanded": tgt.expanded},
            )
        )
        if not tgt.expanded:
            continue

        for sid in slice_ids:
            sl = state.slices.get(sid)
            if not sl:
                continue
            g = slice_status_glyph(sl)
            title = sl.title or sid
            try_s = f"try {sl.tries}/{sl.max_tries}" if sl.tries or sl.started else ""
            wait_s = format_wait(sl)
            label = f"{g} {sid}  {title}"
            if try_s and sl.started and not sl.finished and not sl.wait_reason:
                label += f"     {try_s}"
            else:
                label += f"     {wait_s}"
            rows.append(
                TreeRow(
                    kind="slice",
                    key=f"s:{sid}",
                    depth=1,
                    label=label,
                    target=tkey,
                    slice_id=sid,
                    meta={"outcome": sl.outcome, "wait": sl.wait_reason},
                )
            )
            # pipeline bar under slice
            bar = pipeline_bar(sl)
            rows.append(
                TreeRow(
                    kind="pipeline",
                    key=f"p:{sid}",
                    depth=2,
                    label=bar,
                    target=tkey,
                    slice_id=sid,
                )
            )
            agent = sl.live_agent()
            if agent and (agent.alive or sl.started and not sl.finished):
                pid_s = str(agent.pid) if agent.pid is not None else "—"
                rows.append(
                    TreeRow(
                        kind="agent",
                        key=f"a:{sid}:{agent.stage}",
                        depth=2,
                        label=(
                            f"agent  {agent.role}  pid {pid_s}  "
                            f"{agent.backend or '?'}"
                            + ("  ●" if agent.alive else "")
                        ),
                        target=tkey,
                        slice_id=sid,
                        stage=agent.stage,
                        meta={"pid": agent.pid, "role": agent.role, "alive": agent.alive},
                    )
                )
            if sl.expanded:
                for name in STAGE_ORDER:
                    st = sl.stages.get(name)
                    if st is None or st.status == "pending":
                        # only show started stages when expanded, plus bar stages
                        if name not in ("build", "verify", "review", "ship"):
                            continue
                    g = stage_glyph(st)
                    ls = (st.ledger_status if st else None) or ""
                    el = f"  {st.elapsed_ms}ms" if st and st.elapsed_ms is not None else ""
                    rows.append(
                        TreeRow(
                            kind="stage",
                            key=f"st:{sid}:{name}",
                            depth=2,
                            label=f"{g} {name}{('  ' + ls) if ls else ''}{el}",
                            target=tkey,
                            slice_id=sid,
                            stage=name,
                        )
                    )
    return rows


def _is_blocker(sl: SliceState | None) -> bool:
    if sl is None:
        return False
    if sl.finished and sl.outcome in ("capped", "stuck", "blocked"):
        return True
    if sl.wait_reason in ("deps", "mutex", "blocked_at_start"):
        return True
    if not sl.started and not sl.finished:
        return True
    return False


def row_detail(state: BoardState, row: TreeRow | None, run_dir: str | None = None) -> str:
    """One-line footer detail for the selected row."""
    if row is None:
        return state.last_detail or ""
    if row.kind == "target":
        busy = state.target_busy_slice(row.target or "")
        return f"target {row.target}  " + (
            f"busy with {busy}" if busy else "idle (mutex free)"
        )
    if row.slice_id:
        sl = state.slices.get(row.slice_id)
        if not sl:
            return state.last_detail or ""
        if row.kind == "agent":
            ag = sl.live_agent()
            if ag:
                return (
                    f"{sl.id} agent role={ag.role} pid={ag.pid} "
                    f"backend={ag.backend} stage={ag.stage}"
                )
        if row.kind == "stage" and row.stage:
            st = sl.stages.get(row.stage)
            log = log_path_for(run_dir, sl.id, row.stage) if run_dir else ""
            return (
                f"{sl.id}/{row.stage}  status={st.status if st else '?'}  "
                f"ledger={st.ledger_status if st else '?'}  log={log}"
            )
        wait = format_wait(sl)
        log = ""
        last = sl.active_stage() or sl.last_stage()
        if run_dir and last:
            log = f"  log={log_path_for(run_dir, sl.id, last.name)}"
        return (
            f"{sl.id}  ledger={sl.ledger_status or '—'}  {wait}  "
            f"deps={','.join(sl.depends_on) or 'none'}{log}"
        )
    return state.last_detail or ""


def log_path_for(run_dir: str | None, slice_id: str, stage: str) -> str:
    if not run_dir:
        return ""
    from pathlib import Path

    return str(Path(run_dir) / slice_id / f"{stage}.log")


def selected_log_path(
    state: BoardState, row: TreeRow | None, run_dir: str | None
) -> str | None:
    if not run_dir or not row or not row.slice_id:
        return None
    stage = row.stage
    if not stage:
        sl = state.slices.get(row.slice_id)
        if not sl:
            return None
        last = sl.active_stage() or sl.last_stage()
        if not last:
            # default to build log if nothing yet
            stage = "build"
        else:
            stage = last.name
    return log_path_for(run_dir, row.slice_id, stage)
