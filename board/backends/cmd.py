"""Generic shell-template backend (Pi and other headless agents).

CLI example::

    python3 -m board run --backend cmd \\
      --backend-cmd 'my-agent -p {prompt_file} --dir {cwd}'

Placeholders (string replace, not str.format — safe with braces in prompts):

- ``{prompt}`` — full stage prompt text (prefer ``{prompt_file}`` for shells)
- ``{prompt_file}`` — path to a written prompt file under the stage log dir
- ``{cwd}`` — target repo path
- ``{slice}`` — slice id (e.g. SL-0001)
- ``{stage}`` — stage name (build, verify, …)
- ``{kuru_py}`` — absolute path to scripts/kuru.py when known
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from board.backends.base import StageProcessResult
from board.cancel import wait_or_cancel
from board.prompts import stage_role

if TYPE_CHECKING:
    from board.cancel import RunControl


class CmdBackend:
    """AgentBackend that runs a user-supplied shell command template per stage."""

    name = "cmd"

    def __init__(
        self,
        template: str,
        *,
        kuru_py: Path | None = None,
        control: "RunControl | None" = None,
        shell: bool = True,
        env: dict[str, str] | None = None,
    ):
        if not (template or "").strip():
            raise ValueError("cmd backend requires a non-empty --backend-cmd template")
        self.template = template.strip()
        self.kuru_py = Path(kuru_py).resolve() if kuru_py else None
        self.control = control
        self.shell = shell
        self.env = env  # optional overlay; merged with os.environ at run time

    def expand(
        self,
        *,
        prompt: str,
        prompt_file: Path | str,
        cwd: Path | str,
        slice_id: str,
        stage: str,
    ) -> str:
        """Expand placeholders in the template (literal string replace)."""
        mapping = {
            "{prompt}": prompt,
            "{prompt_file}": str(prompt_file),
            "{cwd}": str(cwd),
            "{slice}": slice_id.upper(),
            "{stage}": stage,
            "{kuru_py}": str(self.kuru_py) if self.kuru_py else "",
        }
        out = self.template
        for key, val in mapping.items():
            out = out.replace(key, val)
        return out

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
        role = stage_role(stage)
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if self.control and self.control.is_cancelled(sid):
            elapsed = int((time.monotonic() - t0) * 1000)
            log_path.write_text(
                f"cmd backend: cancelled before spawn\nstage={stage} slice={sid}\n",
                encoding="utf-8",
            )
            return StageProcessResult(
                exit_code=130,
                elapsed_ms=elapsed,
                pid=None,
                note="cancelled",
                role=role,
            )

        prompt_file = log_path.parent / f"{stage}.prompt.md"
        prompt_file.write_text(prompt or "", encoding="utf-8")
        cmd_str = self.expand(
            prompt=prompt or "",
            prompt_file=prompt_file,
            cwd=cwd,
            slice_id=sid,
            stage=stage,
        )

        env = {**os.environ, **(self.env or {})}
        if self.kuru_py:
            env.setdefault("KURU_PY", str(self.kuru_py))

        header = (
            f"$ {cmd_str}\n"
            f"cwd={cwd}\n"
            f"stage={stage} slice={sid} role={role}\n"
            f"prompt_file={prompt_file}\n"
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
                if self.shell:
                    proc = subprocess.Popen(
                        cmd_str,
                        shell=True,
                        cwd=str(cwd),
                        env=env,
                        stdout=logf,
                        stderr=subprocess.STDOUT,
                        text=True,
                        start_new_session=True,
                    )
                else:
                    proc = subprocess.Popen(
                        shlex.split(cmd_str),
                        cwd=str(cwd),
                        env=env,
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
                        note = f"cmd {wait_note}"
                        logf.write(f"\n--- timeout: {note} ---\n")
                    else:
                        note = f"cmd exited {exit_code}"
                finally:
                    if self.control:
                        self.control.unbind_process(sid, proc)
            except OSError as e:
                exit_code = 127
                note = f"failed to spawn cmd: {e}"
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
