#!/usr/bin/env python3
"""
kuru — deterministic state + gate engine for the Kurukuru enterprise harness.

This is the only thing in the harness that is allowed to mutate machine state.
Agents (builder, verifier, planner) reason and write narrative artifacts; the
*facts* — what slice exists, what status it is in, whether the gates passed —
live in JSON managed here so they cannot be hand-waved.

Zero third-party dependencies: Python 3 stdlib only (ships on macOS/Linux).

Usage:
  kuru init [--stack T] [--profile FILE]   scaffold .kuru/ in the current repo
  kuru set-stack <tool>             rewrite config.json gates from a build-tool preset
  kuru new-slice "<title>" [--epic E]
  kuru ls [--status S]              list slices (table)
  kuru show <id>                    show one slice (paths + state + history)
  kuru next                         print the next actionable slice
  kuru set-status <id> <status> [--note "..."] [--by agent|human]
  kuru gate <id>                    run the configured deterministic gates
  kuru check <id>                   print whether <id> may advance to 'verified'
  kuru doctor                       sanity-check the .kuru workspace

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
import threading
from collections import deque
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
# gate, never the builder. kuru enforces the gate facts; honesty about --by is a
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
    """Walk up looking for a .kuru directory."""
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / ".kuru").is_dir():
            return cand
    return None


def kuru_dir() -> Path:
    root = find_root()
    if root is None:
        die("no .kuru workspace found. Run `kuru init` in your repo root first.")
    return root / ".kuru"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def die(msg: str, code: int = 1):
    print(f"kuru: error: {msg}", file=sys.stderr)
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
    return kuru_dir() / "ledger.json"


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


# Which /kuru:* step advances a slice in a given status (used by `next` and the
# external runner to decide what to dispatch).
STATUS_ACTION = {
    "verifying": "verify",   # a verification was claimed but not finished -> re-verify
    "built": "verify",
    "rejected": "build",
    "in_progress": "build",
    "ready": "build",
    "verified": "review",
    "draft": "slice",        # needs a human to slice/contract it
}


def deps_of(s: dict) -> list[str]:
    return [d.upper() for d in (s.get("depends_on") or [])]


def done_ids(led: dict) -> set[str]:
    return {s["id"] for s in led["slices"] if s["status"] == "done"}


def unmet_deps(led: dict, s: dict) -> list[str]:
    done = done_ids(led)
    return [d for d in deps_of(s) if d not in done]


def pick_next(led: dict) -> dict | None:
    """The next actionable slice in pipeline order, skipping `ready` slices whose
    dependencies aren't `done` yet."""
    order = ["verifying", "built", "rejected", "in_progress", "ready", "verified", "draft"]
    for st in order:
        cands = [s for s in led["slices"] if s["status"] == st]
        if st == "ready":
            cands = [s for s in cands if not unmet_deps(led, s)]
        if cands:
            return cands[0]
    return None


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_init(args):
    root = Path.cwd()
    kd = root / ".kuru"
    if kd.exists() and not args.force:
        die(f"{kd} already exists (use --force to re-scaffold missing files)")
    for sub in ("", "slices", "prd"):
        (kd / sub).mkdir(parents=True, exist_ok=True)

    # Optional reusable environment profile (kept OUTSIDE the plugin by the user).
    profile = None
    if args.profile:
        try:
            profile = json.loads(Path(args.profile).read_text())
        except Exception as e:
            die(f"could not read --profile {args.profile}: {e}")

    # Resolve config.json: explicit profile.config > profile.stack/--stack preset > node default.
    if profile and isinstance(profile.get("config"), dict):
        cfg = dict(profile["config"])
        cfg.setdefault("project", root.name)
        config_text = render(json.dumps(cfg, indent=2) + "\n", PROJECT=root.name)
    else:
        stack = (profile or {}).get("stack") or args.stack
        config_src = f"config.{stack}.json" if stack else "config.json"
        config_text = render(read_template(config_src), PROJECT=root.name)

    seed = {
        "config.json": config_text,
        "ledger.json": json.dumps(
            {"meta": {"project": root.name, "created": now()}, "slices": []}, indent=2
        ) + "\n",
        "charter.md": render(read_template("charter.md"), DATE=now(), PROJECT=root.name),
        "progress.md": render(read_template("progress.md"), DATE=now(), PROJECT=root.name),
        "README.md": read_template("workspace-readme.md"),
        "init.sh": render(read_template("init.sh"), PROJECT=root.name),
    }
    if profile is not None:
        # Persist the profile so /kuru:charter can pre-fill from it.
        seed["profile.json"] = json.dumps(profile, indent=2) + "\n"
    for name, content in seed.items():
        path = kd / name
        if path.exists() and not args.force:
            continue
        path.write_text(content)
        if name == "init.sh":
            path.chmod(0o755)

    # Record where THIS engine lives, captured now while we know our own path.
    # Commands fall back to this when ${CLAUDE_PLUGIN_ROOT}/${KURU_PY} aren't set.
    engine = Path(__file__).resolve()
    (kd / "engine").write_text(str(engine) + "\n")

    stack = (profile or {}).get("stack") or args.stack
    print(f"Initialized Kurukuru workspace at {kd}"
          + (f" (stack: {stack})" if stack else "")
          + (" (profile loaded)" if profile is not None else ""))
    print(f"Engine recorded at {kd / 'engine'}.")
    print(f"Tip: for robust command resolution set  KURU_PY={engine}  in the "
          "kurukuru plugin's env (Claude Code plugin settings).")
    print("Next: run /kuru:charter (it will use .kuru/profile.json if present), "
          "or edit .kuru/config.json gates.")


def cmd_set_stack(args):
    """Rewrite .kuru/config.json from a build-tool preset (templates/config.<stack>.json),
    preserving the project name. Use after `init` to match this repo's pipeline; the
    charter step calls this, then tailors the gate commands."""
    kd = kuru_dir()
    project = kd.parent.name
    try:
        led = load_ledger()
        project = led.get("meta", {}).get("project") or project
    except Exception:
        pass
    content = render(read_template(f"config.{args.stack}.json"), PROJECT=project)
    (kd / "config.json").write_text(content)
    print(f"Rewrote {kd / 'config.json'} from config.{args.stack}.json preset.")
    print("Now tailor the gate commands (task/script names, versions, air-gapped flags),")
    print("then run `kuru doctor`.")


def cmd_new_slice(args):
    led = load_ledger()
    sid = next_id(led)
    kd = kuru_dir()
    sdir = kd / "slices" / sid
    sdir.mkdir(parents=True, exist_ok=False)

    fields = dict(ID=sid, TITLE=args.title, DATE=now(), EPIC=args.epic or "—")
    (sdir / "slice.md").write_text(render(read_template("slice.md"), **fields))
    (sdir / "contract.yml").write_text(render(read_template("contract.yml"), **fields))
    (sdir / "build-log.md").write_text(render(read_template("build-log.md"), **fields))
    (sdir / "verification.md").write_text(render(read_template("verification.md"), **fields))

    deps = []
    if getattr(args, "depends_on", None):
        deps = [d.strip().upper() for d in args.depends_on.split(",") if d.strip()]
    led["slices"].append({
        "id": sid,
        "title": args.title,
        "epic": args.epic,
        "status": "draft",
        "depends_on": deps,
        "created": now(),
        "updated": now(),
        "history": [{"at": now(), "status": "draft", "by": "human", "note": "created"}],
    })
    save_ledger(led)
    print(f"Created {sid}: {args.title}")
    print(f"  dir: {sdir}")
    print("  Fill in slice.md + contract.yml, then `kuru set-status %s ready`." % sid)


def cmd_ls(args):
    led = load_ledger()
    rows = led["slices"]
    if args.status:
        rows = [s for s in rows if s["status"] == args.status]
    if getattr(args, "json", False):
        print(json.dumps(rows))
        return
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
    sdir = kuru_dir() / "slices" / s["id"]
    artfiles = ("slice.md", "contract.yml", "build-log.md", "verification.md", "gate-results.json")
    if getattr(args, "json", False):
        out = dict(s)
        out["artifacts"] = {f: (sdir / f).exists() for f in artfiles}
        out["gate"] = _latest_gate(s["id"])
        out["rejections"] = sum(1 for h in s.get("history", []) if h.get("status") == "rejected")
        print(json.dumps(out, indent=2))
        return
    print(json.dumps(s, indent=2))
    print("\nartifacts:")
    for f in artfiles:
        p = sdir / f
        print(f"  {'✓' if p.exists() else '·'} {p}")


def cmd_next(args):
    """The next thing a human/agent should pick up, in pipeline order."""
    led = load_ledger()
    label = {
        "verifying": "needs a verifier",
        "built": "ready to verify",
        "rejected": "verifier rejected — back to builder",
        "in_progress": "build in progress",
        "ready": "ready to build",
        "verified": "ready for code review",
        "draft": "needs a contract before it can be built",
    }
    s = pick_next(led)
    use_json = getattr(args, "json", False)

    if s is None:
        waiting = [(x["id"], unmet_deps(led, x)) for x in led["slices"]
                   if x["status"] == "ready" and unmet_deps(led, x)]
        blocked = [x["id"] for x in led["slices"] if x["status"] == "blocked"]
        reason = "waiting_on_deps" if waiting else "blocked_present" if blocked else "all_done"
        if use_json:
            print(json.dumps({
                "next_action": "none", "id": None, "status": None, "reason": reason,
                "waiting": [{"id": i, "unmet": u} for i, u in waiting],
                "blocked": blocked,
            }))
            return
        if waiting:
            print("No actionable slices right now — waiting on dependencies:")
            for i, u in waiting:
                print(f"  {i} blocked by {', '.join(u)}")
        elif blocked:
            print("No actionable slices. Blocked slices need a human:")
            for i in blocked:
                print(f"  {i}")
        else:
            print("No actionable slices. Everything is done, or run /kuru:slice.")
        return

    action = STATUS_ACTION[s["status"]]
    if use_json:
        print(json.dumps({
            "next_action": action, "id": s["id"], "status": s["status"],
            "title": s["title"], "epic": s.get("epic"), "depends_on": deps_of(s),
            "label": label[s["status"]],
            "dir": str(kuru_dir() / "slices" / s["id"]),
        }))
        return
    print(f"{s['id']}  [{s['status']}]  {s['title']}")
    print(f"  -> {label[s['status']]}")
    print(f"  -> {kuru_dir() / 'slices' / s['id']}")


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
            die(f"cannot mark {s['id']} verified: no gate run found. Run `kuru gate {s['id']}` first.")
        if not gr["passed"]:
            die(f"cannot mark {s['id']} verified: last gate run FAILED "
                f"({gr['summary']}). Fix and re-run `kuru gate {s['id']}`.")
    if new in {"verified", "reviewed"} and args.by == "builder":
        die(f"a builder may not set '{new}'. This requires the verifier/reviewer (--by human|verifier).")

    # Hard gate: cannot START a build (ready -> in_progress) while dependencies
    # are unfinished. Resuming (rejected/done -> in_progress) is unaffected.
    if new == "in_progress" and cur == "ready":
        unmet = unmet_deps(led, s)
        if unmet:
            die(f"cannot start {s['id']}: unmet dependencies {', '.join(unmet)} "
                f"(each must be 'done' first).")

    s["status"] = new
    s["updated"] = now()
    s["history"].append({"at": now(), "status": new, "by": args.by, "note": args.note or ""})
    save_ledger(led)
    print(f"{s['id']}: {cur} -> {new}")


def _config() -> dict:
    return load_json(kuru_dir() / "config.json")


def _latest_gate(sid: str) -> dict | None:
    p = kuru_dir() / "slices" / sid.upper() / "gate-results.json"
    if not p.exists():
        return None
    return load_json(p)


def _run_one_gate(cmd: str, timeout: int, cwd, logp: Path) -> tuple[int, list[str]]:
    """Run one gate. Stream its combined output live to stdout AND to `logp` (so a
    long build can be watched with `tail -f`), enforce a hard timeout via a watchdog
    (so a silent hang is still killed), and return (exit_code, last_lines)."""
    tail: deque[str] = deque(maxlen=40)
    killed = {"v": False}
    proc = subprocess.Popen(
        cmd, shell=True, cwd=cwd, text=True, bufsize=1,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    def _kill():
        killed["v"] = True
        try:
            proc.kill()
        except Exception:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.start()
    try:
        with open(logp, "w") as lf:
            for line in proc.stdout:  # type: ignore[union-attr]
                sys.stdout.write(line)
                sys.stdout.flush()
                lf.write(line)
                lf.flush()
                tail.append(line.rstrip("\n"))
    finally:
        proc.wait()
        timer.cancel()
    if killed["v"]:
        tail.append(f"TIMEOUT after {timeout}s — process killed")
        return 124, list(tail)
    return proc.returncode, list(tail)


def cmd_gate(args):
    led = load_ledger()
    s = get_slice(led, args.id)
    if not s:
        die(f"no slice {args.id}")
    cfg = _config()
    gates = cfg.get("gates", {})
    if not gates:
        die("no gates configured in .kuru/config.json")

    sdir = kuru_dir() / "slices" / s["id"]
    root = find_root()
    results = []
    overall = True
    print(f"Running gates for {s['id']} (cwd: {root}) ...\n")
    for name, spec in gates.items():
        cmd = spec["cmd"]
        required = spec.get("required", True)
        timeout = spec.get("timeout", 1800)
        logp = sdir / f"gate-{name}.log"
        print(f"  ▶ {name}: {cmd}")
        print(f"    live log: {logp}  ·  watch with:  tail -f {logp}")
        code, tail = _run_one_gate(cmd, timeout, root, logp)
        ok = code == 0
        if required and not ok:
            overall = False
        status = "PASS" if ok else ("FAIL" if required else "WARN")
        print(f"    {status} (exit {code})\n")
        results.append({
            "name": name, "cmd": cmd, "required": required,
            "exit_code": code, "passed": ok,
            "log": str(logp), "output_tail": tail,
        })

    summary = ", ".join(f"{r['name']}={'ok' if r['passed'] else 'x'}" for r in results)
    record = {
        "slice": s["id"], "ran_at": now(), "passed": overall,
        "summary": summary, "gates": results,
    }
    save_json(kuru_dir() / "slices" / s["id"] / "gate-results.json", record)
    print(f"GATE {'PASS' if overall else 'FAIL'} — {summary}")
    print(f"recorded -> {kuru_dir() / 'slices' / s['id'] / 'gate-results.json'}")
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
    contract = kuru_dir() / "slices" / s["id"] / "contract.yml"
    print(f"contract:{' present' if contract.exists() else ' MISSING'}")
    print("\nReminder: green gates are necessary, not sufficient. The verifier must still")
    print("cite concrete evidence for every acceptance criterion in contract.yml.")
    sys.exit(0 if gr["passed"] else 1)


def cmd_doctor(args):
    root = find_root()
    if not root:
        die("no .kuru workspace found here.")
    kd = root / ".kuru"
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
        led = load_json(kd / "ledger.json")
        ids = {s["id"] for s in led.get("slices", [])}
        for s in led.get("slices", []):
            for d in deps_of(s):
                if d not in ids:
                    problems.append(f"{s['id']} depends on unknown slice {d}")
    except Exception as e:
        problems.append(f"ledger.json invalid: {e}")
    if problems:
        print("Problems found:")
        for p in problems:
            print(f"  ✗ {p}")
        sys.exit(1)
    print(f"OK — Kurukuru workspace healthy at {kd}")


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kuru", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--force", action="store_true")
    s.add_argument("--stack", default=None,
                   help="seed a stack-specific config (reads templates/config.<stack>.json, e.g. node|pnpm|gradle|maven|go|python|cargo)")
    s.add_argument("--profile", default=None,
                   help="path to a reusable environment profile (JSON): {stack?, config?, environment?}. "
                        "Saved to .kuru/profile.json; /kuru:charter pre-fills from it.")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("set-stack"); s.add_argument("stack",
        help="build-tool preset: node|pnpm|gradle|maven|go|python|cargo (or any config.<stack>.json)")
    s.set_defaults(fn=cmd_set_stack)

    s = sub.add_parser("new-slice"); s.add_argument("title"); s.add_argument("--epic", default=None)
    s.add_argument("--depends-on", dest="depends_on", default=None,
                   help="comma-separated slice ids this slice depends on, e.g. SL-0001,SL-0002")
    s.set_defaults(fn=cmd_new_slice)

    s = sub.add_parser("ls"); s.add_argument("--status", default=None)
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_ls)
    s = sub.add_parser("show"); s.add_argument("id")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("next"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_next)

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
