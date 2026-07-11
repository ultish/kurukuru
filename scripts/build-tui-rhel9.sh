#!/usr/bin/env bash
# Build a RHEL 9–compatible x86_64 kuru-board-tui binary via a container runtime.
#
# Supports (auto-detected, or set DOCKER=…):
#   - docker          (Docker Desktop / Rancher Desktop / colima …)
#   - container       (Apple Container app — macOS)
#   - podman
#
# Output (repo root):
#   dist/kuru-board-tui-linux-amd64              # bare ELF (Unix style, no extension)
#   dist/kuru-board-tui-linux-amd64.tar.gz       # release archive (has .tar.gz)
#   dist/kuru-board-tui                          # short name (same binary)
#   dist/SHA256SUMS
#
# Attach the .tar.gz (and SHA256SUMS) to a GitHub Release.
#
# Usage:
#   ./scripts/build-tui-rhel9.sh
#   ./scripts/build-tui-rhel9.sh --no-cache
#   DOCKER=container ./scripts/build-tui-rhel9.sh
#   DOCKER=podman ./scripts/build-tui-rhel9.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE_TAG="${IMAGE_TAG:-kuru-board-tui:rhel9}"
# Force x86_64 even on Apple Silicon
PLATFORM="${PLATFORM:-linux/amd64}"
CONTAINER_NAME="${CONTAINER_NAME:-kuru-board-tui-rhel9-extract}"
DIST="${DIST:-$ROOT/dist}"
NO_CACHE=0

for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE=1 ;;
    -h|--help)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown arg: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

# ── detect docker | container (Apple) | podman ───────────────────────────────

_cli_ready() {
  # $1 = binary name. Return 0 if it can talk to its daemon/backend.
  local bin="$1"
  command -v "$bin" >/dev/null 2>&1 || return 1
  case "$bin" in
    docker)
      # Rancher/colima often put docker on PATH even when the daemon is down.
      docker info >/dev/null 2>&1
      ;;
    podman)
      podman info >/dev/null 2>&1
      ;;
    container)
      # Apple Container app — status field "running" when the system service is up.
      container system status 2>/dev/null | grep -qiE 'status[[:space:]]+running' \
        || container system status 2>/dev/null | grep -qi 'running'
      ;;
    *)
      return 1
      ;;
  esac
}

detect_cli() {
  if [[ -n "${DOCKER:-}" ]]; then
    if ! command -v "$DOCKER" >/dev/null 2>&1; then
      echo "error: DOCKER=$DOCKER not found on PATH" >&2
      exit 2
    fi
    if ! _cli_ready "$DOCKER"; then
      echo "error: DOCKER=$DOCKER is installed but not ready." >&2
      case "$DOCKER" in
        docker)
          echo "  Start Docker Desktop / Rancher Desktop / colima, or use:" >&2
          echo "    DOCKER=container ./scripts/build-tui-rhel9.sh" >&2
          ;;
        container)
          echo "  Start the Container app, then: container system start" >&2
          ;;
        podman)
          echo "  Start the podman machine: podman machine start" >&2
          ;;
      esac
      exit 2
    fi
    echo "$DOCKER"
    return
  fi

  # Prefer a working backend. Docker first when its daemon is up; then Apple
  # Container; then podman. A docker binary with a dead daemon is skipped by
  # _cli_ready (common with Rancher Desktop on PATH while stopped).
  local cand
  for cand in docker container podman; do
    if _cli_ready "$cand"; then
      echo "$cand"
      return
    fi
  done

  echo "error: no working container runtime found." >&2
  echo "  Tried: container (Apple), docker, podman" >&2
  echo "" >&2
  if command -v container >/dev/null 2>&1; then
    echo "  Apple Container is installed but not running. Try:" >&2
    echo "    open -a Container   # or launch the app" >&2
    echo "    container system start" >&2
    echo "    DOCKER=container ./scripts/build-tui-rhel9.sh" >&2
  elif command -v docker >/dev/null 2>&1; then
    echo "  docker is on PATH but the daemon is not reachable." >&2
    echo "  Start Docker Desktop / Rancher Desktop, or use Apple Container:" >&2
    echo "    DOCKER=container ./scripts/build-tui-rhel9.sh" >&2
  else
    echo "  Install Docker Desktop, Rancher Desktop, Apple Container, or Podman." >&2
  fi
  exit 2
}

DOCKER="$(detect_cli)"
echo "==> using runtime: $DOCKER"

if [[ ! -f tui/Cargo.toml || ! -d tui/src ]]; then
  echo "error: run from kurukuru repo (missing tui/Cargo.toml or tui/src)" >&2
  exit 2
fi
if [[ ! -f tui/Dockerfile.rhel9 ]]; then
  echo "error: missing tui/Dockerfile.rhel9" >&2
  exit 2
fi

# Context is tui/ so Dockerfile can COPY Cargo.toml + src without a tui/ prefix.
BUILD_ARGS=(build --platform "$PLATFORM" -f tui/Dockerfile.rhel9 -t "$IMAGE_TAG")
if [[ "$NO_CACHE" -eq 1 ]]; then
  BUILD_ARGS+=(--no-cache)
fi
BUILD_ARGS+=(tui)

echo "==> building image $IMAGE_TAG (platform=$PLATFORM, context=tui/) …"
echo "    $DOCKER ${BUILD_ARGS[*]}"
if ! "$DOCKER" "${BUILD_ARGS[@]}"; then
  echo "" >&2
  echo "error: image build failed." >&2
  echo "" >&2
  echo "Common on Apple Silicon building --platform linux/amd64:" >&2
  echo "" >&2
  echo "  exec /bin/sh: exec format error" >&2
  echo "  qemu: uncaught target signal 11 (Segmentation fault)" >&2
  echo "  rustc installed - (error reading rustc version)  then SIGSEGV" >&2
  echo "" >&2
  echo "  That is broken/missing x86_64 emulation under Docker (QEMU/Rosetta)," >&2
  echo "  not a problem with the Dockerfile itself." >&2
  echo "" >&2
  echo "  Fixes (pick one):" >&2
  echo "    1) Prefer Apple Container (often works when Docker QEMU segfaults):" >&2
  echo "         DOCKER=container ./scripts/build-tui-rhel9.sh" >&2
  echo "         # open Container app; container system start if needed" >&2
  echo "    2) Docker Desktop → Settings → General:" >&2
  echo "         Virtualization framework ON + Rosetta for x86_64/amd64 ON" >&2
  echo "         restart Docker, then: DOCKER=docker ./scripts/build-tui-rhel9.sh --no-cache" >&2
  echo "    3) Build on real x86_64 Linux / GitHub Actions (no emulation)." >&2
  echo "    4) If dist/ already has a good binary from an earlier successful build," >&2
  echo "       you can skip rebuild and attach that tarball." >&2
  exit 2
fi

echo "==> extracting /out → $DIST …"
mkdir -p "$DIST"

# Primary extract: stream tar on stdout → host tar. No volume mounts, no
# container cp. Avoids Apple Container issues:
#   - cp from stopped container → "not running"
#   - cp -a / chmod on bind mount → "Operation not permitted"
extract_via_tar_stream() {
  echo "    (using $DOCKER run | tar stream — no volume mount)"
  # GNU tar in Rocky; BSD tar on macOS host both accept this for simple files.
  if ! "$DOCKER" run --rm --platform "$PLATFORM" "$IMAGE_TAG" \
      tar -C /out -cf - . \
    | tar -C "$DIST" -xf -; then
    return 1
  fi
  # Host-side sanity
  [[ -f "$DIST/kuru-board-tui" || -f "$DIST/kuru-board-tui-linux-amd64" ]]
}

extract_via_create_cp() {
  # Docker/Podman only: copy from a created-but-stopped container.
  "$DOCKER" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 \
    || "$DOCKER" delete -f "$CONTAINER_NAME" >/dev/null 2>&1 \
    || true

  local create_args=(create --name "$CONTAINER_NAME" --platform "$PLATFORM" "$IMAGE_TAG")
  if ! "$DOCKER" "${create_args[@]}" >/dev/null; then
    return 1
  fi

  if ! "$DOCKER" cp "$CONTAINER_NAME:/out/." "$DIST/" 2>/dev/null; then
    if "$DOCKER" cp "$CONTAINER_NAME:/out" "$DIST-tmp" 2>/dev/null; then
      if [[ -d "$DIST-tmp/out" ]]; then
        cp -R "$DIST-tmp/out"/. "$DIST"/
      else
        cp -R "$DIST-tmp"/. "$DIST"/
      fi
      rm -rf "$DIST-tmp"
    else
      "$DOCKER" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 \
        || "$DOCKER" delete -f "$CONTAINER_NAME" >/dev/null 2>&1 \
        || true
      return 1
    fi
  fi

  "$DOCKER" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 \
    || "$DOCKER" delete -f "$CONTAINER_NAME" >/dev/null 2>&1 \
    || true
  [[ -f "$DIST/kuru-board-tui" || -f "$DIST/kuru-board-tui-linux-amd64" ]]
}

if ! extract_via_tar_stream; then
  echo "    tar stream failed; trying create+cp (docker/podman) …" >&2
  if ! extract_via_create_cp; then
    echo "error: could not extract /out from image with $DOCKER" >&2
    echo "  Manual fallback:" >&2
    echo "    $DOCKER run --rm --platform $PLATFORM $IMAGE_TAG tar -C /out -cf - . | tar -C dist -xf -" >&2
    exit 2
  fi
fi

# Normalize names (support older image that only wrote short name, or rust-triple name)
BIN_SHORT="$DIST/kuru-board-tui"
BIN_LINUX="$DIST/kuru-board-tui-linux-amd64"
BIN_LEGACY="$DIST/kuru-board-tui-x86_64-unknown-linux-gnu"
if [[ ! -f "$BIN_LINUX" && -f "$BIN_LEGACY" ]]; then
  cp "$BIN_LEGACY" "$BIN_LINUX"
fi
if [[ ! -f "$BIN_LINUX" && -f "$BIN_SHORT" ]]; then
  cp "$BIN_SHORT" "$BIN_LINUX"
fi
if [[ -f "$BIN_LINUX" && ! -f "$BIN_SHORT" ]]; then
  cp "$BIN_LINUX" "$BIN_SHORT"
fi
# Drop verbose rust-triple name if present (redundant)
rm -f "$BIN_LEGACY" 2>/dev/null || true

chmod +x "$BIN_LINUX" "$BIN_SHORT" 2>/dev/null || true

if [[ ! -f "$BIN_LINUX" ]]; then
  echo "error: extract failed — $BIN_LINUX missing. Contents of $DIST:" >&2
  ls -la "$DIST" >&2 || true
  exit 2
fi

# Release archive with a real extension (what you attach on GitHub)
ARCHIVE="$DIST/kuru-board-tui-linux-amd64.tar.gz"
# Pack binary + checksums note under a versioned-looking dir name inside the tarball
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/kuru-tui-pack.XXXXXX")
mkdir -p "$STAGE/kuru-board-tui-linux-amd64"
cp "$BIN_LINUX" "$STAGE/kuru-board-tui-linux-amd64/kuru-board-tui"
cat > "$STAGE/kuru-board-tui-linux-amd64/README.txt" <<'EOF'
kuru-board-tui (Linux amd64 / x86_64)

Built for RHEL 9–class glibc (Rocky 9 builder).

  chmod +x kuru-board-tui
  ./kuru-board-tui --help
  ./kuru-board-tui --repo /path/to/project --plugin-dir /path/to/harness --backend mock

See tui/README.md and docs/airgap-tui.md in the kurukuru repo.
EOF
tar -C "$STAGE" -czf "$ARCHIVE" kuru-board-tui-linux-amd64
rm -rf "$STAGE"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$DIST" && sha256sum kuru-board-tui kuru-board-tui-linux-amd64 kuru-board-tui-linux-amd64.tar.gz > SHA256SUMS)
elif command -v shasum >/dev/null 2>&1; then
  (cd "$DIST" && shasum -a 256 kuru-board-tui kuru-board-tui-linux-amd64 kuru-board-tui-linux-amd64.tar.gz > SHA256SUMS)
fi

echo
echo "==> artifacts"
ls -la "$DIST"
echo
if command -v file >/dev/null 2>&1; then
  file "$BIN_LINUX" || true
fi
echo
echo "GitHub Release attach (recommended):"
echo "  gh release upload <tag> $ARCHIVE $DIST/SHA256SUMS"
echo "  # optional bare binary:"
echo "  gh release upload <tag> $BIN_LINUX"
echo
echo "Airgap smoke (on RHEL9 x86_64):"
echo "  tar -xzf kuru-board-tui-linux-amd64.tar.gz"
echo "  cd kuru-board-tui-linux-amd64 && chmod +x kuru-board-tui && ./kuru-board-tui --help"
