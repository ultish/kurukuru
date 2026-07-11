# kuru-board-tui

Ratatui master–detail terminal UI for the Kurukuru **board runner**.

- **Left:** targets + slices (or stage attempts when you open a slice)
- **Right:** detail + stage log
- **Control plane:** start/stop a board run, pick backend, toggle review
- **Stack:** Rust + [Ratatui](https://ratatui.rs) 0.29 + Crossterm 0.28  
  (same family of stack as Grok Build’s TUI)

The TUI does **not** replace `kuru.py` or `python3 -m board`. It watches
`.kuru/runs/*/events.ndjson` and can spawn `board run --ui plain` so the
orchestrator does not fight for the TTY.

```
harness/
  board/                 # Python orchestrator (plan / run / backends)
  tui/                   # this crate
  scripts/board-tui.sh   # launcher (builds release if needed)
  scripts/build-tui-rhel9.sh
  docs/airgap-tui.md     # short airgap + release checklist
```

---

## Quick start (macOS / any machine with Rust)

```bash
# From the kurukuru (harness) repo root
cd tui
cargo build --release

# Prefer the helper (sets plugin path, builds if missing)
../scripts/board-tui.sh --repo ~/Developer/kuru-test --backend mock --wait-secs 0
```

Or run the binary directly:

```bash
./target/release/kuru-board-tui \
  --repo /path/to/project \
  --plugin-dir /path/to/harness \
  --backend mock \
  --wait-secs 0
```

**Non-TTY snapshot** (CI / pipes):

```bash
./target/release/kuru-board-tui \
  --run-dir /path/to/project/.kuru/runs/r_abc \
  --dump
```

---

## Layout

```text
┌ header: run · backend · review · idle|running ──────────────┐
├─ slices / stages ──────┬─ detail + log ─────────────────────┤
│ ▼ target:default       │ SL-0001  title                     │
│ › ■ SL-0001 shipped    │ pipeline · stages · agent          │
│   · SL-0002 waiting    │ ─────────────────────────────────  │
│                        │ log tail for selected stage        │
└────────────────────────┴────────────────────────────────────┘
  footer: keybinds · status
```

- **Overview:** left = targets/slices; right = preview for selection.
- **Slice drill-in (Enter):** left = chronological **stage attempts**
  (build → verify → review → ship, including retries); right = that stage’s
  log (`.kuru/runs/<id>/<SL>/<stage>.log`).

---

## Keybindings

| Key | Context | Action |
|-----|---------|--------|
| `j` / `k` or arrows | Overview | Move selection |
| **`Enter`** | Overview, on slice | **Open slice** (stage list + logs) |
| `Enter` / `Space` | Overview, on target | Expand/collapse target |
| `l` / `→` | Overview, on slice | Open slice |
| `h` / `←` | Overview | Collapse target |
| `j` / `k` | Slice open | Move between stage attempts |
| `Esc` | Slice open | Back to overview |
| `Esc` | Modal open | Close modal (**does not quit**) |
| `PgUp` / `PgDn` or `K` / `J` | Either | Scroll log |
| `w` | Overview | Filter blockers only |
| `r` | Either | Reload / poll events |
| **`s`** | Overview | **Start** board run (confirm dialog) |
| **`S`** or **`x`** | Overview | **Stop** child board process |
| **`b`** | Overview | Backend picker (mock / claude / grok / cmd) |
| **`B`** | Overview | Cycle backend |
| **`R`** | Overview | Toggle review (`kuru set-review on\|off`) |
| **`?`** | Either | Help modal |
| `q` / Ctrl-C | Either | Quit |

---

## CLI options

```text
--repo <PATH>           Project with .kuru/  (default: .)
--plugin-dir <PATH>     Harness root (board/ + scripts/kuru.py)
--kuru-py <PATH>        Override path to kuru.py
--backend mock|claude|grok|cmd   Default for [s]tart  (default: mock)
--max-tries <N>         Try budget when starting a run
--check-contract        Pass --check-contract to board run
--backend-cmd <TMPL>    Template for --backend cmd
--run-dir <PATH>        Open a specific run (has events.ndjson)
--events <PATH>         Open a specific events.ndjson
--follow / --no-follow  Tail events (default: follow)
--wait-secs <N>         Wait for a run to appear (default: 30)
--dump                  Text snapshot, no raw mode / TTY
```

Env:

| Variable | Meaning |
|----------|---------|
| `BOARD_PLUGIN_DIR` | Plugin / harness root |
| `KURU_PY` | Path to `kuru.py` |
| `PYTHONPATH` | Set automatically when starting a run from the TUI |

---

## How it talks to the board runner

1. **View only** — tail latest (or given) `.kuru/runs/r_*/events.ndjson`.
2. **Start run (`s`)** — spawns:
   ```bash
   python3 -m board run -y \
     --backend <selected> \
     --repo <repo> \
     --plugin-dir <plugin> \
     --ui plain
   ```
   with `PYTHONPATH` / `KURU_PY` / `CLAUDE_PLUGIN_ROOT` set. Then follows the
   new run’s events.
3. **Stop (`S`/`x`)** — SIGTERM then SIGKILL on the child process group.
4. **Review (`R`)** — `python3 <kuru_py> set-review on|off` in the project repo.

After a run, the Python board also rewrites **`.kuru/BOARD_HANDOFF.md`** for a
new agent tab (orient + latest run paths). See that file + `.kuru/progress.md`.

---

## Building

### macOS / local dev (from source)

```bash
cd tui
cargo build --release
# binary: target/release/kuru-board-tui
```

Requires a local Rust toolchain (`rustup`). No Docker.

### Linux RHEL 9 / airgap (prebuilt x86_64 only)

RHEL 9 uses an older **glibc**. A binary built on a newer distro may fail with
`GLIBC_2.xx not found`. We build inside **Rocky Linux 9** (RHEL 9–class glibc).

```bash
# From the kurukuru **repo root** (not from tui/); network at build time
# Auto-detects: docker (if daemon up), Apple `container`, or podman
./scripts/build-tui-rhel9.sh

# Force a runtime:
DOCKER=docker ./scripts/build-tui-rhel9.sh
DOCKER=container ./scripts/build-tui-rhel9.sh
```
Produces:

```text
dist/kuru-board-tui-linux-amd64.tar.gz   # attach this to GitHub Release (.tar.gz)
dist/kuru-board-tui-linux-amd64          # bare ELF (no extension — normal for Unix)
dist/kuru-board-tui                      # short name, same binary
dist/SHA256SUMS
```
Dockerfile: [`Dockerfile.rhel9`](./Dockerfile.rhel9).

**Manual build** — two things that fail often:

1. **Context must be `tui/`** (so `COPY Cargo.toml` / `COPY src` work).  
   If you run from `tui/` with context `.` but the Dockerfile still said `COPY tui/src`, you get `"/tui/src": not found`.
2. **On Apple Silicon, pass `--platform linux/amd64`** (x86_64). Without it you build **arm64**, not RHEL9 x86_64.

```bash
# from repo root
docker build --platform linux/amd64 -f tui/Dockerfile.rhel9 -t kuru-board-tui:rhel9 tui/

# or from tui/
cd tui
docker build --platform linux/amd64 -f Dockerfile.rhel9 -t kuru-board-tui:rhel9 .

# extract
docker create --name kbt kuru-board-tui:rhel9
mkdir -p ../dist && docker cp kbt:/out/. ../dist/
docker rm kbt
```

(`container build` works the same way if that’s your CLI — still pass platform + context.)

**Apple Silicon + Docker QEMU issues:** if you see
`exec format error`, or `qemu: uncaught target signal 11`, or
`rustc … (error reading rustc version)` then a segfault — Docker’s amd64
emulation is broken or incomplete. Prefer Apple Container (this often works
when Docker does not):

```bash
DOCKER=container ./scripts/build-tui-rhel9.sh
```

Or enable Docker Desktop **Rosetta for x86_64/amd64** and rebuild with
`--no-cache`, or build on real x86_64 CI / a Linux box (no emulation).

#### Target triple

```text
x86_64-unknown-linux-gnu
  │       │        │    │
  │       │        │    └─ glibc (dynamic), not musl
  │       │        └────── Linux
  │       └─────────────── vendor placeholder (normal for Linux)
  └─────────────────────── 64-bit Intel/AMD only
```

There is **no** aarch64 Linux artifact in this pipeline. Apple Silicon Macs still
produce an **x86_64** Linux binary via `docker build --platform linux/amd64`.

#### Attach to a GitHub Release

```bash
./scripts/build-tui-rhel9.sh
gh release upload vX.Y.Z \
  dist/kuru-board-tui-linux-amd64.tar.gz \
  dist/SHA256SUMS
```

Or upload the same files in the GitHub web UI.

Suggested release blurb:

```text
### kuru-board-tui (Linux amd64)
- Asset: kuru-board-tui-linux-amd64.tar.gz
- Built on Rocky 9 (RHEL 9–compatible glibc)
- Airgap: tar -xzf … && chmod +x kuru-board-tui; copy harness plugin
- macOS: build from source in tui/
```

#### Airgap runtime (RHEL 9)

```bash
tar -xzf kuru-board-tui-linux-amd64.tar.gz
cd kuru-board-tui-linux-amd64
chmod +x kuru-board-tui

export PYTHONPATH=/opt/kuru/harness
export KURU_PY=/opt/kuru/harness/scripts/kuru.py

./kuru-board-tui \
  --repo /path/to/project \
  --plugin-dir /opt/kuru/harness \
  --backend mock
```
Inside the air gap, prefer **`mock`** or **`cmd`** with a local agent. Cloud
Claude/Grok only work if those CLIs and endpoints exist in the environment.

Verify before transfer:

```bash
file dist/kuru-board-tui-linux-amd64
ldd  dist/kuru-board-tui-linux-amd64
./dist/kuru-board-tui-linux-amd64 --help
```

### Smoke-test the Linux binary from a Mac

You **cannot** run the ELF on macOS. Run it inside Rocky 9 (amd64) instead:

```bash
# from repo root — uses docker/container + rockylinux:9
./scripts/test/smoke-tui-linux-amd64.sh

# force Apple Container if Docker QEMU is flaky:
DOCKER=container ./scripts/test/smoke-tui-linux-amd64.sh
```

Checks: `uname -m` is `x86_64`, `ldd` resolves, `--help` works, optional `--dump` against a local run fixture.

More detail: [`docs/airgap-tui.md`](../docs/airgap-tui.md).

---

## Fallback without this binary

Python-only (stdlib, no Rust):

```bash
python3 -m board run --ui plain …    # streaming event lines (default)
python3 -m board run --ui json …     # summary JSON only
# hierarchical board is this crate (scripts/board-tui.sh), not Python --ui board
```

---

## Project layout (crate)

```text
tui/
  Cargo.toml
  Cargo.lock
  Dockerfile.rhel9      # Rocky 9 → release Linux x86_64
  README.md             # this guide
  src/
    main.rs             # CLI, terminal setup
    app.rs              # event loop, keys, start/stop run
    ui.rs               # layout, modals, slice drill-in
    viewmodel.rs        # NDJSON events → board state
    events.rs           # event tail / run discovery
    config.rs           # RunConfig, backend enum, plugin discover
    control.rs          # spawn board run, set-review, kill child
```

---

## Related

| Path | Role |
|------|------|
| `../board/` | Python board orchestrator |
| `../scripts/board.sh` | `python3 -m board` launcher |
| `../scripts/board-tui.sh` | TUI launcher |
| `../scripts/build-tui-rhel9.sh` | Docker → `dist/` for GitHub releases |
| `../.kuru/BOARD_HANDOFF.md` (in target repos) | New-agent-tab briefing after board runs |
| `../impl/BOARD_RUNNER_PLAN.md` | Board runner design history |
