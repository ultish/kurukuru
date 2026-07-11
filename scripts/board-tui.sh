#!/usr/bin/env bash
# board-tui.sh — launch the Ratatui master-detail board UI (+ control plane).
#
# Watches `.kuru/runs/*/events.ndjson` (live tail by default).
# From the TUI you can also start/stop a board run (s / S), pick backend (b),
# and toggle review (R). Help: press ?
#
# Single pane (start from TUI with s):
#   ./scripts/board-tui.sh --repo ~/Developer/kuru-test --backend mock --wait-secs 0
#
# Pair with a headless orchestrator in another pane:
#   ./scripts/board.sh run -y --backend mock --repo ~/Developer/kuru-test
#   ./scripts/board-tui.sh --repo ~/Developer/kuru-test
#
# Or open a finished run:
#   ./scripts/board-tui.sh --run-dir ~/Developer/kuru-test/.kuru/runs/r_abc --no-follow
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${BOARD_PLUGIN_DIR:-$SCRIPT_DIR/..}" && pwd)"
BIN="$PLUGIN_DIR/tui/target/release/kuru-board-tui"
DBG="$PLUGIN_DIR/tui/target/debug/kuru-board-tui"

if [[ ! -x "$BIN" ]]; then
  if command -v cargo >/dev/null 2>&1; then
    echo "building kuru-board-tui (release)…" >&2
    # shellcheck disable=SC1090
    [[ -f "$HOME/.cargo/env" ]] && source "$HOME/.cargo/env"
    (cd "$PLUGIN_DIR/tui" && cargo build --release)
  fi
fi

if [[ -x "$BIN" ]]; then
  exec "$BIN" "$@"
elif [[ -x "$DBG" ]]; then
  exec "$DBG" "$@"
else
  echo "error: kuru-board-tui not found. Install Rust and run:" >&2
  echo "  cd $PLUGIN_DIR/tui && cargo build --release" >&2
  exit 2
fi
