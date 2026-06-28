#!/usr/bin/env python3
"""
kuru — deterministic state + gate engine for the Kurukuru enterprise harness.

This is the only thing in the harness that is allowed to mutate machine state.
Agents (builder, verifier, planner) reason and write narrative artifacts; the
*facts* — what slice exists, what status it is in, whether the gates passed —
live in JSON managed here so they cannot be hand-waved.

Zero third-party dependencies: Python 3 stdlib only (ships on macOS/Linux).

Usage:
  kuru init [--stack T] [--profile DIR|URL] [--reuse-check off|warn|block]  scaffold .kuru/
  kuru set-stack <tool> [--target N [--discard-flat-gates|--migrate-flat-gates-to NAME]]
                                    rewrite config gates from a preset (or seed one target)
  kuru new-slice "<title>" [--epic E] [--target N]
  kuru set-target <id> <target>     assign a slice to a config.json gate target
  kuru ls [--status S]              list slices (table)
  kuru show <id>                    show one slice (paths + state + history)
  kuru next                         print the next actionable slice
  kuru set-status <id> <status> [--note "..."] [--by agent|human] [--no-commit]
                                    (-> done auto-commits the working tree; --no-commit skips it)
  kuru gate <id>                    run the configured deterministic gates
  kuru check <id>                   print whether <id> may advance to 'verified'
  kuru doctor                       sanity-check the .kuru workspace

Statuses (the slice state machine):
  draft -> ready -> in_progress -> built -> verifying -> verified -> done
  verified -> reviewed -> done            (opt-in code review; /kuru:review)
  any -> blocked ;  verifying -> rejected -> in_progress
  any (except done) -> dropped -> draft   (retire a slice; resurrect to re-write it)
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

STATUSES = [
    "draft", "ready", "in_progress", "built",
    "verifying", "verified", "rejected", "reviewed", "done", "blocked", "dropped",
]

# Allowed transitions. 'blocked' is reachable from anywhere and exits back to
# wherever the human/agent decides, so it is handled separately. 'dropped'
# retires a slice (wrong scope, superseded); everything except shipped (`done`)
# work can be dropped, and a dropped slice can only be resurrected to `draft`
# (same id, so dependents stay valid) for a re-write.
TRANSITIONS = {
    "draft": {"ready", "blocked", "dropped"},
    "ready": {"in_progress", "draft", "blocked", "dropped"},
    "in_progress": {"built", "blocked", "dropped"},
    "built": {"verifying", "in_progress", "blocked", "dropped"},
    "verifying": {"verified", "rejected", "blocked", "dropped"},
    "rejected": {"in_progress", "blocked", "dropped"},
    # Code review is opt-in: a verified slice may ship straight to `done`, or take
    # the `reviewed` detour via /kuru:review when a slice warrants a closer look.
    "verified": {"done", "reviewed", "rejected", "blocked", "dropped"},
    "reviewed": {"done", "in_progress", "blocked", "dropped"},
    "done": {"in_progress"},  # reopen; shipped work cannot be dropped
    "blocked": set(STATUSES),  # unblock to anywhere
    "dropped": {"draft"},  # resurrect to re-write the slice
}

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
    # Atomic: a crash mid-write must never corrupt machine truth (ledger.json).
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


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


@contextlib.contextmanager
def ledger_lock(timeout: float = 60.0):
    """Serialize ledger read-modify-write across concurrent kuru.py processes.

    `set-status` is load -> mutate -> save (plus, for `done`, a git commit); two of
    them racing would lose a write or interleave commits. `/kuru:loop-workflow` runs
    several builders/verifiers at once, each calling `set-status`, so the mutation
    must be serialized. This is an advisory file lock (stdlib only). On platforms
    without fcntl (Windows) it degrades to a no-op — kurukuru targets Unix shells.
    Reads (`next`, `ls`, `show`) don't take the lock: `save_json` swaps the file in
    atomically with `os.replace`, so a reader always sees a complete ledger."""
    lock_path = kuru_dir() / ".ledger.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        fcntl = None
    fh = open(lock_path, "w")
    try:
        if fcntl is not None:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        die("could not acquire .kuru/.ledger.lock within "
                            f"{timeout:.0f}s — another kuru process is holding it.")
                    time.sleep(0.05)
        yield
    finally:
        fh.close()


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
    # Code review is opt-in (run /kuru:review by hand on a verified slice). The
    # default pipeline ships a verified — or already-reviewed — slice straight to
    # `done`. The ship transition is `set-status <id> done`; /kuru:loop runs it
    # inline, while /kuru:ship (and /kuru:loop-workflow) wrap it as a /kuru:* verb.
    "verified": "ship",
    "reviewed": "ship",
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
    order = ["verifying", "built", "rejected", "in_progress", "ready", "verified", "reviewed", "draft"]
    for st in order:
        cands = [s for s in led["slices"] if s["status"] == st]
        if st == "ready":
            cands = [s for s in cands if not unmet_deps(led, s)]
        if cands:
            return cands[0]
    return None


# --------------------------------------------------------------------------- #
# profile catalog (--profile)
# --------------------------------------------------------------------------- #
# `kuru init --profile <LOCATION>` loads a *catalog* of reusable single-stack
# environment profiles from ONE place — instead of repeating the flag per file.
# LOCATION may be: a local directory (every *.json in it), a single .json file,
# or an http(s) URL to a GitHub *contents* / GitLab *repository tree* listing (we
# list the directory, then fetch each *.json blob). All resolve to a list of
# (name, data) pairs that init stashes under .kuru/profiles/ for /kuru:charter.
def _http_get(url: str, headers: dict | None = None, timeout: int = 30) -> str:
    import urllib.request, urllib.error
    req = urllib.request.Request(
        url, headers={"User-Agent": "kurukuru-init", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        die(f"--profile fetch failed ({e.code} {e.reason}) for {url}")
    except Exception as e:
        die(f"--profile fetch failed for {url}: {e}")


def _load_catalog_url(url: str) -> list[tuple[str, dict]]:
    """Resolve an http(s) profile-catalog URL via a Git provider's tree/contents
    API. Private repos: set GITHUB_TOKEN / GITLAB_TOKEN to authenticate."""
    import urllib.parse as up
    parsed = up.urlparse(url)
    host = parsed.netloc.lower()

    # GitHub: api.github.com/repos/OWNER/REPO/contents/DIR[?ref=BRANCH]
    if host == "api.github.com" and "/contents/" in parsed.path:
        headers = {"Accept": "application/vnd.github+json"}
        tok = os.environ.get("GITHUB_TOKEN")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        listing = json.loads(_http_get(url, headers))
        if not isinstance(listing, list):
            die(f"--profile {url}: expected a directory listing (got a file?)")
        out = []
        for e in listing:
            if e.get("type") == "file" and e.get("name", "").endswith(".json"):
                raw = _http_get(e["download_url"], headers)
                out.append((Path(e["name"]).stem, json.loads(raw)))
        if not out:
            die(f"--profile {url}: no *.json profiles in that listing")
        return out

    # GitLab: HOST/api/v4/projects/ID/repository/tree?path=DIR[&ref=BRANCH]
    if "/api/v4/projects/" in parsed.path and "/repository/tree" in parsed.path:
        headers = {}
        tok = os.environ.get("GITLAB_TOKEN")
        if tok:
            headers["PRIVATE-TOKEN"] = tok
        m = re.search(r"/projects/([^/]+)/repository/tree", parsed.path)
        if not m:
            die(f"--profile {url}: could not parse the GitLab project id")
        pid = m.group(1)
        base = f"{parsed.scheme}://{parsed.netloc}/api/v4/projects/{pid}"
        ref = (up.parse_qs(parsed.query).get("ref") or [None])[0]
        if not ref:  # no ref given — ask the API for the default branch
            ref = json.loads(_http_get(base, headers)).get("default_branch") or "main"
        listing = json.loads(_http_get(url, headers))
        if not isinstance(listing, list):
            die(f"--profile {url}: expected a directory listing (got a file?)")
        out = []
        for e in listing:
            if e.get("type") == "blob" and e.get("name", "").endswith(".json"):
                fp = up.quote(e["path"], safe="")
                raw = _http_get(
                    f"{base}/repository/files/{fp}/raw?ref={up.quote(ref)}", headers)
                out.append((Path(e["name"]).stem, json.loads(raw)))
        if not out:
            die(f"--profile {url}: no *.json profiles in that listing")
        return out

    die(f"--profile {url}: unrecognized catalog URL. Use a GitHub contents API "
        "(https://api.github.com/repos/OWNER/REPO/contents/DIR) or a GitLab tree "
        "API (https://HOST/api/v4/projects/ID/repository/tree?path=DIR) URL.")


def load_profile_catalog(location: str) -> list[tuple[str, dict]]:
    """Normalize a --profile LOCATION (dir | .json file | http(s) catalog URL)
    into a sorted list of (name, profile-dict) pairs."""
    if re.match(r"^https?://", location, re.I):
        return _load_catalog_url(location)
    p = Path(location)
    if p.is_dir():
        files = sorted(p.glob("*.json"))
        if not files:
            die(f"--profile {location}: no *.json profiles in that directory")
        out = []
        for f in files:
            try:
                out.append((f.stem, json.loads(f.read_text())))
            except Exception as e:
                die(f"could not read profile {f}: {e}")
        return out
    if p.is_file():
        try:
            return [(p.stem or "profile", json.loads(p.read_text()))]
        except Exception as e:
            die(f"could not read --profile {location}: {e}")
    die(f"--profile {location}: not a directory, .json file, or http(s) URL")


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

    # Optional reusable environment profiles (kept OUTSIDE the plugin by the user).
    # `--profile <LOCATION>` points at a *catalog* — a directory of single-stack
    # profile JSONs, a single .json file, or a Git tree/contents URL (see
    # load_profile_catalog). /kuru:charter then picks the ones that match the apps
    # it discovers in this repo, assigns each a gate target + dir, and folds its
    # environment/conventions in.
    profiles = load_profile_catalog(args.profile) if args.profile else []

    # Seed a sane STARTING config so `doctor` passes before the charter runs. With a
    # SINGLE profile we seed its stack preset (a nice default); with several (or none)
    # we seed the --stack preset or the node default. A profile's
    # config/environment/conventions — and, for a polyglot repo, the choice of which
    # profiles apply and to which app/dir — are guidance for /kuru:charter, never
    # applied at init: the charter writes the authoritative config.json (a flat gate
    # set, or a multi-app `targets` map) + charter.
    stack = (profiles[0][1].get("stack") if len(profiles) == 1 else None) or args.stack
    config_src = f"config.{stack}.json" if stack else "config.json"
    config_text = render(read_template(config_src), PROJECT=root.name)

    # Optional dupehound duplicate-code gate. A repo-wide gate (the builder runs
    # `kuru gate`, which reads config.json), so it's seeded here at init. `warn` is
    # advisory (required:false -> WARN, never blocks); `block` is enforcing. A failing
    # `block` run can still be moved past with `kuru gate --waive reuse=...`.
    if args.reuse_check != "off":
        if shutil.which("dupehound") is None:
            print("  ! dupehound not found on PATH — the 'reuse' gate is seeded but will "
                  "fail until you install it: https://github.com/Rafaelpta/dupehound")
        cfg = json.loads(config_text)
        cfg.setdefault("gates", {})["reuse"] = {
            "cmd": "dupehound check",
            "required": args.reuse_check == "block",
            "timeout": 600,
        }
        config_text = json.dumps(cfg, indent=2) + "\n"

    seed = {
        "config.json": config_text,
        "ledger.json": json.dumps(
            {"meta": {"project": root.name, "created": now()}, "slices": []}, indent=2
        ) + "\n",
        "charter.md": render(read_template("charter.md"), DATE=now(), PROJECT=root.name),
        "progress.md": render(read_template("progress.md"), DATE=now(), PROJECT=root.name),
        "README.md": read_template("workspace-readme.md"),
        "init.sh": render(read_template("init.sh"), PROJECT=root.name),
        # .kuru/ is meant to be committed (it's the project's delivery memory);
        # this excludes only the machine-local bits.
        ".gitignore": "# machine-local — do not commit\nengine\n.ledger.lock\nslices/*/gate-*.log\n",
    }
    for name, content in seed.items():
        path = kd / name
        if path.exists() and not args.force:
            continue
        path.write_text(content)
        if name == "init.sh":
            path.chmod(0o755)

    # Stash each profile under .kuru/profiles/ (committed guidance) for the charter
    # to read. Name files by their catalog name (file stem / blob name), de-duplicated.
    if profiles:
        pdir = kd / "profiles"
        pdir.mkdir(exist_ok=True)
        used: set[str] = set()
        for stem, data in profiles:
            stem = stem or "profile"
            name, i = stem, 2
            while name in used:
                name, i = f"{stem}-{i}", i + 1
            used.add(name)
            (pdir / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")

    # Record where THIS engine lives, captured now while we know our own path.
    # A last-resort pointer for humans/agents when KURU_PY/CLAUDE_PLUGIN_ROOT
    # aren't set (run: python3 "$(cat .kuru/engine)" <cmd>) — the inline command
    # snippets do NOT read it automatically. Machine-local; gitignored above.
    engine = Path(__file__).resolve()
    (kd / "engine").write_text(str(engine) + "\n")

    print(f"Initialized Kurukuru workspace at {kd}"
          + (f" (stack: {stack})" if stack else "")
          + (f" ({len(profiles)} profile{'s' if len(profiles) != 1 else ''} loaded)" if profiles else ""))
    print(f"Engine recorded at {kd / 'engine'}.")
    print(f"Tip: for robust command resolution set  KURU_PY={engine}  in the "
          "kurukuru plugin's env (Claude Code plugin settings).")
    print("Next: run /kuru:charter (it reads .kuru/profiles/ as guidance if "
          "present, confirms it with you, then writes config.json), or edit "
          ".kuru/config.json gates by hand.")


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
    rendered = render(read_template(f"config.{args.stack}.json"), PROJECT=project)
    target = getattr(args, "target", None)
    if target:
        # Multi-app repo: seed/replace ONE target's gates, preserving the rest of
        # config.json (other targets, project, etc.). The preset is parsed only for
        # its `gates`; the target keeps (or defaults) its own working `dir`.
        preset = json.loads(rendered)
        try:
            cfg = load_json(kd / "config.json")
        except Exception:
            cfg = {"project": project}

        # Adding a target converts this repo to multi-app, where the engine ignores the
        # flat top-level `gates` (and `doctor` rejects having both). If a single-app
        # `gates` exists — e.g. left by `init`, possibly already tailored — we will NOT
        # silently throw it away. The caller must say what happens to it:
        #   --discard-flat-gates        drop it (it was just boilerplate)
        #   --migrate-flat-gates-to N   keep it as its own app, target N (dir ".")
        flat = cfg.get("gates")
        migrate_to = getattr(args, "migrate_flat_gates_to", None)
        discard = getattr(args, "discard_flat_gates", False)
        migrated = None
        if flat is not None:
            if migrate_to and discard:
                die("pass only one of --migrate-flat-gates-to / --discard-flat-gates.")
            if not migrate_to and not discard:
                die(f"config.json has a single-app top-level `gates`; adding `--target "
                    f"{target}` makes this a multi-app repo, where that top-level `gates` "
                    f"is ignored. Decide what happens to the existing config — re-run with "
                    f"ONE of:\n"
                    f"  • keep it as its own app:  set-stack {args.stack} --target {target} "
                    f"--migrate-flat-gates-to <NAME>\n"
                    f"  • discard it (just the init default):  set-stack {args.stack} "
                    f"--target {target} --discard-flat-gates")
            if migrate_to:
                if migrate_to == target:
                    die(f"--migrate-flat-gates-to {migrate_to} collides with the --target "
                        f"{target} you're adding; name the existing app something else.")
                cfg.setdefault("targets", {})[migrate_to] = {"dir": ".", "gates": flat}
                migrated = migrate_to
            cfg.pop("gates", None)   # obsolete now (migrated into a target or discarded)

        tmap = cfg.setdefault("targets", {})
        existing_dir = tmap.get(target, {}).get("dir", ".")
        tmap[target] = {"dir": existing_dir, "gates": preset.get("gates", {})}
        save_json(kd / "config.json", cfg)
        print(f"Set target '{target}' gates from the config.{args.stack}.json preset "
              f"(dir: {existing_dir}).")
        if migrated:
            print(f"Kept the previous single-app `gates` as target '{migrated}' (dir: '.') — "
                  f"set its real `dir` and tailor it.")
        elif flat is not None and discard:
            print("Discarded the previous single-app top-level `gates`.")
        print(f"Now set this target's `dir` (where the app lives) and tailor its gate commands,")
        print("then run `kuru doctor`.")
        return
    (kd / "config.json").write_text(rendered)
    print(f"Rewrote {kd / 'config.json'} from config.{args.stack}.json preset.")
    print("Now tailor the gate commands (task/script names, versions, air-gapped flags),")
    print("then run `kuru doctor`. For a multi-app repo, use `set-stack <tool> --target <name>`")
    print("once per app instead, so each gets its own dir + gates.")


def cmd_new_slice(args):
    led = load_ledger()
    # Validate everything that can fail BEFORE touching the filesystem, so a bad
    # invocation never leaves an orphan slice dir that collides with the next id.
    target = getattr(args, "target", None)
    if target:
        targets = _targets(_config())
        if target not in targets:
            die(f"unknown target '{target}'. config.json targets: {', '.join(targets)}. "
                f"(Define targets in the charter step, or omit --target for a single-app repo.)")
    deps = []
    if getattr(args, "depends_on", None):
        deps = [d.strip().upper() for d in args.depends_on.split(",") if d.strip()]

    sid = next_id(led)
    kd = kuru_dir()
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
        "depends_on": deps,
        "target": target,
        "created": now(),
        "updated": now(),
        "history": [{"at": now(), "status": "draft", "by": "human", "note": "created"}],
    })
    save_ledger(led)
    print(f"Created {sid}: {args.title}")
    print(f"  dir: {sdir}")
    if target:
        print(f"  target: {target}")
    print("  Fill in slice.md + contract.yml, then `kuru set-status %s ready`." % sid)


def cmd_set_target(args):
    led = load_ledger()
    s = get_slice(led, args.id)
    if not s:
        die(f"no slice {args.id}")
    targets = _targets(_config())
    if args.target not in targets:
        die(f"unknown target '{args.target}'. config.json targets: {', '.join(targets)}.")
    s["target"] = args.target
    s["updated"] = now()
    save_ledger(led)
    print(f"{s['id']}: target -> {args.target}")


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
    show_target = any(s.get("target") for s in rows)
    if show_target:
        print(f"{'ID':<8} {'STATUS':<12} {'TARGET':<10} {'EPIC':<10} TITLE")
        print("-" * (8 + 12 + 10 + 10 + min(width, 50) + 4))
        for s in rows:
            print(f"{s['id']:<8} {s['status']:<12} {str(s.get('target') or '—'):<10} "
                  f"{str(s.get('epic') or '—'):<10} {s['title']}")
    else:
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
        "rejected": "rejected — back to builder",
        "in_progress": "build in progress",
        "ready": "ready to build",
        "verified": "ready to ship (code review optional)",
        "reviewed": "reviewed — ready to ship",
        "draft": "needs a contract before it can be built",
    }
    use_json = getattr(args, "json", False)

    def emit_action(s):
        action = STATUS_ACTION[s["status"]]
        if use_json:
            print(json.dumps({
                "next_action": action, "id": s["id"], "status": s["status"],
                "title": s["title"], "epic": s.get("epic"), "target": s.get("target"),
                "depends_on": deps_of(s), "label": label[s["status"]],
                "dir": str(kuru_dir() / "slices" / s["id"]),
            }))
            return
        tgt = f"  (target: {s['target']})" if s.get("target") else ""
        print(f"{s['id']}  [{s['status']}]{tgt}  {s['title']}")
        print(f"  -> {label[s['status']]}")
        print(f"  -> {kuru_dir() / 'slices' / s['id']}")

    # Single-slice query: the action for one named slice (for single-slice loops),
    # so the caller never has to consult the board's pick and risk a sibling.
    sid = getattr(args, "slice", None)
    if sid:
        s = get_slice(led, sid)
        if s is None:
            die(f"no slice {sid}")
        if s["status"] in ("done", "blocked"):
            if use_json:
                print(json.dumps({"next_action": "none", "id": s["id"],
                                  "status": s["status"], "reason": s["status"]}))
            else:
                print(f"{s['id']} is {s['status']} — nothing to loop.")
            return
        if s["status"] == "ready":
            unmet = unmet_deps(led, s)
            if unmet:
                if use_json:
                    print(json.dumps({"next_action": "none", "id": s["id"], "status": "ready",
                                      "reason": "waiting_on_deps",
                                      "waiting": [{"id": s["id"], "unmet": unmet}]}))
                else:
                    print(f"{s['id']} waiting on dependencies: {', '.join(unmet)}")
                return
        emit_action(s)
        return

    # Whole-batch query: every slice actionable *right now* (for a parallel driver
    # like /kuru:loop-workflow), not just the single board pick. Same pipeline order
    # and dependency rule as pick_next, but it returns the full set plus what is
    # waiting/draft/blocked/done so the caller can show a plan before starting.
    if getattr(args, "all", False):
        order = ["verifying", "built", "rejected", "in_progress", "ready", "verified", "reviewed"]
        actionable, waiting = [], []
        for st in order:
            for s in [x for x in led["slices"] if x["status"] == st]:
                if st == "ready":
                    unmet = unmet_deps(led, s)
                    if unmet:
                        waiting.append({"id": s["id"], "title": s["title"], "unmet": unmet})
                        continue
                actionable.append({
                    "next_action": STATUS_ACTION[s["status"]], "id": s["id"],
                    "status": s["status"], "title": s["title"], "epic": s.get("epic"),
                    "target": s.get("target"), "depends_on": deps_of(s),
                    "label": label[s["status"]],
                    "dir": str(kuru_dir() / "slices" / s["id"]),
                })
        draft = [{"id": x["id"], "title": x["title"]} for x in led["slices"] if x["status"] == "draft"]
        blocked = [x["id"] for x in led["slices"] if x["status"] == "blocked"]
        done = [x["id"] for x in led["slices"] if x["status"] == "done"]
        if use_json:
            print(json.dumps({"actionable": actionable, "waiting": waiting,
                              "draft": draft, "blocked": blocked, "done": done}))
            return
        if actionable:
            print(f"Actionable now ({len(actionable)}) — can run in parallel:")
            for a in actionable:
                dep = f"  (deps: {', '.join(a['depends_on'])})" if a["depends_on"] else ""
                print(f"  {a['id']}  [{a['status']}] -> {a['next_action']}{dep}  {a['title']}")
        else:
            print("Nothing actionable right now.")
        if waiting:
            print("Waiting on dependencies (will unlock as deps finish):")
            for w in waiting:
                print(f"  {w['id']} <- {', '.join(w['unmet'])}  {w['title']}")
        if draft:
            print("Draft — need a human to contract (/kuru:slice):")
            for d in draft:
                print(f"  {d['id']}  {d['title']}")
        if blocked:
            print(f"Blocked — need a human: {', '.join(blocked)}")
        return

    s = pick_next(led)

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

    emit_action(s)


def cmd_set_status(args):
    # Hold the ledger lock across the whole load -> mutate -> save (-> commit) so
    # parallel loop-workflow transitions can't clobber each other or interleave
    # auto-commits. die()/sys.exit() raise through the `with`, releasing the lock.
    with ledger_lock():
        _set_status_impl(args)


def _set_status_impl(args):
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
        # Freshness: a gate run recorded before the latest build is stale evidence.
        built_at = max((h["at"] for h in s.get("history", [])
                        if h.get("status") == "built"), default=None)
        if built_at and _dt.datetime.fromisoformat(gr["ran_at"]) < _dt.datetime.fromisoformat(built_at):
            die(f"cannot mark {s['id']} verified: last gate run ({gr['ran_at']}) is stale — "
                f"it predates the latest build ({built_at}). Re-run `kuru gate {s['id']}`.")
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
    if new == "done":
        if getattr(args, "no_commit", False):
            print(f"  (--no-commit: {s['id']} is done in the ledger; commit deferred to the caller)")
        else:
            _commit_slice(s)


def _commit_slice(s: dict) -> None:
    """Commit the working tree when a slice reaches `done`, so each shipped slice
    is one atomic commit — its code, its `.kuru/` artifacts, and the ledger's
    transition to `done` together. Best-effort: it never undoes the state change.
    If the repo isn't a git work tree, there's nothing to commit, or `git commit`
    fails (e.g. no configured identity, a rejecting hook), it warns and moves on —
    the slice is already `done` in the ledger."""
    root = find_root()
    if root is None:
        return

    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)

    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return  # not a git repo — quietly skip
    git("add", "-A")
    if not git("status", "--porcelain").stdout.strip():
        print("  (working tree clean — nothing to commit)")
        return
    msg = f"kuru: ship {s['id']} — {s['title']}"
    r = git("commit", "-m", msg)
    if r.returncode != 0:
        detail = (r.stderr.strip() or r.stdout.strip() or "git commit failed").splitlines()[0]
        print(f"  ! auto-commit skipped ({detail}); {s['id']} is still marked done")
        return
    sha = git("rev-parse", "--short", "HEAD").stdout.strip()
    print(f"  committed {sha}: {msg}")


def _config() -> dict:
    return load_json(kuru_dir() / "config.json")


def _targets(cfg: dict) -> dict:
    """Normalize gate config into {name: {"dir": str, "gates": dict}}.

    A polyglot/monorepo declares a `targets` map in config.json — one entry per
    build flavor (a gradle service, a pnpm web app), each with its own working
    `dir` (relative to the repo root) and `gates`. A single-app repo keeps a flat
    top-level `gates`; that's treated as one implicit target named 'default' rooted
    at the repo ('.'), so existing configs keep working unchanged."""
    tg = cfg.get("targets")
    if tg:
        return {name: {"dir": (spec.get("dir") or "."), "gates": spec.get("gates", {})}
                for name, spec in tg.items()}
    return {"default": {"dir": ".", "gates": cfg.get("gates", {})}}


def _slice_target(s: dict, cfg: dict) -> tuple[str, dict]:
    """Resolve which target's gates a slice runs. Returns (name, {"dir","gates"})."""
    targets = _targets(cfg)
    want = s.get("target")
    if want:
        if want not in targets:
            die(f"slice {s['id']} targets '{want}', which is not in config.json "
                f"(targets: {', '.join(targets) or 'none'}). Fix with "
                f"`kuru set-target {s['id']} <name>`.")
        return want, targets[want]
    if len(targets) == 1:
        name = next(iter(targets))
        return name, targets[name]
    die(f"slice {s['id']} has no target, but config.json defines several "
        f"({', '.join(targets)}). Assign one: `kuru set-target {s['id']} <name>`.")


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
        start_new_session=True,  # own process group, so the watchdog can kill children too
    )

    def _kill():
        killed["v"] = True
        # Kill the whole process group: killing just the shell leaves children
        # (gradle/npm/...) alive holding the stdout pipe, which would wedge the
        # read loop below — the exact silent hang the watchdog exists to stop.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
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
    tname, target = _slice_target(s, cfg)
    gates = target["gates"]
    if not gates:
        die(f"no gates configured for target '{tname}' in .kuru/config.json")

    root = find_root()
    cwd = (root / target["dir"]).resolve()
    if not cwd.is_dir():
        die(f"target '{tname}' dir '{target['dir']}' does not exist under {root}")

    # One-off waivers: --waive NAME[=REASON] lets a FAILING required gate proceed for
    # this run, recording the reason as a fact in gate-results.json (the verifier reads
    # it and may still reject). Not persisted — a later `kuru gate` without --waive fails
    # again, so a waiver can't rot into a silent permanent bypass.
    waivers = {}
    for w in args.waive:
        wname, _, wreason = w.partition("=")
        waivers[wname.strip()] = wreason.strip() or "(no reason given)"

    sdir = kuru_dir() / "slices" / s["id"]
    results = []
    overall = True
    print(f"Running gates for {s['id']} [target: {tname}] (cwd: {cwd}) ...\n")
    for name, spec in gates.items():
        cmd = spec["cmd"]
        required = spec.get("required", True)
        timeout = spec.get("timeout", 1800)
        logp = sdir / f"gate-{name}.log"
        print(f"  ▶ {name}: {cmd}")
        print(f"    live log: {logp}  ·  watch with:  tail -f {logp}")
        code, tail = _run_one_gate(cmd, timeout, cwd, logp)
        ok = code == 0
        waived = (not ok) and required and name in waivers
        if required and not ok and not waived:
            overall = False
        status = "WAIVED" if waived else ("PASS" if ok else ("FAIL" if required else "WARN"))
        if waived:
            print(f"    {status} (exit {code}) — {waivers[name]}\n")
        else:
            print(f"    {status} (exit {code})\n")
        results.append({
            "name": name, "cmd": cmd, "required": required,
            "exit_code": code, "passed": ok,
            "waived": waived, "waive_reason": waivers.get(name) if waived else None,
            "log": str(logp), "output_tail": tail,
        })

    summary = ", ".join(
        f"{r['name']}={'ok' if r['passed'] else ('waived' if r['waived'] else 'x')}"
        for r in results)
    record = {
        "slice": s["id"], "target": tname, "dir": target["dir"],
        "ran_at": now(), "passed": overall,
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
    problems, warnings = [], []
    for f in ("config.json", "ledger.json", "charter.md", "progress.md"):
        if not (kd / f).exists():
            problems.append(f"missing {f}")
    targets, multi = {}, False
    try:
        cfg = load_json(kd / "config.json")
        targets = _targets(cfg)
        multi = bool(cfg.get("targets")) and len(targets) > 1
        for tname, t in targets.items():
            if not t["gates"]:
                if tname == "default" and not cfg.get("targets"):
                    problems.append("config.json has no gates configured")
                else:
                    problems.append(f"target '{tname}' has no gates configured")
            tdir = (root / t["dir"]).resolve()
            if not tdir.is_dir():
                # A target dir that a not-yet-built slice will create is normal early on
                # — warn, don't fail. `kuru gate` still hard-errors if it's missing at
                # build time, when the dir genuinely must exist.
                warnings.append(f"target '{tname}' dir '{t['dir']}' does not exist yet "
                                f"under {root} (a slice may create it)")
        if cfg.get("targets") and cfg.get("gates"):
            problems.append("config.json has both top-level `gates` and `targets`; the "
                            "top-level `gates` is ignored — move it into a target or remove it")
    except Exception as e:
        problems.append(f"config.json invalid: {e}")
    try:
        led = load_json(kd / "ledger.json")
        status_by_id = {s["id"]: s["status"] for s in led.get("slices", [])}
        for s in led.get("slices", []):
            if s["status"] == "dropped":
                continue
            tgt = s.get("target")
            if tgt and tgt not in targets:
                problems.append(f"{s['id']} targets unknown '{tgt}' "
                                f"(config targets: {', '.join(targets) or 'none'})")
            elif not tgt and multi:
                problems.append(f"{s['id']} has no target but config defines several "
                                f"({', '.join(targets)}); set one with `set-target {s['id']} <name>`")
            for d in deps_of(s):
                if d not in status_by_id:
                    problems.append(f"{s['id']} depends on unknown slice {d}")
                elif status_by_id[d] == "dropped":
                    problems.append(f"{s['id']} depends on dropped slice {d} "
                                    f"(resurrect it with `set-status {d} draft`, or re-cut the dependency)")
    except Exception as e:
        problems.append(f"ledger.json invalid: {e}")
    if problems:
        print("Problems found:")
        for p in problems:
            print(f"  ✗ {p}")
        for w in warnings:
            print(f"  ⚠ {w}")
        sys.exit(1)
    print(f"OK — Kurukuru workspace healthy at {kd}")
    for w in warnings:
        print(f"  ⚠ {w}")


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
    s.add_argument("--profile", default=None, metavar="DIR|URL",
                   help="a CATALOG of reusable single-stack environment profiles "
                        "({stack?, config?, environment?, conventions?} JSON) to load: a local "
                        "DIRECTORY of *.json files, a single .json file, or an http(s) URL to a "
                        "GitHub contents / GitLab repository-tree listing (private repos: set "
                        "GITHUB_TOKEN / GITLAB_TOKEN). /kuru:charter matches each to an app. "
                        "Stashed under .kuru/profiles/.")
    s.add_argument("--reuse-check", choices=("off", "warn", "block"), default="off",
                   help="seed a dupehound duplicate-code gate into config.json: "
                        "off=none (default) · warn=advisory (WARN, never blocks) · "
                        "block=required (must be green or --waive'd to verify). "
                        "Needs the `dupehound` binary on PATH at gate time.")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("set-stack"); s.add_argument("stack",
        help="build-tool preset: node|pnpm|gradle|maven|go|python|cargo (or any config.<stack>.json)")
    s.add_argument("--target", default=None,
                   help="multi-app repo: seed/replace just this named target's gates "
                        "(preserving other targets) instead of rewriting the whole config")
    s.add_argument("--migrate-flat-gates-to", default=None, metavar="NAME",
                   help="when --target converts a single-app config to multi-app, keep the "
                        "existing top-level `gates` as a target named NAME (dir '.')")
    s.add_argument("--discard-flat-gates", action="store_true",
                   help="when --target converts a single-app config to multi-app, drop the "
                        "existing top-level `gates` (it was just the init default)")
    s.set_defaults(fn=cmd_set_stack)

    s = sub.add_parser("new-slice"); s.add_argument("title"); s.add_argument("--epic", default=None)
    s.add_argument("--depends-on", dest="depends_on", default=None,
                   help="comma-separated slice ids this slice depends on, e.g. SL-0001,SL-0002")
    s.add_argument("--target", default=None,
                   help="which config.json target's gates this slice runs (multi-app repos)")
    s.set_defaults(fn=cmd_new_slice)

    s = sub.add_parser("set-target"); s.add_argument("id"); s.add_argument("target")
    s.set_defaults(fn=cmd_set_target)

    s = sub.add_parser("ls"); s.add_argument("--status", default=None)
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_ls)
    s = sub.add_parser("show"); s.add_argument("id")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("next"); s.add_argument("--json", action="store_true")
    s.add_argument("--slice", default=None, metavar="ID",
                   help="action for this one slice only (for single-slice loops), "
                        "not the board's next pick.")
    s.add_argument("--all", action="store_true",
                   help="list EVERY slice actionable right now (deps satisfied), not just "
                        "the single next pick — the parallel batch for /kuru:loop-workflow. "
                        "Also reports waiting/draft/blocked/done.")
    s.set_defaults(fn=cmd_next)

    s = sub.add_parser("set-status"); s.add_argument("id"); s.add_argument("status")
    s.add_argument("--note", default=""); s.add_argument("--by", default="human",
                                                         choices=["human", "builder", "verifier", "planner", "reviewer"])
    s.add_argument("--no-commit", dest="no_commit", action="store_true",
                   help="for `done`: flip the ledger only, skip the auto-commit (the caller commits later — "
                        "used by /kuru:loop-workflow, which commits once after the parallel run)")
    s.set_defaults(fn=cmd_set_status)

    s = sub.add_parser("gate"); s.add_argument("id")
    s.add_argument("--waive", action="append", default=[], metavar="NAME[=REASON]",
                   help="treat a failing REQUIRED gate as non-blocking for THIS run, recording "
                        "the reason in gate-results.json (e.g. --waive reuse=\"false positive\"). "
                        "Per-run only — re-running without --waive fails again.")
    s.set_defaults(fn=cmd_gate)
    s = sub.add_parser("check"); s.add_argument("id"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("doctor"); s.set_defaults(fn=cmd_doctor)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
