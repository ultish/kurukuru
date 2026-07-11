# Air-gapped / RHEL 9 board TUI

> Full TUI guide (keys, CLI, layout, builds): **[`tui/README.md`](../tui/README.md)**.  
> This page is the short airgap + GitHub Release checklist.

The Ratatui board UI (`kuru-board-tui`) is a **standalone Linux binary**. Air-gapped
hosts do not need Rust, Cargo, crates.io, or Docker at runtime.

## What to ship

| Asset | Purpose |
|-------|---------|
| `kuru-board-tui-linux-amd64.tar.gz` | **Preferred release asset** (archive + short README) |
| `kuru-board-tui-linux-amd64` | Bare ELF (no extension — normal on Unix) |
| `SHA256SUMS` | Checksums |
| Kurukuru plugin tree | `board/`, `scripts/kuru.py`, `templates/`, … for `python3 -m board` |

macOS developers build from source (`cd tui && cargo build --release`). No
Linux binary is required on Mac.

## Build the RHEL 9 artifact (networked machine)

Requires Docker or Podman. Uses **Rocky Linux 9** so glibc matches RHEL 9
(building on newer distros can produce binaries that fail with
`GLIBC_2.xx not found` on RHEL 9).

```bash
# From the kurukuru **repo root** (not tui/)
# Detects Apple Container, docker, or podman (prefers a *working* runtime)
./scripts/build-tui-rhel9.sh

# Force Apple Container (macOS Container app):
DOCKER=container ./scripts/build-tui-rhel9.sh
# If the app is installed but idle:
#   container system start

# Manual (context = tui/, platform = linux/amd64 — required on Apple Silicon):
# docker build --platform linux/amd64 -f tui/Dockerfile.rhel9 -t kuru-board-tui:rhel9 tui/
# container build --platform linux/amd64 -f tui/Dockerfile.rhel9 -t kuru-board-tui:rhel9 tui/
```Produces:

```text
dist/kuru-board-tui-linux-amd64.tar.gz
dist/kuru-board-tui-linux-amd64
dist/kuru-board-tui
dist/SHA256SUMS
```
On Apple Silicon the script uses `--platform linux/amd64` so the artifact is
**x86_64**, not aarch64.

### Target triple

```text
x86_64-unknown-linux-gnu
  arch=x86_64  vendor=unknown  os=linux  abi=glibc (dynamic)
```

`unknown` is the normal Linux vendor placeholder in Rust triples — not “unsupported.”

## Attach to a GitHub Release

```bash
TAG=vX.Y.Z   # or your tag
./scripts/build-tui-rhel9.sh
gh release upload "$TAG" \
  dist/kuru-board-tui-linux-amd64.tar.gz \
  dist/SHA256SUMS
```
Or upload the same files in the GitHub web UI.

Suggested release note blurb:

```text
### kuru-board-tui (Linux amd64)
- Asset: kuru-board-tui-linux-amd64.tar.gz
- Built on Rocky 9 (RHEL 9–compatible glibc), x86_64 only
- Airgap: tar -xzf … && chmod +x kuru-board-tui; copy harness plugin tree
- macOS: build from source in tui/
```
## Run on air-gapped RHEL 9

```bash
tar -xzf kuru-board-tui-linux-amd64.tar.gz
cd kuru-board-tui-linux-amd64
chmod +x kuru-board-tui

# Optional: put harness plugin somewhere permanent
export PYTHONPATH=/opt/kuru/harness
export KURU_PY=/opt/kuru/harness/scripts/kuru.py

# Viewer / control plane TUI
./kuru-board-tui \
  --repo /path/to/project \
  --plugin-dir /opt/kuru/harness \
  --backend mock

# Orchestrator (separate terminal, or start with [s] in the TUI)
python3 -m board run -y --backend mock \
  --repo /path/to/project \
  --plugin-dir /opt/kuru/harness
```

Inside the air gap, prefer `--backend mock` or `--backend cmd` with a local agent.
Cloud Claude/Grok CLIs only work if those tools and their endpoints exist in the environment.

## Verify before transfer

On any x86_64 Linux close to RHEL9 (or the build machine):

```bash
file dist/kuru-board-tui-linux-amd64
# ELF 64-bit LSB … x86-64, dynamically linked

ldd dist/kuru-board-tui-linux-amd64
# should resolve libc.so.6 etc. without "not found"

./dist/kuru-board-tui-linux-amd64 --help
```
## Fallback without the Rust binary

Python-only (stdlib):

```bash
python3 -m board run --ui plain …    # streaming event lines (default)
# hierarchical board is this binary (kuru-board-tui), not a Python --ui mode
```

## Rebuild options

```bash
./scripts/build-tui-rhel9.sh --no-cache   # force full rebuild
DOCKER=podman ./scripts/build-tui-rhel9.sh
IMAGE_TAG=my-tui:9 ./scripts/build-tui-rhel9.sh
```
