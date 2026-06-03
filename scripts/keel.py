#!/usr/bin/env python3
"""
keel — deterministic state + gate engine for the Keel enterprise harness.

This is the only thing in the harness that is allowed to mutate machine state.
Agents (builder, verifier, planner) reason and write narrative artifacts; the
*facts* — what slice exists, what status it is in, whether the gates passed —
live in JSON managed here so they cannot be hand-waved.

Zero third-party dependencies: Python 3 stdlib only (ships on macOS/Linux).

Usage:
  keel init                         scaffold .keel/ in the current repo
  keel new-slice "<title>" [--epic E]
  keel ls [--status S]              list slices (table)
  keel show <id>                    show one slice (paths + state + history)
  keel next                         print the next actionable slice
  keel set-status <id> <status> [--note "..."] [--by agent|human]
  keel gate <id>                    run the configured deterministic gates
  keel check <id>                   print whether <id> may advance to 'verified'
  keel doctor                       sanity-check the .keel workspace

Statuses (the slice state machine):
  draft -> ready -> in_progress -> built -> verifying -> verified -> reviewed -> done
  any -> blocked ;  verifying -> rejected -> in_progress
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

STATUSES = [
    "draft", "ready", "in_progress", "built",
    "verifying", "verified", "rejected", "reviewed", "done", "blocked",
]

# Allowed transitions. 'blocked' is reachable from anywhere and exits back to
# wherever the human/agent decides, so it is handled separately.
TRANSITIONS = {
    "draft": {"ready", "blocked"},
    "ready": {"in_progress", "draft", "blocked"},
    "in_progress": {"built", "blocked"},
    "built": {"verifying", "in_progress", "blocked"},
    "verifying": {"verified", "rejected", "blocked"},
    "rejected": {"in_progress", "blocked"},
    "verified": {"reviewed", "rejected", "blocked"},
    "reviewed": {"done", "in_progress", "blocked"},
    "done": {"in_progress"},  # reopen
    "blocked": set(STATUSES),  # unblock to anywhere
}

# Statuses an agent that is *implementing* is allowed to set on its own.
# Crossing into 'verified'/'reviewed' requires --by human or the verifier/review
# gate, never the builder. keel enforces the gate facts; honesty about --by is a
# convention the subagent prompts reinforce.
BUILDER_SETTABLE = {"ready", "in_progress", "built", "blocked"}

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent.parent))
TEMPLATES = PLUGIN_ROOT / "templates"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def now() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def find_root(start: Path | None = None) -> Path | None:
    """Walk up looking for a .keel directory."""
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / ".keel").is_dir():
            return cand
    return None


def keel_dir() -> Path:
    root = find_root()
    if root is None:
        die("no .keel workspace found. Run `keel init` in your repo root first.")
    return root / ".keel"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def die(msg: str, code: int = 1):
    print(f"keel: error: {msg}", file=sys.stderr)
    sys.exit(code)


def render(template: str, **kw) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def read_template(name: str) -> str:
    path = TEMPLATES / name
    if not path.exists():
        die(f"missing template {name} (looked in {TEMPLATES})")
    return path.read_text()


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #
def ledger_path() -> Path:
    return keel_dir() / "ledger.json"


def load_ledger() -> dict:
    return load_json(ledger_path())


def save_ledger(led: dict) -> None:
    save_json(ledger_path(), led)


def get_slice(led: dict, sid: str) -> dict | None:
    sid = sid.upper()
    for s in led["slices"]:
        if s["id"] == sid:
            return s
    return None


def next_id(led: dict) -> str:
    nums = [int(s["id"].split("-")[1]) for s in led["slices"] if re.match(r"SL-\d+$", s["id"])]
    n = (max(nums) + 1) if nums else 1
    return f"SL-{n:04d}"


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_init(args):
    root = Path.cwd()
    kd = root / ".keel"
    if kd.exists() and not args.force:
        die(f"{kd} already exists (use --force to re-scaffold missing files)")
    for sub in ("", "slices", "prd"):
        (kd / sub).mkdir(parents=True, exist_ok=True)

    seed = {
        "config.json": read_template("config.json"),
        "ledger.json": json.dumps(
            {"meta": {"project": root.name, "created": now()}, "slices": []}, indent=2
        ) + "\n",
        "charter.md": render(read_template("charter.md"), DATE=now(), PROJECT=root.name),
        "progress.md": render(read_template("progress.md"), DATE=now(), PROJECT=root.name),
        "README.md": read_template("workspace-readme.md"),
    }
    for name, content in seed.items():
        path = kd / name
        if path.exists() and not args.force:
            continue
        path.write_text(content)
    print(f"Initialized Keel workspace at {kd}")
    print("Next: edit .keel/config.json gates, then run /keel:charter")


def cmd_new_slice(args):
    led = load_ledger()
    sid = next_id(led)
    kd = keel_dir()
    sdir = kd / "slices" / sid
    sdir.mkdir(parents=True, exist_ok=False)

    fields = dict(ID=sid, TITLE=args.title, DATE=now(), EPIC=args.epic or "—")
    (sdir / "slice.md").write_text(render(read_template("slice.md"), **fields))
    (sdir / "contract.yml").write_text(render(read_template("contract.yml"), **fields))
    (sdir / "build-log.md").write_text(render(read_template("build-log.md"), **fields))
    (sdir / "verification.md").write_text(render(read_template("verification.md"), **fields))

    led["slices"].append({
        "id": sid,
        "title": args.title,
        "epic": args.epic,
        "status": "draft",
        "created": now(),
        "updated": now(),
        "history": [{"at": now(), "status": "draft", "by": "human", "note": "created"}],
    })
    save_ledger(led)
    print(f"Created {sid}: {args.title}")
    print(f"  dir: {sdir}")
    print("  Fill in slice.md + contract.yml, then `keel set-status %s ready`." % sid)


def cmd_ls(args):
    led = load_ledger()
    rows = led["slices"]
    if args.status:
        rows = [s for s in rows if s["status"] == args.status]
    if not rows:
        print("(no slices)")
        return
    width = max(len(s["title"]) for s in rows)
    print(f"{'ID':<8} {'STATUS':<12} {'EPIC':<10} TITLE")
    print("-" * (8 + 12 + 10 + min(width, 50) + 3))
    for s in rows:
        print(f"{s['id']:<8} {s['status']:<12} {str(s.get('epic') or '—'):<10} {s['title']}")


def cmd_show(args):
    led = load_ledger()
    s = get_slice(led, args.id)
    if not s:
        die(f"no slice {args.id}")
    sdir = keel_dir() / "slices" / s["id"]
    print(json.dumps(s, indent=2))
    print("\nartifacts:")
    for f in ("slice.md", "contract.yml", "build-log.md", "verification.md", "gate-results.json"):
        p = sdir / f
        print(f"  {'✓' if p.exists() else '·'} {p}")


def cmd_next(args):
    """The next thing a human/agent should pick up, in pipeline order."""
    led = load_ledger()
    order = ["verifying", "built", "rejected", "in_progress", "ready", "verified", "draft"]
    label = {
        "verifying": "needs a verifier",
        "built": "ready to verify",
        "rejected": "verifier rejected — back to builder",
        "in_progress": "build in progress",
        "ready": "ready to build",
        "verified": "ready for code review",
        "draft": "needs a contract before it can be built",
    }
    for st in order:
        cand = [s for s in led["slices"] if s["status"] == st]
        if cand:
            s = cand[0]
            print(f"{s['id']}  [{st}]  {s['title']}")
            print(f"  -> {label[st]}")
            print(f"  -> {keel_dir() / 'slices' / s['id']}")
            return
    print("No actionable slices. Either everything is done/blocked, or run /keel:slice.")


def cmd_set_status(args):
    led = load_ledger()
    s = get_slice(led, args.id)
    if not s:
        die(f"no slice {args.id}")
    new = args.status
    if new not in STATUSES:
        die(f"unknown status '{new}'. one of: {', '.join(STATUSES)}")
    cur = s["status"]
    allowed = TRANSITIONS.get(cur, set())
    if new != cur and new not in allowed and cur != "blocked":
        die(f"illegal transition {cur} -> {new}. allowed from {cur}: {', '.join(sorted(allowed)) or '(none)'}")

    # Hard gate: cannot enter 'verified' unless gates are green for this slice.
    if new == "verified":
        gr = _latest_gate(s["id"])
        if gr is None:
            die(f"cannot mark {s['id']} verified: no gate run found. Run `keel gate {s['id']}` first.")
        if not gr["passed"]:
            die(f"cannot mark {s['id']} verified: last gate run FAILED "
                f"({gr['summary']}). Fix and re-run `keel gate {s['id']}`.")
    if new in {"verified", "reviewed"} and args.by == "builder":
        die(f"a builder may not set '{new}'. This requires the verifier/reviewer (--by human|verifier).")

    s["status"] = new
    s["updated"] = now()
    s["history"].append({"at": now(), "status": new, "by": args.by, "note": args.note or ""})
    save_ledger(led)
    print(f"{s['id']}: {cur} -> {new}")


def _config() -> dict:
    return load_json(keel_dir() / "config.json")


def _latest_gate(sid: str) -> dict | None:
    p = keel_dir() / "slices" / sid.upper() / "gate-results.json"
    if not p.exists():
        return None
    return load_json(p)


def cmd_gate(args):
    led = load_ledger()
    s = get_slice(led, args.id)
    if not s:
        die(f"no slice {args.id}")
    cfg = _config()
    gates = cfg.get("gates", {})
    if not gates:
        die("no gates configured in .keel/config.json")

    results = []
    overall = True
    print(f"Running gates for {s['id']} ...\n")
    for name, spec in gates.items():
        cmd = spec["cmd"]
        required = spec.get("required", True)
        print(f"  ▶ {name}: {cmd}")
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=spec.get("timeout", 1800),
                cwd=find_root(),
            )
            code = proc.returncode
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-30:]
        except subprocess.TimeoutExpired:
            code = 124
            tail = [f"TIMEOUT after {spec.get('timeout', 1800)}s"]
        ok = code == 0
        if required and not ok:
            overall = False
        status = "PASS" if ok else ("FAIL" if required else "WARN")
        print(f"    {status} (exit {code})\n")
        results.append({
            "name": name, "cmd": cmd, "required": required,
            "exit_code": code, "passed": ok, "output_tail": tail,
        })

    summary = ", ".join(f"{r['name']}={'ok' if r['passed'] else 'x'}" for r in results)
    record = {
        "slice": s["id"], "ran_at": now(), "passed": overall,
        "summary": summary, "gates": results,
    }
    save_json(keel_dir() / "slices" / s["id"] / "gate-results.json", record)
    print(f"GATE {'PASS' if overall else 'FAIL'} — {summary}")
    print(f"recorded -> {keel_dir() / 'slices' / s['id'] / 'gate-results.json'}")
    sys.exit(0 if overall else 1)


def cmd_check(args):
    """Read-only: can this slice be advanced to 'verified'?"""
    led = load_ledger()
    s = get_slice(led, args.id)
    if not s:
        die(f"no slice {args.id}")
    gr = _latest_gate(s["id"])
    print(f"slice:   {s['id']}  ({s['status']})")
    if gr is None:
        print("gates:   NOT RUN  -> cannot verify")
        sys.exit(1)
    print(f"gates:   {'PASS' if gr['passed'] else 'FAIL'}  ({gr['summary']})  at {gr['ran_at']}")
    contract = keel_dir() / "slices" / s["id"] / "contract.yml"
    print(f"contract:{' present' if contract.exists() else ' MISSING'}")
    print("\nReminder: green gates are necessary, not sufficient. The verifier must still")
    print("cite concrete evidence for every acceptance criterion in contract.yml.")
    sys.exit(0 if gr["passed"] else 1)


def cmd_doctor(args):
    root = find_root()
    if not root:
        die("no .keel workspace found here.")
    kd = root / ".keel"
    problems = []
    for f in ("config.json", "ledger.json", "charter.md", "progress.md"):
        if not (kd / f).exists():
            problems.append(f"missing {f}")
    try:
        cfg = load_json(kd / "config.json")
        if not cfg.get("gates"):
            problems.append("config.json has no gates configured")
    except Exception as e:
        problems.append(f"config.json invalid: {e}")
    try:
        load_json(kd / "ledger.json")
    except Exception as e:
        problems.append(f"ledger.json invalid: {e}")
    if problems:
        print("Problems found:")
        for p in problems:
            print(f"  ✗ {p}")
        sys.exit(1)
    print(f"OK — Keel workspace healthy at {kd}")


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="keel", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_init)

    s = sub.add_parser("new-slice"); s.add_argument("title"); s.add_argument("--epic", default=None)
    s.set_defaults(fn=cmd_new_slice)

    s = sub.add_parser("ls"); s.add_argument("--status", default=None); s.set_defaults(fn=cmd_ls)
    s = sub.add_parser("show"); s.add_argument("id"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("next"); s.set_defaults(fn=cmd_next)

    s = sub.add_parser("set-status"); s.add_argument("id"); s.add_argument("status")
    s.add_argument("--note", default=""); s.add_argument("--by", default="human",
                                                         choices=["human", "builder", "verifier", "planner", "reviewer"])
    s.set_defaults(fn=cmd_set_status)

    s = sub.add_parser("gate"); s.add_argument("id"); s.set_defaults(fn=cmd_gate)
    s = sub.add_parser("check"); s.add_argument("id"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("doctor"); s.set_defaults(fn=cmd_doctor)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
