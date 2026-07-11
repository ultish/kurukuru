#!/usr/bin/env bash
# board.sh — convenient launcher for the Kurukuru board runner (`python3 -m board`).
#
# Resolves the plugin root from this script's location, sets PYTHONPATH / KURU_PY /
# CLAUDE_PLUGIN_ROOT, and injects --plugin-dir when you don't pass one.
#
# Usage:
#   ./scripts/board.sh plan
#   ./scripts/board.sh run -y --backend mock
#   ./scripts/board.sh run -y --backend grok --repo ~/Developer/kuru-test
#   ./scripts/board.sh status
#   ./scripts/board.sh logs --slice SL-0001 --stage build --tail 40
#
# Env overrides:
#   BOARD_PLUGIN_DIR  plugin checkout (default: parent of scripts/)
#   BOARD_REPO        default --repo if not on the CLI (default: cwd)
#   KURU_PY           path to kuru.py (default: $PLUGIN/scripts/kuru.py)
#   PYTHON            python interpreter (default: python3)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${BOARD_PLUGIN_DIR:-$SCRIPT_DIR/..}" && pwd)"
PYTHON="${PYTHON:-python3}"

if [[ ! -d "$PLUGIN_DIR/board" ]]; then
  echo "error: board package not found under $PLUGIN_DIR (set BOARD_PLUGIN_DIR)" >&2
  exit 2
fi
if [[ ! -f "$PLUGIN_DIR/scripts/kuru.py" ]]; then
  echo "error: scripts/kuru.py not found under $PLUGIN_DIR" >&2
  exit 2
fi

export PYTHONPATH="$PLUGIN_DIR${PYTHONPATH:+:$PYTHONPATH}"
export KURU_PY="${KURU_PY:-$PLUGIN_DIR/scripts/kuru.py}"
export CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$PLUGIN_DIR}"

usage() {
  cat <<EOF
Kurukuru board runner

  $(basename "$0") <plan|run|status|logs|version> [options]

Common examples:
  $(basename "$0") plan
  $(basename "$0") plan --repo ~/Developer/kuru-test
  $(basename "$0") run -y --backend mock
  $(basename "$0") run -y --backend grok
  $(basename "$0") run -y --backend claude
  $(basename "$0") run -y --backend cmd --backend-cmd 'my-agent -p {prompt_file} --dir {cwd}'
  $(basename "$0") status
  $(basename "$0") logs --slice SL-0001 --stage verify --tail 50

Interactive hierarchical board (Ratatui — not part of this launcher):
  $PLUGIN_DIR/scripts/board-tui.sh --repo ~/Developer/kuru-test --backend mock
  # or pair headless board + TUI watch:
  #   $(basename "$0") run -y --backend mock --repo ~/Developer/kuru-test
  #   $PLUGIN_DIR/scripts/board-tui.sh --repo ~/Developer/kuru-test

Plugin:  $PLUGIN_DIR
KURU_PY: \$KURU_PY
Repo:    \${BOARD_REPO:-. }  (cwd unless --repo or BOARD_REPO)

Pass-through: all flags go to \`python3 -m board\`. See:
  $(basename "$0") run --help
EOF
}

if [[ $# -eq 0 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

# Collect args; inject defaults for --plugin-dir and --repo when absent.
args=("$@")
has_plugin_dir=0
has_repo=0
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    --plugin-dir) has_plugin_dir=1 ;;
    --repo) has_repo=1 ;;
  esac
done

extra=()
if [[ $has_plugin_dir -eq 0 ]]; then
  extra+=(--plugin-dir "$PLUGIN_DIR")
fi
if [[ $has_repo -eq 0 && -n "${BOARD_REPO:-}" ]]; then
  extra+=(--repo "$BOARD_REPO")
fi

# Subcommands that accept --repo/--plugin-dir: plan, run, status, logs
# version does not — still fine to pass unknown? board version ignores them if only --version
# Our CLI: version is separate; status/logs/plan/run have --repo.

exec "$PYTHON" -m board "${args[@]}" "${extra[@]}"
