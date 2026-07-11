"""Claude Code backend — one fresh `claude -p` process per stage.

Ports stage dispatch from `runner.py`. Outcome is always re-read from the ledger
by the pipeline; this backend only runs the process and logs its output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from board.backends.base import StageProcessResult
from board.cancel import wait_or_cancel
from board.prompts import stage_prompt, stage_role

if TYPE_CHECKING:
    from board.cancel import RunControl


def find_claude(explicit: str | None = None) -> str | None:
    """Locate the claude CLI (same search order as runner.py)."""
    if explicit:
        p = Path(explicit)
        return str(p) if p.exists() else None
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (
        Path.home() / ".local/bin/claude",
        Path.home() / ".claude/local/claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class ClaudeNotFoundError(RuntimeError):
    """Raised when the claude binary cannot be resolved."""


class ClaudeBackend:
    """AgentBackend that spawns `claude -p '/kuru:<stage> …'` per stage."""

    name = "claude"

    def __init__(
        self,
        *,
        plugin_dir: Path,
        claude_bin: str | None = None,
        permission_mode: str | None = "bypassPermissions",
        allowed_tools: str | None = None,
        settings: str | None = None,
        model: str | None = None,
        kuru_py: Path | None = None,
        control: "RunControl | None" = None,
    ):
        self.plugin_dir = Path(plugin_dir).resolve()
        self.claude_bin = claude_bin  # may still be None; fail at run_stage
        self.permission_mode = permission_mode
        self.allowed_tools = allowed_tools
        self.settings = settings
        self.model = model
        self.kuru_py = (
            Path(kuru_py).resolve()
            if kuru_py
            else self.plugin_dir / "scripts" / "kuru.py"
        )
        self.control = control
        self.env = {
            **os.environ,
            "CLAUDE_PLUGIN_ROOT": str(self.plugin_dir),
            "KURU_PY": str(self.kuru_py),
        }

    def build_cmd(self, prompt: str) -> list[str]:
        if not self.claude_bin:
            raise ClaudeNotFoundError(
                "claude CLI not found (pass --claude-bin or install the Claude Code CLI)"
            )
        cmd = [
            self.claude_bin,
            "-p",
            prompt,
            "--plugin-dir",
            str(self.plugin_dir),
        ]
        if self.settings:
            cmd += ["--settings", self.settings]
        if self.allowed_tools:
            cmd += ["--allowedTools", self.allowed_tools]
        if self.permission_mode:
            cmd += ["--permission-mode", self.permission_mode]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

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
        # Prefer orchestrator-supplied prompt; fall back to slash-command form.
        effective = (prompt or "").strip() or stage_prompt(stage, sid)
        role = stage_role(stage)
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if self.control and self.control.is_cancelled(sid):
            elapsed = int((time.monotonic() - t0) * 1000)
            log_path.write_text(
                f"claude backend: cancelled before spawn\n"
                f"stage={stage} slice={sid}\n",
                encoding="utf-8",
            )
            return StageProcessResult(
                exit_code=130,
                elapsed_ms=elapsed,
                pid=None,
                note="cancelled",
                role=role,
            )

        try:
            cmd = self.build_cmd(effective)
        except ClaudeNotFoundError as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            log_path.write_text(
                f"claude backend error: {e}\n"
                f"stage={stage} slice={sid}\nprompt={effective}\n",
                encoding="utf-8",
            )
            return StageProcessResult(
                exit_code=127,
                elapsed_ms=elapsed,
                pid=None,
                note=str(e),
                role=role,
            )

        header = (
            f"$ {' '.join(cmd)}\n"
            f"cwd={cwd}\n"
            f"stage={stage} slice={sid} role={role}\n"
            f"---\n"
        )
        pid: int | None = None
        note = ""
        exit_code = 1
        cancel_check = self.control.cancel_check(sid) if self.control else None

        with log_path.open("w", encoding="utf-8") as logf:
            logf.write(header)
            logf.flush()
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    env=self.env,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                pid = proc.pid
                if self.control:
                    self.control.bind_process(sid, proc)
                try:
                    exit_code, wait_note = wait_or_cancel(
                        proc,
                        cancel_check=cancel_check,
                        timeout=timeout,
                    )
                    if wait_note == "cancelled":
                        note = "cancelled"
                    elif wait_note.startswith("timed out"):
                        note = f"claude {wait_note}"
                        logf.write(f"\n--- timeout: {note} ---\n")
                    else:
                        note = f"claude exited {exit_code}"
                finally:
                    if self.control:
                        self.control.unbind_process(sid, proc)
            except OSError as e:
                exit_code = 127
                note = f"failed to spawn claude: {e}"
                logf.write(f"\n--- spawn error: {e} ---\n")
                pid = None

        elapsed = int((time.monotonic() - t0) * 1000)
        return StageProcessResult(
            exit_code=exit_code,
            elapsed_ms=elapsed,
            pid=pid,
            note=note,
            role=role,
        )
