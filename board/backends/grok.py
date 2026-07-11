"""Grok Build backend — one fresh `grok -p` process per stage.

Grok does not load Claude plugin slash commands. Stage prompts must be
self-contained (see ``board.prompts.stage_prompt_grok``): point at skills on
disk under the plugin root and drive ledger transitions via ``KURU_PY``.

Default auto-approve for autonomous board runs uses ``--always-approve``
(Grok has no ``--yolo`` flag). Disable with ``always_approve=False`` if you
need interactive permission prompts (not typical for board).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from board.backends.base import StageProcessResult
from board.prompts import stage_prompt_grok, stage_role


def find_grok(explicit: str | None = None) -> str | None:
    """Locate the grok CLI.

    Search order: explicit path → PATH → common install locations
    (``~/.local/bin/grok``, ``~/.grok/bin/grok``, Homebrew).
    """
    if explicit:
        p = Path(explicit)
        return str(p) if p.exists() else None
    found = shutil.which("grok")
    if found:
        return found
    for candidate in (
        Path.home() / ".local/bin/grok",
        Path.home() / ".grok/bin/grok",
        Path("/opt/homebrew/bin/grok"),
        Path("/usr/local/bin/grok"),
    ):
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class GrokNotFoundError(RuntimeError):
    """Raised when the grok binary cannot be resolved."""


class GrokBackend:
    """AgentBackend that spawns ``grok -p '<self-contained prompt>'`` per stage."""

    name = "grok"

    def __init__(
        self,
        *,
        plugin_dir: Path,
        grok_bin: str | None = None,
        always_approve: bool = True,
        permission_mode: str | None = None,
        model: str | None = None,
        max_turns: int | None = None,
        kuru_py: Path | None = None,
        extra_args: list[str] | None = None,
    ):
        self.plugin_dir = Path(plugin_dir).resolve()
        self.grok_bin = grok_bin  # may still be None; fail at run_stage
        # Board default: autonomous runs. Maps to Grok's --always-approve
        # (there is no --yolo on the grok CLI as of 2026-07).
        self.always_approve = always_approve
        self.permission_mode = permission_mode
        self.model = model
        self.max_turns = max_turns
        self.kuru_py = (
            Path(kuru_py).resolve()
            if kuru_py
            else self.plugin_dir / "scripts" / "kuru.py"
        )
        self.extra_args = list(extra_args or [])
        self.env = {
            **os.environ,
            "CLAUDE_PLUGIN_ROOT": str(self.plugin_dir),
            "KURU_PY": str(self.kuru_py),
        }

    def build_cmd(self, prompt: str, *, cwd: Path | None = None) -> list[str]:
        if not self.grok_bin:
            raise GrokNotFoundError(
                "grok CLI not found (pass --grok-bin or install the Grok Build CLI)"
            )
        # -p / --single: headless single-turn; prints response and exits.
        cmd = [self.grok_bin, "-p", prompt]
        if cwd is not None:
            cmd += ["--cwd", str(cwd)]
        if self.always_approve:
            cmd.append("--always-approve")
        if self.permission_mode:
            cmd += ["--permission-mode", self.permission_mode]
        if self.model:
            cmd += ["--model", self.model]
        if self.max_turns is not None:
            cmd += ["--max-turns", str(self.max_turns)]
        if self.extra_args:
            cmd += self.extra_args
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
        # Prefer orchestrator-supplied prompt; fall back to skill-based form.
        effective = (prompt or "").strip() or stage_prompt_grok(
            stage,
            sid,
            plugin_dir=self.plugin_dir,
            kuru_py=self.kuru_py,
        )
        role = stage_role(stage)
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            cmd = self.build_cmd(effective, cwd=Path(cwd))
        except GrokNotFoundError as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            log_path.write_text(
                f"grok backend error: {e}\n"
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
        with log_path.open("w", encoding="utf-8") as logf:
            logf.write(header)
            logf.flush()
            try:
                # Popen so hierarchical TUI can eventually surface a real pid
                # (pipeline currently emits spawn with pid=None pre-stage; we
                # still return pid on exit for backend.exited / logs).
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    env=self.env,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                pid = proc.pid
                try:
                    exit_code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    exit_code = 124
                    note = f"grok timed out after {timeout}s"
                    logf.write(f"\n--- timeout: {note} ---\n")
                else:
                    note = f"grok exited {exit_code}"
            except OSError as e:
                exit_code = 127
                note = f"failed to spawn grok: {e}"
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
