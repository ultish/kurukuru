"""board CLI — plan + run + status/logs (mock / claude / grok / cmd backends)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from board import __version__
from board.backends.claude import ClaudeBackend, find_claude
from board.backends.cmd import CmdBackend
from board.backends.grok import GrokBackend, find_grok
from board.backends.mock import MockBackend, load_mock_scenarios
from board.cancel import RunControl
from board.events import EventWriter, default_run_dir, new_run_id
from board.ledger import Ledger, resolve_kuru_py
from board.plan import build_plan, format_plan_text
from board.preconditions import check_preconditions
from board.scheduler import RunResult, Scheduler
from board.ui.plain import make_run_ui


def _parse_slices(raw: str | None) -> list[str] | None:
    if not raw or not raw.strip():
        return None
    return [p.strip().upper() for p in raw.split(",") if p.strip()]


def _common_repo_plugin(args: argparse.Namespace) -> tuple[Path, Path, Ledger]:
    repo = Path(args.repo).resolve()
    kuru_py = resolve_kuru_py(Path(args.plugin_dir) if args.plugin_dir else None)
    return repo, kuru_py, Ledger(repo, kuru_py)


def cmd_plan(args: argparse.Namespace) -> int:
    repo, kuru_py, ledger = _common_repo_plugin(args)
    scope = _parse_slices(args.slices)

    pre = check_preconditions(
        ledger,
        scope=scope,
        require_no_drafts=scope is None and not args.allow_drafts,
    )
    for w in pre.warnings:
        print(f"  ⚠ {w}", file=sys.stderr)
    if not pre.ok and not args.force:
        for e in pre.errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print(
            "Refusing to plan (pass --force to print a partial plan anyway).",
            file=sys.stderr,
        )
        return 2

    next_all = ledger.next_all()
    plan = build_plan(next_all, scope=scope, max_tries=args.max_tries)

    if args.json:
        print(json.dumps(plan.to_event_payload(), indent=2))
    else:
        print(format_plan_text(plan, repo=str(repo)))

    if args.emit_events or args.run_dir:
        run_id = args.run_id or new_run_id()
        run_dir = Path(args.run_dir) if args.run_dir else default_run_dir(repo, run_id)
        with EventWriter(run_dir, run_id) as ev:
            ev.write_json(
                "config.json",
                {
                    "repo": str(repo),
                    "kuru_py": str(kuru_py),
                    "max_tries": args.max_tries,
                    "scope": scope,
                    "command": "plan",
                },
            )
            ev.emit("run.planned", **plan.to_event_payload())
        if not args.json:
            print(f"\nevents: {run_dir / 'events.ndjson'}")

    return 0 if pre.ok or args.force else 2


def _append_progress(repo: Path, run_id: str, result: RunResult) -> None:
    """Best-effort one-line update to .kuru/progress.md. Never raises to caller."""
    path = repo / ".kuru" / "progress.md"
    try:
        if not path.is_file():
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        shipped = ", ".join(result.shipped) or "—"
        stuck_bits = []
        for s in result.stuck:
            if isinstance(s, dict):
                stuck_bits.append(f"{s.get('id', '?')}({s.get('reason', '')})")
            else:
                stuck_bits.append(str(s))
        stuck_s = ", ".join(stuck_bits) or "—"
        capped_s = ", ".join(result.capped) or "—"
        line = (
            f"- {ts} board run `{run_id}`: shipped [{shipped}]; "
            f"capped [{capped_s}]; stuck [{stuck_s}]\n"
            f"  → see `.kuru/BOARD_HANDOFF.md` for the full board handoff\n"
        )
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text + line, encoding="utf-8")
    except OSError:
        pass

def cmd_run(args: argparse.Namespace) -> int:
    repo, kuru_py, ledger = _common_repo_plugin(args)
    scope = _parse_slices(args.slices)

    pre = check_preconditions(
        ledger,
        scope=scope,
        require_no_drafts=scope is None and not args.allow_drafts,
    )
    for w in pre.warnings:
        print(f"  ⚠ {w}", file=sys.stderr)
    if not pre.ok:
        for e in pre.errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print("Refusing to run.", file=sys.stderr)
        return 2

    next_all = ledger.next_all()
    plan = build_plan(next_all, scope=scope, max_tries=args.max_tries)

    if args.ui != "json":
        print(format_plan_text(plan, repo=str(repo)))
        print()

    if not args.yes and sys.stdin.isatty() and not args.dry_run:
        try:
            ans = input("Start run? [y/N] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            print("aborted")
            return 2

    run_id = args.run_id or new_run_id()
    run_dir = Path(args.run_dir) if args.run_dir else default_run_dir(repo, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Cancel is shared with backends (kill). Interactive board UI is Ratatui
    # (`scripts/board-tui.sh`); this process only streams plain / json.
    control = RunControl()
    ui_name = args.ui
    ui = make_run_ui(ui_name)
    listeners = [ui.on_event] if ui is not None else []
    skip_check = not bool(getattr(args, "check_contract", False))

    with EventWriter(run_dir, run_id, listeners=listeners) as ev:
        ev.write_json(
            "config.json",
            {
                "repo": str(repo),
                "kuru_py": str(kuru_py),
                "backend": args.backend,
                "max_tries": args.max_tries,
                "scope": scope,
                "review": plan.review,
                "command": "run",
                "check_contract": not skip_check,
                "ui": ui_name,
            },
        )
        ev.emit("run.planned", **plan.to_event_payload())
        ev.emit("run.started", run_id=run_id, backend=args.backend, review=plan.review)

        if args.dry_run:
            print("(dry-run — not starting pipelines)")
            return 0

        backend = _make_backend(
            args,
            ledger=ledger,
            kuru_py=kuru_py,
            plugin_dir=Path(args.plugin_dir),
            control=control,
        )
        if backend is None:
            return 2

        sched = Scheduler(
            ledger=ledger,
            backend=backend,
            events=ev,
            run_dir=run_dir,
            review=plan.review,
            max_tries=args.max_tries,
            control=control,
            skip_check=skip_check,
        )

        result_box: dict = {}

        def _run_sched() -> None:
            result_box["result"] = sched.run(plan)
            res = result_box["result"]
            ev.write_json("summary.json", res.to_summary())
            # Deferred commit if anything shipped
            if res.shipped and not args.no_commit:
                msg = args.commit_message or (
                    f"kuru: board run {run_id} — ship {', '.join(res.shipped)}"
                )
                ev.emit("commit.started", message=msg, slices=res.shipped)
                proc = ledger.commit(message=msg, slices=res.shipped)
                ok = proc.returncode == 0
                detail = (proc.stdout or proc.stderr or "").strip().splitlines()
                detail_s = detail[-1] if detail else ""
                ev.emit("commit.finished", ok=ok, detail=detail_s)
                result_box["commit_detail"] = detail_s
                result_box["commit_ok"] = ok
            # Best-effort progress.md line + agent-oriented BOARD_HANDOFF.md
            _append_progress(repo, run_id, res)
            from board.handoff import write_board_handoff

            write_board_handoff(
                repo,
                run_id=run_id,
                run_dir=run_dir,
                result=res,
                backend=getattr(args, "backend", "") or "",
                review=plan.review,
            )

        _run_sched()
        result = result_box["result"]
        if (
            result.shipped
            and not args.no_commit
            and args.ui == "plain"
            and result_box.get("commit_ok")
        ):
            print(f"deferred commit: {result_box.get('commit_detail') or 'ok'}")

        if args.ui == "json":
            print(json.dumps(result.to_summary(), indent=2))
        else:
            print()
            print(
                f"summary: shipped={result.shipped} capped={result.capped} "
                f"stuck={result.stuck} blocked_at_start={result.blocked_at_start}"
            )
            print(f"events: {run_dir / 'events.ndjson'}")
            print(f"handoff: {repo / '.kuru' / 'BOARD_HANDOFF.md'}")

        return result.exit_code()


def cmd_status(args: argparse.Namespace) -> int:
    """List recent .kuru/runs/* with summary.json when present."""
    repo = Path(args.repo).resolve()
    runs_root = repo / ".kuru" / "runs"
    if not runs_root.is_dir():
        print(f"no runs under {runs_root}")
        return 0

    run_dirs = sorted(
        [p for p in runs_root.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    limit = max(1, int(args.limit or 20))
    run_dirs = run_dirs[:limit]

    if not run_dirs:
        print(f"no runs under {runs_root}")
        return 0

    if args.json:
        rows = []
        for d in run_dirs:
            rows.append(_run_status_row(d))
        print(json.dumps(rows, indent=2))
        return 0

    print(f"runs under {runs_root} (newest first, limit {limit}):")
    for d in run_dirs:
        row = _run_status_row(d)
        shipped = row.get("shipped") or []
        capped = row.get("capped") or []
        stuck = row.get("stuck") or []
        stuck_n = len(stuck) if isinstance(stuck, list) else 0
        backend = row.get("backend") or "?"
        print(
            f"  {row['run_id']:<28} backend={backend:<7} "
            f"shipped={len(shipped)} capped={len(capped)} stuck={stuck_n}  "
            f"{row.get('path', '')}"
        )
        if args.verbose and (shipped or capped or stuck):
            if shipped:
                print(f"    shipped: {', '.join(shipped)}")
            if capped:
                print(f"    capped:  {', '.join(capped)}")
            if stuck:
                bits = []
                for s in stuck:
                    if isinstance(s, dict):
                        bits.append(f"{s.get('id', '?')}:{s.get('reason', '')}")
                    else:
                        bits.append(str(s))
                print(f"    stuck:   {', '.join(bits)}")
    return 0


def _run_status_row(run_dir: Path) -> dict:
    rid = run_dir.name
    row: dict = {
        "run_id": rid,
        "path": str(run_dir),
        "shipped": [],
        "capped": [],
        "stuck": [],
        "backend": None,
    }
    cfg_path = run_dir / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            row["backend"] = cfg.get("backend")
            row["config"] = cfg
        except (OSError, json.JSONDecodeError):
            pass
    sum_path = run_dir / "summary.json"
    if sum_path.is_file():
        try:
            s = json.loads(sum_path.read_text(encoding="utf-8"))
            row["shipped"] = s.get("shipped") or []
            row["capped"] = s.get("capped") or []
            row["stuck"] = s.get("stuck") or []
            row["summary"] = s
        except (OSError, json.JSONDecodeError):
            pass
    return row


def cmd_logs(args: argparse.Namespace) -> int:
    """Print path (or tail) of a stage log under a run dir."""
    repo = Path(args.repo).resolve()
    run_id = args.run_id
    if not run_id:
        # newest run
        runs_root = repo / ".kuru" / "runs"
        if not runs_root.is_dir():
            print("error: no runs found", file=sys.stderr)
            return 2
        dirs = sorted(
            [p for p in runs_root.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not dirs:
            print("error: no runs found", file=sys.stderr)
            return 2
        run_dir = dirs[0]
        run_id = run_dir.name
    else:
        run_dir = repo / ".kuru" / "runs" / run_id
        if not run_dir.is_dir():
            # allow absolute / relative override
            alt = Path(run_id)
            if alt.is_dir():
                run_dir = alt
            else:
                print(f"error: run dir not found: {run_dir}", file=sys.stderr)
                return 2

    sid = (args.slice or "").upper() or None
    stage = args.stage or None

    if sid and stage:
        path = run_dir / sid / f"{stage}.log"
        if not path.is_file():
            print(f"error: log not found: {path}", file=sys.stderr)
            return 2
        if args.tail and args.tail > 0:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                for ln in lines[-args.tail :]:
                    print(ln)
            except OSError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        else:
            print(path)
        return 0

    # List available logs
    print(f"run: {run_dir}")
    found = False
    for d in sorted(run_dir.iterdir()) if run_dir.is_dir() else []:
        if not d.is_dir() or not d.name.startswith("SL-"):
            continue
        if sid and d.name != sid:
            continue
        for log in sorted(d.glob("*.log")):
            found = True
            print(f"  {d.name}/{log.name}  ({log.stat().st_size} bytes)")
    if not found:
        print("  (no stage logs)")
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print(f"board {__version__}")
    return 0


def _make_backend(
    args: argparse.Namespace,
    *,
    ledger: Ledger,
    kuru_py: Path,
    plugin_dir: Path,
    control: RunControl | None = None,
):
    """Construct the stage backend. Prints errors and returns None on failure."""
    if args.backend == "mock":
        scenarios = load_mock_scenarios(
            Path(args.mock_scenario) if args.mock_scenario else None
        )
        return MockBackend(ledger, scenarios, control=control)

    if args.backend == "claude":
        claude_bin = find_claude(getattr(args, "claude_bin", None))
        if not claude_bin:
            print(
                "error: claude CLI not found (use --claude-bin PATH, or install "
                "Claude Code so `claude` is on PATH).",
                file=sys.stderr,
            )
            return None
        return ClaudeBackend(
            plugin_dir=plugin_dir.resolve(),
            claude_bin=claude_bin,
            permission_mode=getattr(args, "permission_mode", None),
            allowed_tools=getattr(args, "allowed_tools", None),
            settings=getattr(args, "settings", None),
            model=getattr(args, "model", None),
            kuru_py=kuru_py,
            control=control,
        )

    if args.backend == "grok":
        grok_bin = find_grok(getattr(args, "grok_bin", None))
        if not grok_bin:
            print(
                "error: grok CLI not found (use --grok-bin PATH, or install the "
                "Grok Build CLI so `grok` is on PATH — common: ~/.local/bin/grok, "
                "~/.grok/bin/grok).",
                file=sys.stderr,
            )
            return None
        # Board default: --always-approve (Grok has no --yolo). Opt out with
        # --no-always-approve for interactive permission prompts.
        always = not getattr(args, "no_always_approve", False)
        return GrokBackend(
            plugin_dir=plugin_dir.resolve(),
            grok_bin=grok_bin,
            always_approve=always,
            permission_mode=getattr(args, "grok_permission_mode", None) or None,
            model=getattr(args, "model", None),
            max_turns=getattr(args, "max_turns", None),
            kuru_py=kuru_py,
            control=control,
        )

    if args.backend == "cmd":
        template = getattr(args, "backend_cmd", None) or ""
        if not template.strip():
            print(
                "error: --backend cmd requires --backend-cmd "
                "'… {prompt_file} … {cwd} …' template",
                file=sys.stderr,
            )
            return None
        try:
            return CmdBackend(
                template,
                kuru_py=kuru_py,
                control=control,
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return None

    print(
        f"error: backend {args.backend!r} not implemented "
        f"(supported: mock, claude, grok, cmd)",
        file=sys.stderr,
    )
    return None


def _add_repo_flags(p: argparse.ArgumentParser, here: Path) -> None:
    p.add_argument("--repo", default=".", help="target repo with .kuru/ (default: cwd)")
    p.add_argument(
        "--plugin-dir",
        default=str(here),
        help="kurukuru plugin root containing scripts/kuru.py",
    )
    p.add_argument(
        "--slices",
        default=None,
        metavar="IDS",
        help="comma-separated slice scope (e.g. SL-0001,SL-0002)",
    )
    p.add_argument("--max-tries", type=int, default=2, help="per-slice try budget")
    p.add_argument(
        "--allow-drafts",
        action="store_true",
        help="do not require an empty draft set on whole-board mode",
    )


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(
        prog="board",
        description=(
            "Kurukuru board runner — agent-agnostic multi-slice orchestrator.\n"
            "See impl/BOARD_RUNNER_PLAN.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="store_true", help="print version and exit")

    sub = p.add_subparsers(dest="command")

    plan_p = sub.add_parser("plan", help="show the multi-slice plan (no agents)")
    _add_repo_flags(plan_p, here)
    plan_p.add_argument("--json", action="store_true", help="machine-readable plan")
    plan_p.add_argument(
        "--emit-events",
        action="store_true",
        help="write run.planned NDJSON under .kuru/runs/<run_id>/",
    )
    plan_p.add_argument("--run-dir", default=None, help="override run directory for events")
    plan_p.add_argument("--run-id", default=None, help="override run id")
    plan_p.add_argument(
        "--force",
        action="store_true",
        help="print plan even if preconditions fail",
    )
    plan_p.set_defaults(func=cmd_plan)

    run_p = sub.add_parser(
        "run",
        help="drive ready slices (--backend mock|claude|grok|cmd; default mock)",
    )
    _add_repo_flags(run_p, here)
    run_p.add_argument(
        "--backend",
        default="mock",
        choices=["mock", "claude", "grok", "cmd"],
        help="stage worker (mock for tests; claude/grok/cmd for live agents)",
    )
    run_p.add_argument(
        "--backend-cmd",
        default=None,
        metavar="TEMPLATE",
        help=(
            "cmd backend: shell template with {prompt_file} {prompt} {cwd} "
            "{slice} {stage} {kuru_py}"
        ),
    )
    run_p.add_argument(
        "--mock-scenario",
        default=None,
        metavar="PATH",
        help="JSON scenario file for mock backend",
    )
    run_p.add_argument(
        "--check-contract",
        action="store_true",
        help=(
            "run advisory contract check before first clean build "
            "(default: skip; mock scenarios can exercise repair)"
        ),
    )
    # Claude backend flags (mirrors runner.py)
    run_p.add_argument(
        "--claude-bin",
        default=None,
        metavar="PATH",
        help="path to the claude CLI (default: autodetect)",
    )
    run_p.add_argument(
        "--permission-mode",
        default="bypassPermissions",
        help="claude --permission-mode (default: bypassPermissions for autonomous runs)",
    )
    run_p.add_argument(
        "--allowed-tools",
        default=None,
        help="pass-through to claude --allowedTools",
    )
    run_p.add_argument(
        "--settings",
        default=None,
        help="pass-through to claude --settings (JSON permission allowlist)",
    )
    run_p.add_argument(
        "--model",
        default=None,
        help="pass-through model id (claude --model / grok -m)",
    )
    # Grok backend flags
    run_p.add_argument(
        "--grok-bin",
        default=None,
        metavar="PATH",
        help="path to the grok CLI (default: PATH, ~/.local/bin/grok, ~/.grok/bin/grok)",
    )
    run_p.add_argument(
        "--no-always-approve",
        action="store_true",
        help=(
            "grok: do not pass --always-approve (board default is always-approve "
            "for autonomous runs; Grok has no --yolo flag)"
        ),
    )
    run_p.add_argument(
        "--grok-permission-mode",
        default=None,
        metavar="MODE",
        help=(
            "grok --permission-mode (optional; default: omit and rely on "
            "--always-approve). Values: default|acceptEdits|auto|dontAsk|"
            "bypassPermissions|plan"
        ),
    )
    run_p.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help="grok --max-turns (optional cap on agent turns per stage)",
    )
    run_p.add_argument(
        "--ui",
        default="plain",
        choices=["plain", "json"],
        help=(
            "progress UI: plain (streaming logs, default), json (summary only). "
            "Interactive hierarchical board: scripts/board-tui.sh (Ratatui)"
        ),
    )
    run_p.add_argument("--yes", "-y", action="store_true", help="skip approval prompt")
    run_p.add_argument("--dry-run", action="store_true", help="plan only, do not run")
    run_p.add_argument("--run-dir", default=None, help="override run directory")
    run_p.add_argument("--run-id", default=None, help="override run id")
    run_p.add_argument(
        "--no-commit",
        action="store_true",
        help="skip deferred kuru commit after ships",
    )
    run_p.add_argument("--commit-message", "-m", default=None, help="deferred commit message")
    run_p.set_defaults(func=cmd_run)

    status_p = sub.add_parser(
        "status",
        help="list recent .kuru/runs/* with summary (shipped/capped/stuck)",
    )
    status_p.add_argument("--repo", default=".", help="target repo (default: cwd)")
    status_p.add_argument(
        "--limit", type=int, default=20, help="max runs to show (default 20)"
    )
    status_p.add_argument("--json", action="store_true", help="machine-readable")
    status_p.add_argument(
        "-v", "--verbose", action="store_true", help="list slice ids per outcome"
    )
    status_p.set_defaults(func=cmd_status)

    logs_p = sub.add_parser(
        "logs",
        help="print stage log path (or --tail N) under a run",
    )
    logs_p.add_argument("--repo", default=".", help="target repo (default: cwd)")
    logs_p.add_argument(
        "--run-id",
        default=None,
        help="run id under .kuru/runs/ (default: newest)",
    )
    logs_p.add_argument("--slice", default=None, metavar="ID", help="slice id e.g. SL-0001")
    logs_p.add_argument(
        "--stage",
        default=None,
        help="stage name (build|verify|review|ship|check|…)",
    )
    logs_p.add_argument(
        "--tail",
        type=int,
        default=0,
        metavar="N",
        help="print last N lines instead of just the path",
    )
    logs_p.set_defaults(func=cmd_logs)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False) and not getattr(args, "command", None):
        return cmd_version(args)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    return args.func(args)
