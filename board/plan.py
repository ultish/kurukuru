"""Build a BoardPlan from kuru next --all --json."""

from __future__ import annotations

from typing import Any

from board.models import BoardPlan, SlicePlanRow, mutex_key


def build_plan(
    next_all: dict[str, Any],
    *,
    scope: list[str] | None = None,
    max_tries: int = 2,
) -> BoardPlan:
    review = bool(next_all.get("review", False))
    scope_u = [s.upper() for s in scope] if scope else None
    scope_set = set(scope_u) if scope_u else None

    actionable: list[SlicePlanRow] = []
    for a in next_all.get("actionable") or []:
        sid = a["id"].upper()
        if scope_set is not None and sid not in scope_set:
            continue
        raw_t = a.get("target")
        actionable.append(
            SlicePlanRow(
                id=sid,
                status=a.get("status") or "",
                title=a.get("title") or "",
                next_action=a.get("next_action"),
                depends_on=[d.upper() for d in (a.get("depends_on") or [])],
                target=raw_t,
                mutex_target=mutex_key(raw_t),
                epic=a.get("epic"),
            )
        )

    waiting_deps: list[SlicePlanRow] = []
    for w in next_all.get("waiting") or []:
        sid = w["id"].upper()
        if scope_set is not None and sid not in scope_set:
            continue
        unmet = [u.upper() for u in (w.get("unmet") or [])]
        dep_full = [d.upper() for d in (w.get("depends_on") or [])] or unmet
        waiting_deps.append(
            SlicePlanRow(
                id=sid,
                status=w.get("status") or "ready",
                title=w.get("title") or "",
                next_action=None,
                depends_on=dep_full,
                target=w.get("target"),
                mutex_target=mutex_key(w.get("target")),
                waiting_reason="deps",
                unmet_deps=unmet,
            )
        )

    blocked_at_start: list[SlicePlanRow] = []
    for bid in next_all.get("blocked") or []:
        # Stable engine shape: list of id strings
        sid = (bid if isinstance(bid, str) else bid.get("id", "")).upper()
        if not sid:
            continue
        if scope_set is not None and sid not in scope_set:
            continue
        blocked_at_start.append(
            SlicePlanRow(
                id=sid,
                status="blocked",
                title=bid.get("title", "") if isinstance(bid, dict) else "",
                next_action=None,
                waiting_reason="blocked_at_start",
                mutex_target=mutex_key(bid.get("target") if isinstance(bid, dict) else None),
            )
        )

    draft: list[SlicePlanRow] = []
    for d in next_all.get("draft") or []:
        sid = d["id"].upper()
        if scope_set is not None and sid not in scope_set:
            continue
        draft.append(
            SlicePlanRow(
                id=sid,
                status="draft",
                title=d.get("title") or "",
                next_action="slice",
                waiting_reason="draft",
                mutex_target=mutex_key(None),
            )
        )

    done_ids = [str(x).upper() for x in (next_all.get("done") or [])]
    if scope_set is not None:
        done_ids = [d for d in done_ids if d in scope_set]

    return BoardPlan(
        review=review,
        actionable=actionable,
        waiting_deps=waiting_deps,
        blocked_at_start=blocked_at_start,
        draft=draft,
        done_ids=done_ids,
        scope=scope_u,
        max_tries=max_tries,
    )


def format_plan_text(plan: BoardPlan, *, repo: str = ".") -> str:
    """Human-readable plan for the terminal (Grok-sparse)."""
    lines: list[str] = []
    scope = f"scope={','.join(plan.scope)}" if plan.scope else "whole board"
    lines.append(
        f"board plan  ·  {repo}  ·  review {'on' if plan.review else 'off'}  ·  "
        f"max_tries={plan.max_tries}  ·  {scope}"
    )
    lines.append("─" * 72)

    lanes = plan.by_mutex_target()
    if not lanes and not plan.blocked_at_start and not plan.draft:
        lines.append("(no actionable or waiting slices)")
    else:
        for tname in sorted(lanes.keys()):
            rows = lanes[tname]
            act = [r for r in rows if r.waiting_reason is None]
            wait = [r for r in rows if r.waiting_reason == "deps"]
            if len(act) > 1:
                concurrency = "SERIAL (same mutex — one pipeline at a time)"
            elif len(act) == 1 and wait:
                concurrency = "one ready; others wait on deps"
            elif len(act) == 1:
                concurrency = "one slice on this target"
            else:
                concurrency = "nothing ready (deps)"
            lines.append(f"target:{tname}  ·  {concurrency}")
            for r in act:
                deps = f"  deps={','.join(r.depends_on)}" if r.depends_on else ""
                lines.append(
                    f"  ● {r.id}  [{r.status}] -> {r.next_action}  {r.title}{deps}"
                )
            for r in wait:
                lines.append(
                    f"  · {r.id}  waiting (deps: {','.join(r.unmet_deps) or '?'})  {r.title}"
                )
            lines.append("")

    # Lanes that only appear as multi-target parallel note
    ready_targets = {r.mutex_target for r in plan.actionable}
    if len(ready_targets) > 1:
        lines.append(
            f"parallelism: {len(ready_targets)} mutex targets can run at once "
            f"({', '.join(sorted(ready_targets))})"
        )
    elif len(ready_targets) == 1:
        only = next(iter(ready_targets))
        n = sum(1 for r in plan.actionable if r.mutex_target == only)
        if n > 1:
            lines.append(
                f"parallelism: none — {n} slices share target:{only} (serialized)"
            )
        else:
            lines.append("parallelism: single ready pipeline")

    if plan.blocked_at_start:
        lines.append(
            "blocked at start (left for human): "
            + ", ".join(r.id for r in plan.blocked_at_start)
        )
    if plan.draft:
        lines.append(
            "draft (need /kuru:slice): " + ", ".join(r.id for r in plan.draft)
        )
    if plan.done_ids:
        lines.append(f"already done: {len(plan.done_ids)} slice(s)")

    lines.append("─" * 72)
    lines.append(
        f"summary: {len(plan.actionable)} actionable  ·  "
        f"{len(plan.waiting_deps)} waiting on deps  ·  "
        f"{len(plan.blocked_at_start)} blocked  ·  "
        f"{len(plan.draft)} draft"
    )
    return "\n".join(lines)
