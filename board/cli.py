"""board CLI — plan + run (mock / claude backends)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from board import __version__
from board.backends.claude import ClaudeBackend, find_claude
from board.backends.mock import MockBackend, load_mock_scenarios
from board.events import EventWriter, default_run_dir, new_run_id
from board.ledger import Ledger, resolve_kuru_py
from board.plan import build_plan, format_plan_text
from board.preconditions import check_preconditions
from board.scheduler import Scheduler
from board.ui.plain import PlainUI


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

    ui = PlainUI() if args.ui == "plain" else None
    listeners = [ui.on_event] if ui else []

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
            },
        )
        ev.emit("run.planned", **plan.to_event_payload())
        ev.emit("run.started", run_id=run_id, backend=args.backend, review=plan.review)

        if args.dry_run:
            print("(dry-run — not starting pipelines)")
            return 0

        backend = _make_backend(args, ledger=ledger, kuru_py=kuru_py, plugin_dir=Path(args.plugin_dir))
        if backend is None:
            return 2

        sched = Scheduler(
            ledger=ledger,
            backend=backend,
            events=ev,
            run_dir=run_dir,
            review=plan.review,
            max_tries=args.max_tries,
        )
        result = sched.run(plan)
        ev.write_json("summary.json", result.to_summary())

        # Deferred commit if anything shipped
        if result.shipped and not args.no_commit:
            msg = args.commit_message or (
                f"kuru: board run {run_id} — ship {', '.join(result.shipped)}"
            )
            ev.emit("commit.started", message=msg, slices=result.shipped)
            proc = ledger.commit(message=msg, slices=result.shipped)
            ok = proc.returncode == 0
            detail = (proc.stdout or proc.stderr or "").strip().splitlines()
            detail_s = detail[-1] if detail else ""
            ev.emit("commit.finished", ok=ok, detail=detail_s)
            # Assert runs/ not in last commit when possible
            if ok and args.ui == "plain":
                print(f"deferred commit: {detail_s or 'ok'}")

        if args.ui == "json":
            print(json.dumps(result.to_summary(), indent=2))
        else:
            print()
            print(
                f"summary: shipped={result.shipped} capped={result.capped} "
                f"stuck={result.stuck} blocked_at_start={result.blocked_at_start}"
            )
            print(f"events: {run_dir / 'events.ndjson'}")

        return result.exit_code()


def cmd_version(_: argparse.Namespace) -> int:
    print(f"board {__version__}")
    return 0


def _make_backend(
    args: argparse.Namespace,
    *,
    ledger: Ledger,
    kuru_py: Path,
    plugin_dir: Path,
):
    """Construct the stage backend. Prints errors and returns None on failure."""
    if args.backend == "mock":
        scenarios = load_mock_scenarios(
            Path(args.mock_scenario) if args.mock_scenario else None
        )
        return MockBackend(ledger, scenarios)

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
        )

    print(
        f"error: backend {args.backend!r} not implemented yet "
        f"(supported: mock, claude)",
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
        help="drive ready slices (--backend mock|claude; default mock)",
    )
    _add_repo_flags(run_p, here)
    run_p.add_argument(
        "--backend",
        default="mock",
        choices=["mock", "claude", "grok", "cmd"],
        help="stage worker (mock for tests; claude for live runs; grok/cmd later)",
    )
    run_p.add_argument(
        "--mock-scenario",
        default=None,
        metavar="PATH",
        help="JSON scenario file for mock backend",
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
        help="pass-through to claude --model",
    )
    run_p.add_argument(
        "--ui",
        default="plain",
        choices=["plain", "json"],
        help="progress UI (board TUI is Phase 3)",
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
