#!/usr/bin/env bash
# Build a native macOS kuru-board-tui binary (no container needed — you're
# already on the target platform, unlike scripts/build-tui-rhel9.sh which
# cross-builds for Linux). kuru-board-tui is pure Rust (ratatui/crossterm,
# no vendored C deps like librdkafka/OpenSSL), so this is just
# `cargo build --release` plus packaging to match the RHEL 9 script's dist/
# conventions.
#
# Output (repo root):
#   dist/kuru-board-tui-macos-<arch>       # bare Mach-O binary
#   dist/kuru-board-tui-macos-<arch>.tar.gz  # release archive
#   dist/SHA256SUMS                        # merged with any existing entries
#   dist/otool-macos-<arch>.txt            # dynamic-link audit (otool -L)
#
# Usage:
#   ./scripts/build-tui-macos.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f tui/Cargo.toml || ! -d tui/src ]]; then
  echo "error: run from kurukuru repo (missing tui/Cargo.toml or tui/src)" >&2
  exit 2
fi

DIST="${DIST:-$ROOT/dist}"
mkdir -p "$DIST"

ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  arm64) ARCH="arm64" ;;
  x86_64) ARCH="x86_64" ;;
  *) ARCH="$ARCH_RAW" ;;
esac

BIN_NAME="kuru-board-tui"
BIN_MACOS_NAME="${BIN_NAME}-macos-${ARCH}"
ARCHIVE_NAME="${BIN_MACOS_NAME}.tar.gz"
BIN_MACOS="$DIST/$BIN_MACOS_NAME"
ARCHIVE="$DIST/$ARCHIVE_NAME"

echo "==> cargo build --release (native $ARCH_RAW)"
[[ -f "$HOME/.cargo/env" ]] && source "$HOME/.cargo/env"
(cd tui && cargo build --release)

cp "tui/target/release/$BIN_NAME" "$BIN_MACOS"
chmod +x "$BIN_MACOS"

echo
echo "==> dynamic-link audit (otool -L)"
OTOOL_OUT="$DIST/otool-macos-${ARCH}.txt"
otool -L "$BIN_MACOS" | tee "$OTOOL_OUT"
echo "(pure Rust binary — only system frameworks / libSystem / libiconv expected)"

echo
echo "==> packaging"
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/kuru-tui-pack.XXXXXX")
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' tui/Cargo.toml | head -1)"
INNER="${BIN_NAME}-v${VERSION}-macos-${ARCH}"
mkdir -p "$STAGE/$INNER"
cp "$BIN_MACOS" "$STAGE/$INNER/$BIN_NAME"
cat > "$STAGE/$INNER/README.txt" <<EOF
kuru-board-tui v${VERSION} (macOS ${ARCH})

  chmod +x kuru-board-tui
  ./kuru-board-tui --help
  ./kuru-board-tui --repo /path/to/project --plugin-dir /path/to/harness --backend mock

See tui/README.md in the kurukuru repo.
EOF
cp "$OTOOL_OUT" "$STAGE/$INNER/otool.txt"
tar -C "$STAGE" -czf "$ARCHIVE" "$INNER"
rm -rf "$STAGE"

# Merge into dist/SHA256SUMS rather than clobbering (build-tui-rhel9.sh may
# have already written Linux entries there, or may run after this).
SUMS_TMP="$(mktemp)"
if [[ -f "$DIST/SHA256SUMS" ]]; then
  grep -v -E "$BIN_MACOS_NAME$|$ARCHIVE_NAME$" "$DIST/SHA256SUMS" > "$SUMS_TMP" || true
fi
if command -v sha256sum >/dev/null 2>&1; then
  (cd "$DIST" && sha256sum "$BIN_MACOS_NAME" "$ARCHIVE_NAME" >> "$SUMS_TMP")
elif command -v shasum >/dev/null 2>&1; then
  (cd "$DIST" && shasum -a 256 "$BIN_MACOS_NAME" "$ARCHIVE_NAME" >> "$SUMS_TMP")
fi
mv "$SUMS_TMP" "$DIST/SHA256SUMS"

echo
echo "==> artifacts"
ls -la "$DIST"
echo
file "$BIN_MACOS" || true
echo
echo "GitHub Release attach (recommended):"
echo "  gh release upload <tag> $ARCHIVE $DIST/SHA256SUMS"
