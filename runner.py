#!/usr/bin/env python3
"""
runner.py — external autonomous driver for the kuru delivery loop.

This is the headless counterpart to the in-session `/kuru:loop` command. It runs
*outside* Claude: a plain Python control loop that

  1. reads state deterministically from the engine  (`kuru.py next --json`), then
  2. launches a FRESH `claude -p` session for the one step that advances it
     (`/kuru:build`, `/kuru:verify`, or — when this workspace has review on —
     `/kuru:review`) — and ships a shippable slice (`verified` with review off, or
     `reviewed`) to `done` itself,

repeating until the board is clear. Each step is its own process with its own
context, so nothing accumulates: builder and verifier are not just separate
agents but separate OS processes. State carries between steps only through the
`.kuru/` files — which is exactly what the plugin persists.

With `--slice <id>` it drives a single named slice to `done` and stops instead of
clearing the board, asking the engine `kuru next --slice <id>` each step so it acts
on that one slice only (never a sibling the board would rank first).

Division of labour (deliberate):
  • The runner DECIDES (what's next, retries, when to stop) — never by asking a
    model; `kuru.py` is the brain.
  • The `claude -p` session DOES the work and is the only thing that writes
    *progress* status (`built`, `verified`), so the engine's gate + role rules still
    gate every real transition. The runner only writes two safety statuses itself:
    `blocked` when it finally gives up, and the retry-reset (`blocked`/`verifying`
    -> `in_progress`) that re-queues a failed build — never fabricated progress.

Retries: a TRY is one build->verify(->review) cycle, counted at the build that starts
it, so a failed *build* (the builder gives up -> `blocked`), a verify rejection, OR a
review rejection (when review is on) each counts and is retried with a fresh process —
up to `--max-tries` per slice, per run. Only a slice already blocked before the run
began is left for a human untouched.

Preconditions: charter + spec + frozen (non-draft) slices must already exist (in
`--slice` mode, only the named slice and its dependencies need contracts). The
runner refuses to start charter/spec/slicing — those need a human.

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
        self.max_tries = args.max_tries
        self.max_iters = args.max_iters
        self.dry_run = args.dry_run
        self.slice = (args.slice or "").upper() or None   # --slice: drive only this id
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
        """Escalate a slice to `blocked` and stop — a safety stop, never fabricated
        progress. The runner writes only this and the retry-reset to `in_progress`
        (see the loop); it never writes `built`/`verified`."""
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
    def check_preconditions(self, single: bool) -> str | None:
        kd = self.repo / ".kuru"
        if not kd.is_dir():
            return f"no .kuru workspace in {self.repo} (run `kuru init` first)."
        d = self.kuru_run("doctor")
        if d.returncode != 0:
            return f"workspace unhealthy:\n{d.stdout}{d.stderr}"
        charter = kd / "charter.md"
        if not charter.exists() or charter.stat().st_size < 50:
            return "charter missing/empty — run /kuru:charter (needs a human)."
        if not any((kd / "spec").glob("*.md")):
            return "no spec under .kuru/spec/ — run /kuru:spec (needs a human)."
        slices = self.kuru_json("ls", "--json")
        if not slices:
            return "no slices — run /kuru:slice (needs a human)."
        # The whole board must be contracted only to *clear the board*. In
        # single-slice mode just the target (and its deps) need a contract — other
        # drafts are fine — so defer to check_named_target / the loop's draft branch.
        if not single:
            drafts = [s["id"] for s in slices if s["status"] == "draft"]
            if drafts:
                return (f"draft slices need contracts before the loop can run: "
                        f"{', '.join(drafts)} — finish /kuru:slice (needs a human).")
        return None

    def check_named_target(self) -> str | None:
        """--slice <id>: the named slice must exist and have all deps `done`.
        `kuru next` enforces deps for an auto-picked target; naming one bypasses
        that, so re-check here rather than build on an unfinished prerequisite."""
        by_id = {s["id"]: s for s in self.kuru_json("ls", "--json")}
        if self.slice not in by_id:
            return f"no slice {self.slice} (have: {', '.join(by_id) or 'none'})."
        info = self.kuru_json("show", self.slice, "--json")
        if info["status"] == "draft":
            return f"{self.slice} is still a draft — finish /kuru:slice to contract it first."
        done = {sid for sid, s in by_id.items() if s["status"] == "done"}
        unmet = [d for d in (info.get("depends_on") or []) if d not in done]
        if unmet:
            return (f"{self.slice} depends on {', '.join(unmet)}, not done yet — "
                    f"finish {'those' if len(unmet) > 1 else 'it'} first "
                    f"(--slice each, or clear the whole board).")
        return None

    # -- main loop ------------------------------------------------------------ #
    def run(self) -> int:
        if self.claude is None and not self.dry_run:
            print("error: claude CLI not found (use --claude-bin).", file=sys.stderr)
            return 2

        # Single-slice mode (--slice <id>): drive exactly one named slice to done,
        # then stop, instead of clearing the whole board.
        single = self.slice is not None

        problem = self.check_preconditions(single)
        if problem:
            print(f"Refusing to start: {problem}")
            return 2

        if self.slice is not None:
            bad = self.check_named_target()
            if bad:
                print(f"Refusing to start: {bad}")
                return 2

        done_count = 0
        last_key = None       # (sid, status) we acted on last iteration
        stalls = 0
        tries = {}            # sid -> build->verify cycles started this run (the try budget)
        target = self.slice

        for i in range(1, self.max_iters + 1):
            # Single-slice mode asks the engine for the action of *this slice only*
            # (`next --slice <id>`) — never the board's pick — so it can't drift onto
            # a fresh ready sibling the board ranks ahead of shipping our target.
            if single:
                nxt = self.kuru_json("next", "--slice", target, "--json")
            else:
                nxt = self.kuru_json("next", "--json")
            action = nxt["next_action"]

            if action == "none":
                reason = nxt.get("reason")
                if reason in ("all_done", "done"):
                    if single:
                        print(f"\n✓ {target} reached done — one slice complete.")
                    else:
                        print(f"\n✓ Board clear — every slice is done. ({done_count} reached done this run)")
                    return 0
                if reason == "waiting_on_deps":
                    waits = ", ".join(f"{w['id']}<-{','.join(w['unmet'])}" for w in nxt.get("waiting", []))
                    print(f"\n⛔ Waiting on dependencies that won't resolve here: {waits}")
                    print("   A dependency is blocked or undone. Needs a human.")
                    return 1
                # blocked: a named slice that is itself blocked (reason 'blocked'), or
                # the board's blocked set (reason 'blocked_present').
                stuck = nxt.get("blocked") or ([nxt["id"]] if nxt.get("status") == "blocked" else [])
                print(f"\n⛔ Nothing actionable; blocked slices need a human: {', '.join(stuck)}")
                return 1

            sid, status = nxt["id"], nxt["status"]

            if action == "slice":
                print(f"\n⛔ {sid} is a draft and needs human slicing/contracting — run /kuru:slice.")
                return 1

            if action == "ship":
                # Ship a slice the engine says is shippable: `verified` when review is
                # off, or `reviewed` when review is on (the engine routes verified ->
                # review first via the `review` action, handled by the generic dispatch
                # below). Ship inline as one `done` (auto-commits).
                print(f"[{i}] {sid} [{status}] -> ship  ({nxt['title']})")
                self.kuru_run("set-status", sid, "done")
                print(f"  ✓ {sid} -> done")
                done_count += 1
                last_key = (sid, status)
                # single-slice mode: the next iteration re-derives the target as done and stops.
                continue

            print(f"[{i}] {sid} [{status}] -> {action}  ({nxt['title']})")

            # Per-run try cap. A TRY is one build->verify cycle, counted at the build
            # that starts it — so a failed *build* (builder gave up -> blocked, reset
            # to in_progress below) costs a try just like a verify rejection (rejected
            # -> action 'build'). Exhaust the budget -> stop for a human. Per run: the
            # `tries` map starts empty each run, so a re-run gets a fresh budget.
            if action == "build":
                if tries.get(sid, 0) >= self.max_tries:
                    self.block(sid, f"exhausted {self.max_tries} build->verify tries this run")
                    print("   Stopping for a human.")
                    return 1
                tries[sid] = tries.get(sid, 0) + 1

            # stall guard: same slice+status again after we acted = no progress;
            # allow one retry, then block (i.e. stop on the third consecutive sighting)
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
            st = after["status"]
            if st == "done" and status != "done":
                done_count += 1
                last_key = key
            elif st == "blocked" and tries.get(sid, 0) < self.max_tries:
                # A build/verify that gave up mid-run is a FAILED try, not a stop:
                # reset to a buildable status so the next iteration rebuilds with a
                # fresh process. The build-dispatch cap above bounds the retries, then
                # blocks for a human. Clear the stall tracking so the retried build
                # isn't mistaken for a no-progress stall.
                self.kuru_run("set-status", sid, "in_progress",
                              "--note", "retry after a failed build/verify")
                print(f"  ↻ {sid} blocked -> in_progress "
                      f"(retry; {tries.get(sid, 0)}/{self.max_tries} tries used)")
                last_key = None
                stalls = 0
            else:
                # blocked with the try budget already spent -> leave it for a human
                # (next iteration surfaces it via `next` and stops).
                last_key = key

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
    p.add_argument("--max-tries", "--max-retries", dest="max_tries", type=int, default=2,
                   help="per-slice, per-run cap on build->verify(->review) TRIES before blocking (default 2). "
                        "One try = one full build->verify->review cycle; a failed build (blocked), a verify "
                        "rejection, or a review rejection each costs a try and is retried with a fresh "
                        "process. (--max-retries: alias.)")
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
    p.add_argument("--slice", default=None, metavar="ID",
                   help="drive only this slice (e.g. SL-0003) all the way to done, then "
                        "stop — instead of clearing the whole board. Refuses if the slice "
                        "is a draft or its deps aren't done.")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.claude_bin = find_claude(args.claude_bin)
    sys.exit(Runner(args).run())


if __name__ == "__main__":
    main()
