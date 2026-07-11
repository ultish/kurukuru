"""Backend protocol — one process (or simulation) per stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class StageProcessResult:
    """Process outcome only. Ledger status is always re-read by the pipeline."""

    exit_code: int
    elapsed_ms: int
    pid: int | None = None
    note: str = ""
    role: str = ""  # builder|verifier|reviewer|critic|planner|ship


@runtime_checkable
class AgentBackend(Protocol):
    name: str

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
        """Run a stage to completion. Must not invent ledger status for the caller."""
        ...
