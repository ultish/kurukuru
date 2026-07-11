#!/usr/bin/env bash
# Smoke-test dist/kuru-board-tui-linux-amd64 inside Rocky 9 (linux/amd64).
#
# Use this on a Mac (or any host) to verify the RHEL9-class release binary
# actually runs under the right glibc — without shipping to the airgap yet.
#
# Usage (repo root):
#   ./scripts/test/smoke-tui-linux-amd64.sh
#   DOCKER=container ./scripts/test/smoke-tui-linux-amd64.sh
#   ./scripts/test/smoke-tui-linux-amd64.sh /path/to/kuru-board-tui-linux-amd64
#
set -euo pipefail

# This file lives in scripts/test/; repo root is two levels up.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BIN_HOST="${1:-$ROOT/dist/kuru-board-tui-linux-amd64}"
IMAGE="${SMOKE_IMAGE:-rockylinux:9}"
PLATFORM="${PLATFORM:-linux/amd64}"

if [[ ! -f "$BIN_HOST" ]]; then
  # try unpacking the tarball
  TGZ="$ROOT/dist/kuru-board-tui-linux-amd64.tar.gz"
  if [[ -f "$TGZ" ]]; then
    echo "==> unpacking $TGZ …"
    mkdir -p "$ROOT/dist/_smoke_unpack"
    tar -xzf "$TGZ" -C "$ROOT/dist/_smoke_unpack"
    BIN_HOST=$(find "$ROOT/dist/_smoke_unpack" -type f -name 'kuru-board-tui' | head -1)
  fi
fi

if [[ ! -f "$BIN_HOST" ]]; then
  echo "error: binary not found: ${1:-dist/kuru-board-tui-linux-amd64}" >&2
  echo "  Build first: ./scripts/build-tui-rhel9.sh" >&2
  exit 2
fi

BIN_HOST="$(cd "$(dirname "$BIN_HOST")" && pwd)/$(basename "$BIN_HOST")"
chmod +x "$BIN_HOST" || true

# Detect runtime (same idea as build-tui-rhel9.sh)
_cli_ready() {
  local bin="$1"
  command -v "$bin" >/dev/null 2>&1 || return 1
  case "$bin" in
    docker) docker info >/dev/null 2>&1 ;;
    podman) podman info >/dev/null 2>&1 ;;
    container)
      container system status 2>/dev/null | grep -qiE 'status[[:space:]]+running' \
        || container system status 2>/dev/null | grep -qi 'running'
      ;;
    *) return 1 ;;
  esac
}

if [[ -n "${DOCKER:-}" ]]; then
  CLI="$DOCKER"
else
  CLI=""
  for cand in docker container podman; do
    if _cli_ready "$cand"; then CLI="$cand"; break; fi
  done
fi

if [[ -z "$CLI" ]]; then
  echo "error: no working container runtime (docker/container/podman)" >&2
  exit 2
fi

echo "==> runtime: $CLI"
echo "==> binary:  $BIN_HOST"
echo "==> image:   $IMAGE ($PLATFORM)"
file "$BIN_HOST" 2>/dev/null || true
echo

# Mount binary + harness (for --plugin-dir) + optional fixture for --dump
MOUNT_ARGS=(
  --volume "$BIN_HOST:/usr/local/bin/kuru-board-tui:ro"
  --volume "$ROOT:/harness:ro"
)
if [[ -d "${HOME}/Developer/kuru-test/.kuru/runs" ]]; then
  RUN=$(ls -td "$HOME/Developer/kuru-test/.kuru/runs"/r_* 2>/dev/null | head -1 || true)
  if [[ -n "${RUN:-}" && -f "$RUN/events.ndjson" ]]; then
    echo "==> also testing --dump against $RUN"
    MOUNT_ARGS+=(--volume "$RUN:/run-fixture:ro")
  fi
fi

# Script file avoids nested-quote hell with optional dump step
INNER=$(mktemp)
trap 'rm -f "$INNER"' EXIT
cat > "$INNER" <<'EOS'
set -e
echo "--- uname ---"
uname -m
test "$(uname -m)" = "x86_64"
echo "--- ldd (first lines) ---"
ldd /usr/local/bin/kuru-board-tui 2>&1 | head -15 || true
echo "--- --help ---"
/usr/local/bin/kuru-board-tui --help | head -25
if [ -d /run-fixture ]; then
  echo "--- --dump (fixture) ---"
  /usr/local/bin/kuru-board-tui \
    --plugin-dir /harness \
    --run-dir /run-fixture \
    --dump | head -30
fi
echo
echo "SMOKE OK"
EOS

set +e
"$CLI" run --rm --platform "$PLATFORM" \
  "${MOUNT_ARGS[@]}" \
  --volume "$INNER:/smoke.sh:ro" \
  "$IMAGE" \
  sh /smoke.sh
RC=$?
set -e

if [[ $RC -ne 0 ]]; then
  echo "" >&2
  echo "SMOKE FAILED (exit $RC)." >&2
  echo "  If you see exec format error / qemu segfault: amd64 emulation is broken." >&2
  echo "  Try: DOCKER=container $0" >&2
  echo "  Or enable Rosetta in Rancher/Docker Desktop for x86_64." >&2
  exit "$RC"
fi

echo
echo "Binary runs under Rocky 9 / $PLATFORM — good for RHEL 9–class airgap (x86_64)."
