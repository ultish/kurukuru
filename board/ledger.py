"""Thin wrapper around scripts/kuru.py — the only path to machine state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class KuruError(RuntimeError):
    def __init__(self, cmd: list[str], returncode: int, stderr: str, stdout: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        super().__init__(
            f"kuru {' '.join(cmd[2:])} failed (exit {returncode}): "
            f"{(stderr or stdout).strip() or 'no output'}"
        )


class Ledger:
    """Invoke kuru.py against a target repo."""

    def __init__(self, repo: Path, kuru_py: Path, env: dict[str, str] | None = None):
        self.repo = Path(repo).resolve()
        self.kuru_py = Path(kuru_py).resolve()
        if not self.kuru_py.is_file():
            raise FileNotFoundError(f"kuru.py not found: {self.kuru_py}")
        self.env = {**os.environ, **(env or {})}
        # Prefer explicit plugin root when present so templates/engine resolve.
        plugin_root = self.kuru_py.parent.parent
        self.env.setdefault("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        self.env.setdefault("KURU_PY", str(self.kuru_py))

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, str(self.kuru_py), *args]
        proc = subprocess.run(
            cmd,
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise KuruError(cmd, proc.returncode, proc.stderr, proc.stdout)
        return proc

    def json(self, *args: str) -> Any:
        """Run a kuru subcommand that prints JSON (caller must pass --json)."""
        proc = self.run(*args)
        text = proc.stdout.strip()
        if not text:
            raise KuruError(
                [sys.executable, str(self.kuru_py), *args],
                proc.returncode,
                proc.stderr,
                "empty stdout (expected JSON)",
            )
        return json.loads(text)

    def next_all(self) -> dict[str, Any]:
        return self.json("next", "--all", "--json")

    def next_one(self, slice_id: str | None = None) -> dict[str, Any]:
        if slice_id:
            return self.json("next", "--slice", slice_id, "--json")
        return self.json("next", "--json")

    def show(self, slice_id: str) -> dict[str, Any]:
        return self.json("show", slice_id, "--json")

    def doctor(self) -> subprocess.CompletedProcess[str]:
        """doctor exits 1 on hard problems; always return the process."""
        return self.run("doctor", check=False)

    def commit(self, message: str | None = None, slices: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        args: list[str] = ["commit"]
        if message:
            args += ["--message", message]
        if slices:
            args += ["--slices", ",".join(slices)]
        return self.run(*args)


def resolve_kuru_py(plugin_dir: Path | None = None) -> Path:
    """Locate kuru.py from --plugin-dir or this package's parent repo."""
    if plugin_dir is not None:
        candidate = Path(plugin_dir).resolve() / "scripts" / "kuru.py"
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"no scripts/kuru.py under plugin-dir {plugin_dir}")

    # board/ lives at <plugin>/board/ → parent is plugin root
    here = Path(__file__).resolve().parent.parent
    candidate = here / "scripts" / "kuru.py"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        "could not find scripts/kuru.py; pass --plugin-dir pointing at the kurukuru checkout"
    )
