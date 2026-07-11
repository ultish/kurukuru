"""Shared enums and small data types for the board runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_MUTEX_TARGET = "default"


def mutex_key(target: str | None) -> str:
    """Concurrency key for a slice. Null/missing target → one shared bucket."""
    if target is None or target == "":
        return DEFAULT_MUTEX_TARGET
    return str(target)


@dataclass
class SlicePlanRow:
    id: str
    status: str
    title: str
    next_action: str | None
    depends_on: list[str] = field(default_factory=list)
    target: str | None = None  # raw from ledger (may be null)
    mutex_target: str = DEFAULT_MUTEX_TARGET
    epic: str | None = None
    waiting_reason: str | None = None  # "deps" | "blocked_at_start" | "draft" | None
    unmet_deps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title,
            "next_action": self.next_action,
            "depends_on": list(self.depends_on),
            "target": self.target,
            "mutex_target": self.mutex_target,
            "epic": self.epic,
            "waiting_reason": self.waiting_reason,
            "unmet_deps": list(self.unmet_deps),
        }


@dataclass
class BoardPlan:
    """Snapshot used for `board plan` and run.planned events."""

    review: bool
    actionable: list[SlicePlanRow] = field(default_factory=list)
    waiting_deps: list[SlicePlanRow] = field(default_factory=list)
    blocked_at_start: list[SlicePlanRow] = field(default_factory=list)
    draft: list[SlicePlanRow] = field(default_factory=list)
    done_ids: list[str] = field(default_factory=list)
    scope: list[str] | None = None  # None = whole board
    max_tries: int = 2

    def all_rows(self) -> list[SlicePlanRow]:
        return (
            list(self.actionable)
            + list(self.waiting_deps)
            + list(self.blocked_at_start)
            + list(self.draft)
        )

    def by_mutex_target(self) -> dict[str, list[SlicePlanRow]]:
        groups: dict[str, list[SlicePlanRow]] = {}
        for row in self.actionable + self.waiting_deps:
            groups.setdefault(row.mutex_target, []).append(row)
        return groups

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "review": self.review,
            "max_tries": self.max_tries,
            "scope": self.scope,
            "actionable": [r.to_dict() for r in self.actionable],
            "waiting_deps": [r.to_dict() for r in self.waiting_deps],
            "blocked_at_start": [r.to_dict() for r in self.blocked_at_start],
            "draft": [r.to_dict() for r in self.draft],
            "done_ids": list(self.done_ids),
            "mutex_lanes": {
                t: [r.id for r in rows] for t, rows in sorted(self.by_mutex_target().items())
            },
        }
