"""board CLI — Phase 0: plan (+ event emission). run/backends arrive later."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from board import __version__
from board.events import EventWriter, default_run_dir, new_run_id
from board.ledger import Ledger, resolve_kuru_py
from board.plan import build_plan, format_plan_text
from board.preconditions import check_preconditions


def _parse_slices(raw: str | None) -> list[str] | None:
    if not raw or not raw.strip():
        return None
    return [p.strip().upper() for p in raw.split(",") if p.strip()]


def cmd_plan(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    kuru_py = resolve_kuru_py(Path(args.plugin_dir) if args.plugin_dir else None)
    ledger = Ledger(repo, kuru_py)
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


def cmd_version(_: argparse.Namespace) -> int:
    print(f"board {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(
        prog="board",
        description=(
            "Kurukuru board runner — agent-agnostic multi-slice orchestrator.\n"
            "Phase 0: plan only. See impl/BOARD_RUNNER_PLAN.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="store_true", help="print version and exit")

    sub = p.add_subparsers(dest="command")

    plan_p = sub.add_parser("plan", help="show the multi-slice plan (no agents)")
    plan_p.add_argument("--repo", default=".", help="target repo with .kuru/ (default: cwd)")
    plan_p.add_argument(
        "--plugin-dir",
        default=str(here),
        help="kurukuru plugin root containing scripts/kuru.py",
    )
    plan_p.add_argument(
        "--slices",
        default=None,
        metavar="IDS",
        help="comma-separated slice scope (e.g. SL-0001,SL-0002)",
    )
    plan_p.add_argument("--max-tries", type=int, default=2, help="recorded in plan payload")
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
    plan_p.add_argument(
        "--allow-drafts",
        action="store_true",
        help="do not require an empty draft set on whole-board plan",
    )
    plan_p.set_defaults(func=cmd_plan)

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
