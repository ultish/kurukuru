# scripts/

Production tooling for the kurukuru plugin. Test/smoke scripts live under
[`test/`](test/).

| Path | Role |
|------|------|
| `kuru.py` | Deterministic state + gate engine (**path is load-bearing** — keep at `scripts/kuru.py`) |
| `board.sh` | Launcher for `python3 -m board` |
| `board-tui.sh` | Launcher for the Ratatui board UI |
| `build-tui-rhel9.sh` | Container build → `dist/kuru-board-tui-linux-amd64.tar.gz` |
| `build-tui-macos.sh` | Native build → `dist/kuru-board-tui-macos-<arch>.tar.gz` |
| `test/selftest.sh` | Engine regression suite (must stay green) |
| `test/board-selftest.sh` | Board orchestrator + mock backend suite |
| `test/smoke-headless.sh` | Headless `claude -p` + `/kuru:*` bridge smoke |
| `test/smoke-tui-linux-amd64.sh` | Rocky 9 smoke for the Linux amd64 TUI binary |

Do not move `kuru.py` without updating every `${CLAUDE_PLUGIN_ROOT}/scripts/kuru.py`
reference in commands, agents, skills, and board launchers.
