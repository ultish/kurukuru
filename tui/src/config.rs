//! Run configuration for starting board runs from the TUI.

use std::env;
use std::path::{Path, PathBuf};

/// Backends accepted by `python3 -m board run --backend …`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    Mock,
    Claude,
    Grok,
    /// Custom shell template (`--backend cmd --backend-cmd …`).
    Cmd,
}

impl Backend {
    pub const ALL: [Backend; 4] = [
        Backend::Mock,
        Backend::Claude,
        Backend::Grok,
        Backend::Cmd,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Backend::Mock => "mock",
            Backend::Claude => "claude",
            Backend::Grok => "grok",
            Backend::Cmd => "cmd",
        }
    }

    /// Short label for UI (cmd shown as "cmd/profile").
    pub fn label(self) -> &'static str {
        match self {
            Backend::Mock => "mock",
            Backend::Claude => "claude",
            Backend::Grok => "grok",
            Backend::Cmd => "cmd (pi/profile)",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "mock" => Some(Backend::Mock),
            "claude" => Some(Backend::Claude),
            "grok" => Some(Backend::Grok),
            "cmd" | "pi" | "profile" => Some(Backend::Cmd),
            _ => None,
        }
    }

    pub fn next(self) -> Self {
        match self {
            Backend::Mock => Backend::Claude,
            Backend::Claude => Backend::Grok,
            Backend::Grok => Backend::Cmd,
            Backend::Cmd => Backend::Mock,
        }
    }
}

impl std::fmt::Display for Backend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Configuration held by the TUI for spawning `board run`.
#[derive(Debug, Clone)]
pub struct RunConfig {
    pub backend: Backend,
    pub repo: PathBuf,
    pub plugin_dir: PathBuf,
    pub kuru_py: PathBuf,
    pub max_tries: u32,
    pub check_contract: bool,
    /// Shell template for `--backend cmd` (required when backend is Cmd).
    pub backend_cmd: Option<String>,
    pub python: String,
    /// Workspace review policy cache (from set-review / next --json).
    pub review: Option<bool>,
}

impl RunConfig {
    pub fn new(repo: PathBuf, plugin_dir: PathBuf, kuru_py: Option<PathBuf>) -> Self {
        let kuru_py = kuru_py.unwrap_or_else(|| plugin_dir.join("scripts").join("kuru.py"));
        let python = env::var("PYTHON").unwrap_or_else(|_| "python3".into());
        Self {
            backend: Backend::Mock,
            repo,
            plugin_dir,
            kuru_py,
            max_tries: 2,
            check_contract: false,
            backend_cmd: None,
            python,
            review: None,
        }
    }

    pub fn backend_label(&self) -> String {
        match self.backend {
            Backend::Cmd => {
                if let Some(ref cmd) = self.backend_cmd {
                    let short: String = cmd.chars().take(24).collect();
                    if cmd.chars().count() > 24 {
                        format!("cmd:{short}…")
                    } else {
                        format!("cmd:{short}")
                    }
                } else {
                    "cmd".into()
                }
            }
            other => other.as_str().into(),
        }
    }

    pub fn review_label(&self) -> &'static str {
        match self.review {
            Some(true) => "on",
            Some(false) => "off",
            None => "?",
        }
    }
}

/// Resolve plugin root: `BOARD_PLUGIN_DIR` → walk from start → binary-relative.
pub fn discover_plugin_dir(start: &Path) -> Option<PathBuf> {
    if let Ok(env_dir) = env::var("BOARD_PLUGIN_DIR") {
        let p = PathBuf::from(env_dir);
        if is_plugin_root(&p) {
            return Some(canonicalize_soft(&p));
        }
    }

    // Walk upward from start (usually CWD or repo).
    let mut cur = canonicalize_soft(start);
    loop {
        if is_plugin_root(&cur) {
            return Some(cur);
        }
        if !cur.pop() {
            break;
        }
    }

    // Binary-adjacent: <bin>/../../ (tui/target/release → repo root) or <bin>/../
    if let Ok(exe) = env::current_exe() {
        if let Some(bin_dir) = exe.parent() {
            for up in [1usize, 2, 3, 4] {
                let mut p = bin_dir.to_path_buf();
                for _ in 0..up {
                    if !p.pop() {
                        break;
                    }
                }
                if is_plugin_root(&p) {
                    return Some(canonicalize_soft(&p));
                }
            }
            // Also: sibling of tui/ if binary is under tui/target/{debug,release}
            let mut p = bin_dir.to_path_buf();
            // release → target → tui → plugin
            for _ in 0..3 {
                if !p.pop() {
                    break;
                }
            }
            if is_plugin_root(&p) {
                return Some(canonicalize_soft(&p));
            }
        }
    }

    None
}

pub fn is_plugin_root(p: &Path) -> bool {
    p.join("board").is_dir() && p.join("scripts").join("kuru.py").is_file()
}

fn canonicalize_soft(p: &Path) -> PathBuf {
    p.canonicalize().unwrap_or_else(|_| p.to_path_buf())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backend_parse_and_cycle() {
        assert_eq!(Backend::parse("mock"), Some(Backend::Mock));
        assert_eq!(Backend::parse("PI"), Some(Backend::Cmd));
        assert_eq!(Backend::Mock.next(), Backend::Claude);
        assert_eq!(Backend::Cmd.next(), Backend::Mock);
        assert_eq!(Backend::Cmd.label(), "cmd (pi/profile)");
    }

    #[test]
    fn run_config_labels() {
        let mut cfg = RunConfig::new(
            PathBuf::from("/tmp/repo"),
            PathBuf::from("/tmp/plugin"),
            None,
        );
        assert_eq!(cfg.backend_label(), "mock");
        assert_eq!(cfg.review_label(), "?");
        cfg.backend = Backend::Cmd;
        cfg.backend_cmd = Some("pi run {prompt_file}".into());
        assert!(cfg.backend_label().starts_with("cmd:"));
        cfg.review = Some(true);
        assert_eq!(cfg.review_label(), "on");
    }
}
