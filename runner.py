#!/usr/bin/env python3
"""
runner.py — external autonomous driver for the kuru delivery loop.

This is the headless counterpart to the in-session `/kuru:loop` command. It runs
*outside* Claude: a plain Python control loop that

  1. reads state deterministically from the engine  (`kuru.py next --json`), then
  2. launches a FRESH `claude -p` session for the one step that advances it
     (`/kuru:build`, `/kuru:verify`, or `/kuru:review`),

repeating until the board is clear. Each step is its own process with its own
context, so nothing accumulates: builder and verifier are not just separate
agents but separate OS processes. State carries between steps only through the
`.kuru/` files — which is exactly what the plugin persists.

Division of labour (deliberate):
  • The runner DECIDES (what's next, retries, when to stop) — never by asking a
    model; `kuru.py` is the brain.
  • The `claude -p` session DOES the work and is the only thing that writes
    progress status, so the engine's gate + role rules still gate every
    transition. The runner only escalates to `blocked` when it gives up.

Preconditions: charter + PRD + frozen (non-draft) slices must already exist.
The runner refuses to start charter/PRD/slicing — those need a human.

Requires: python3 (stdlib only) and the `claude` CLI. No third-party deps.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_claude(explicit: str | None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which("claude")
    if found:
        return found
    for p in (
        Path.home() / ".local/bin/claude",
        Path.home() / ".claude/local/claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ):
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return None


class Runner:
    def __init__(self, args):
        self.repo = Path(args.repo).resolve()
        self.plugin_dir = Path(args.plugin_dir).resolve()
        self.kuru = str(self.plugin_dir / "scripts" / "kuru.py")
        self.max_retries = args.max_retries
        self.max_iters = args.max_iters
        self.dry_run = args.dry_run
        self.once = args.once
        self.claude = args.claude_bin
        self.permission_mode = args.permission_mode
        self.allowed_tools = args.allowed_tools
        self.settings = args.settings
        self.model = args.model
        self.env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(self.plugin_dir)}

    # -- engine (the brain): read-only state queries + escalation only -------- #
    def kuru_json(self, *cmd) -> dict | list:
        out = subprocess.run(
            [sys.executable, self.kuru, *cmd],
            cwd=self.repo, env=self.env, capture_output=True, text=True,
        )
        if out.returncode != 0:
            raise RuntimeError(f"kuru {' '.join(cmd)} failed: {out.stderr.strip()}")
        return json.loads(out.stdout)

    def kuru_run(self, *cmd) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, self.kuru, *cmd],
            cwd=self.repo, env=self.env, capture_output=True, text=True,
        )

    def block(self, sid: str, note: str):
        """Escalate a slice to `blocked` and stop. This is the only status the
        runner ever writes — a safety stop, never fabricated progress."""
        self.kuru_run("set-status", sid, "blocked", "--note", note)
        print(f"  ! {sid} -> blocked: {note}")

    # -- the work (a fresh Claude per step) ----------------------------------- #
    def dispatch(self, action: str, sid: str) -> int:
        prompt = f"/kuru:{action} {sid}"
        cmd = [self.claude, "-p", prompt, "--plugin-dir", str(self.plugin_dir)]
        if self.settings:
            cmd += ["--settings", self.settings]
        if self.allowed_tools:
            cmd += ["--allowedTools", self.allowed_tools]
        if self.permission_mode:
            cmd += ["--permission-mode", self.permission_mode]
        if self.model:
            cmd += ["--model", self.model]
        print(f"  $ claude -p '{prompt}'  ({self.permission_mode or 'default perms'})")
        # stream the session's output live; we judge progress from engine state
        proc = subprocess.run(cmd, cwd=self.repo, env=self.env)
        return proc.returncode

    # -- preconditions -------------------------------------------------------- #
    def check_preconditions(self) -> str | None:
        kd = self.repo / ".kuru"
        if not kd.is_dir():
            return f"no .kuru workspace in {self.repo} (run `kuru init` first)."
        d = self.kuru_run("doctor")
        if d.returncode != 0:
            return f"workspace unhealthy:\n{d.stdout}{d.stderr}"
        charter = kd / "charter.md"
        if not charter.exists() or charter.stat().st_size < 50:
            return "charter missing/empty — run /kuru:charter (needs a human)."
        if not any((kd / "prd").glob("*.md")):
            return "no PRD under .kuru/prd/ — run /kuru:prd (needs a human)."
        slices = self.kuru_json("ls", "--json")
        if not slices:
            return "no slices — run /kuru:slice (needs a human)."
        drafts = [s["id"] for s in slices if s["status"] == "draft"]
        if drafts:
            return (f"draft slices need contracts before the loop can run: "
                    f"{', '.join(drafts)} — finish /kuru:slice (needs a human).")
        return None

    # -- main loop ------------------------------------------------------------ #
    def run(self) -> int:
        if self.claude is None and not self.dry_run:
            print("error: claude CLI not found (use --claude-bin).", file=sys.stderr)
            return 2

        problem = self.check_preconditions()
        if problem:
            print(f"Refusing to start: {problem}")
            return 2

        done_count = 0
        last_key = None       # (sid, status) we acted on last iteration
        stalls = 0

        for i in range(1, self.max_iters + 1):
            nxt = self.kuru_json("next", "--json")
            action = nxt["next_action"]

            if action == "none":
                reason = nxt.get("reason")
                if reason == "all_done":
                    print(f"\n✓ Board clear — every slice is done. ({done_count} reached done this run)")
                    return 0
                if reason == "waiting_on_deps":
                    waits = ", ".join(f"{w['id']}<-{','.join(w['unmet'])}" for w in nxt.get("waiting", []))
                    print(f"\n⛔ Deadlocked waiting on dependencies that won't resolve: {waits}")
                    print("   A dependency is blocked or undone. Needs a human.")
                    return 1
                print(f"\n⛔ Nothing actionable; blocked slices need a human: "
                      f"{', '.join(nxt.get('blocked', []))}")
                return 1

            sid, status = nxt["id"], nxt["status"]

            if action == "slice":
                print(f"\n⛔ {sid} is a draft and needs human slicing/contracting — run /kuru:slice.")
                return 1

            print(f"[{i}] {sid} [{status}] -> {action}  ({nxt['title']})")

            # retry cap on the build<->verify reject cycle
            if status == "rejected":
                info = self.kuru_json("show", sid, "--json")
                if info.get("rejections", 0) >= self.max_retries:
                    self.block(sid, f"exceeded {self.max_retries} verify rejections")
                    print("   Stopping for a human.")
                    return 1

            # stall guard: same slice+status twice in a row after we acted = no progress
            key = (sid, status)
            if key == last_key:
                stalls += 1
                if stalls >= 2:
                    self.block(sid, f"no progress after retry (stuck at {status})")
                    print("   Stopping for a human.")
                    return 1
            else:
                stalls = 0

            if self.dry_run:
                print(f"   (dry-run) would run: claude -p '/kuru:{action} {sid}'")
                return 0

            rc = self.dispatch(action, sid)
            if rc != 0:
                print(f"   claude exited {rc}; re-reading state and continuing.")

            after = self.kuru_json("show", sid, "--json")
            if after["status"] == "done" and status != "done":
                done_count += 1
            last_key = key

            if self.once:
                print("\n(--once) stopping after one step.")
                return 0

        print(f"\n⚠ Hit --max-iters ({self.max_iters}) without clearing the board.")
        return 1


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent  # runner.py sits at the plugin repo root
    p = argparse.ArgumentParser(
        prog="runner.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".", help="target repo containing .kuru/ (default: cwd)")
    p.add_argument("--plugin-dir", default=str(here),
                   help="the kuru plugin directory (default: the directory holding this script). "
                        "Set this if you move runner.py out of the plugin repo.")
    p.add_argument("--max-retries", type=int, default=2,
                   help="per-slice verify-rejection cap before blocking (default 2)")
    p.add_argument("--max-iters", type=int, default=100,
                   help="global safety cap on loop iterations (default 100)")
    p.add_argument("--permission-mode", default="bypassPermissions",
                   help="claude --permission-mode for each step. Default bypassPermissions "
                        "(autonomous). Use 'acceptEdits'/'default' for a tighter run, ideally "
                        "with --settings allowlisting the gate/kuru.py commands up front.")
    p.add_argument("--allowed-tools", default=None,
                   help="pass-through to claude --allowedTools (tighten instead of bypassing).")
    p.add_argument("--settings", default=None,
                   help="pass-through to claude --settings (a JSON file allowlisting permissions up front).")
    p.add_argument("--model", default=None, help="pass-through to claude --model.")
    p.add_argument("--claude-bin", dest="claude_bin", default=None,
                   help="path to the claude CLI (default: autodetect).")
    p.add_argument("--dry-run", action="store_true",
                   help="print the next action and exit without launching claude.")
    p.add_argument("--once", action="store_true", help="do a single step then stop.")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.claude_bin = find_claude(args.claude_bin)
    sys.exit(Runner(args).run())


if __name__ == "__main__":
    main()
