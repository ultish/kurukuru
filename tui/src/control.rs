//! Child process control: start/stop board runs, toggle review via kuru.py.

use crate::config::{Backend, RunConfig};
use crate::events::latest_run_dir;
use anyhow::{bail, Context, Result};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// Lifecycle of a board child spawned by the TUI.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RunStatus {
    Idle,
    Running { pid: u32 },
    Finished { code: Option<i32> },
}

impl RunStatus {
    pub fn label(&self) -> String {
        match self {
            RunStatus::Idle => "idle".into(),
            RunStatus::Running { pid } => format!("running (pid {pid})"),
            RunStatus::Finished { code: Some(c) } => format!("finished (exit {c})"),
            RunStatus::Finished { code: None } => "finished".into(),
        }
    }

    pub fn is_running(&self) -> bool {
        matches!(self, RunStatus::Running { .. })
    }
}

pub struct RunManager {
    child: Option<Child>,
    pub status: RunStatus,
    /// Run dir that existed before the last spawn (to detect a *new* run).
    pre_start_run: Option<PathBuf>,
    /// When we started waiting for a new run after spawn.
    wait_since: Option<Instant>,
}

impl Default for RunManager {
    fn default() -> Self {
        Self {
            child: None,
            status: RunStatus::Idle,
            pre_start_run: None,
            wait_since: None,
        }
    }
}

impl RunManager {
    pub fn is_running(&self) -> bool {
        self.status.is_running()
    }

    /// Poll child exit without blocking. Updates `status`.
    pub fn poll(&mut self) {
        let Some(child) = self.child.as_mut() else {
            return;
        };
        match child.try_wait() {
            Ok(Some(status)) => {
                let code = status.code();
                self.child = None;
                self.status = RunStatus::Finished { code };
                self.wait_since = None;
            }
            Ok(None) => {
                // still running — keep Running status (pid already set)
            }
            Err(_) => {
                self.child = None;
                self.status = RunStatus::Finished { code: None };
                self.wait_since = None;
            }
        }
    }

    /// Spawn `python3 -m board run -y --ui plain …`.
    pub fn start(&mut self, cfg: &RunConfig) -> Result<()> {
        if self.is_running() {
            bail!("a board run is already active (pid {:?})", self.pid());
        }

        if cfg.backend == Backend::Cmd && cfg.backend_cmd.as_ref().map(|s| s.is_empty()).unwrap_or(true)
        {
            bail!("backend cmd requires a --backend-cmd template (set via CLI)");
        }

        if !cfg.plugin_dir.join("board").is_dir() {
            bail!(
                "plugin_dir missing board/ package: {}",
                cfg.plugin_dir.display()
            );
        }
        if !cfg.kuru_py.is_file() {
            bail!("kuru.py not found: {}", cfg.kuru_py.display());
        }

        self.pre_start_run = latest_run_dir(&cfg.repo);
        self.wait_since = Some(Instant::now());

        let mut args = vec![
            "-m".to_string(),
            "board".to_string(),
            "run".to_string(),
            "-y".to_string(),
            "--backend".to_string(),
            cfg.backend.as_str().to_string(),
            "--repo".to_string(),
            cfg.repo.display().to_string(),
            "--plugin-dir".to_string(),
            cfg.plugin_dir.display().to_string(),
            "--ui".to_string(),
            "plain".to_string(),
            "--max-tries".to_string(),
            cfg.max_tries.to_string(),
        ];
        if cfg.check_contract {
            args.push("--check-contract".to_string());
        }
        if cfg.backend == Backend::Cmd {
            if let Some(ref tmpl) = cfg.backend_cmd {
                args.push("--backend-cmd".to_string());
                args.push(tmpl.clone());
            }
        }

        let mut cmd = Command::new(&cfg.python);
        cmd.args(&args)
            .current_dir(&cfg.repo)
            .env("PYTHONPATH", plugin_pythonpath(&cfg.plugin_dir))
            .env("KURU_PY", &cfg.kuru_py)
            .env("CLAUDE_PLUGIN_ROOT", &cfg.plugin_dir)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        // Own process group so stop can signal the whole tree.
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }

        let child = cmd
            .spawn()
            .with_context(|| format!("spawn {} -m board run", cfg.python))?;
        let pid = child.id();
        self.child = Some(child);
        self.status = RunStatus::Running { pid };
        Ok(())
    }

    pub fn pid(&self) -> Option<u32> {
        match self.status {
            RunStatus::Running { pid } => Some(pid),
            _ => self.child.as_ref().map(|c| c.id()),
        }
    }

    /// SIGTERM the process group, then SIGKILL after a short grace if still alive.
    pub fn stop(&mut self) -> Result<()> {
        let Some(child) = self.child.as_mut() else {
            if self.is_running() {
                self.status = RunStatus::Idle;
            }
            bail!("no board child process to stop");
        };

        let pid = child.id();
        #[cfg(unix)]
        {
            // Negative pid → process group (we set process_group(0) on spawn).
            let pgid = -(pid as i32);
            kill_pg(pgid, 15); // SIGTERM
            // Brief wait
            let deadline = Instant::now() + Duration::from_millis(800);
            loop {
                match child.try_wait() {
                    Ok(Some(status)) => {
                        self.child = None;
                        self.status = RunStatus::Finished {
                            code: status.code(),
                        };
                        self.wait_since = None;
                        return Ok(());
                    }
                    Ok(None) if Instant::now() < deadline => {
                        std::thread::sleep(Duration::from_millis(50));
                    }
                    Ok(None) => break,
                    Err(e) => {
                        self.child = None;
                        self.status = RunStatus::Finished { code: None };
                        bail!("wait after SIGTERM: {e}");
                    }
                }
            }
            kill_pg(pgid, 9); // SIGKILL
            let _ = child.try_wait();
        }
        #[cfg(not(unix))]
        {
            let _ = child.kill();
            let _ = child.wait();
        }

        self.child = None;
        self.status = RunStatus::Finished { code: None };
        self.wait_since = None;
        Ok(())
    }

    /// If a *new* run dir with `events.ndjson` appeared after start, return it
    /// and clear the wait flag.
    pub fn take_new_run_dir(&mut self, repo: &Path) -> Option<PathBuf> {
        if self.wait_since.is_none() {
            return None;
        }
        // Timeout so we don't wait forever if spawn failed silently.
        if let Some(since) = self.wait_since {
            if since.elapsed() > Duration::from_secs(60) {
                self.wait_since = None;
                return None;
            }
        }

        let latest = latest_run_dir(repo)?;
        let ev = latest.join("events.ndjson");
        if !ev.is_file() {
            return None;
        }

        let is_new = match &self.pre_start_run {
            None => true,
            Some(prev) => prev != &latest,
        };
        if !is_new {
            return None;
        }

        self.pre_start_run = Some(latest.clone());
        self.wait_since = None;
        Some(latest)
    }

    /// True while we may still be waiting for events.ndjson after spawn.
    pub fn waiting_for_run(&self) -> bool {
        self.wait_since.is_some()
    }
}

impl Drop for RunManager {
    fn drop(&mut self) {
        if self.is_running() {
            let _ = self.stop();
        }
    }
}

/// Signal a process group. `pgid` should be negative (e.g. `-1234` for group 1234).
#[cfg(unix)]
fn kill_pg(pgid: i32, sig: i32) {
    // No libc dependency: shell out to kill(1). Args: kill -TERM -- -pgid
    let _ = Command::new("kill")
        .args([
            format!("-{sig}"),
            "--".to_string(),
            format!("{pgid}"),
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

/// Prepend plugin dir to PYTHONPATH.
fn plugin_pythonpath(plugin_dir: &Path) -> String {
    let plugin = plugin_dir.display().to_string();
    match std::env::var("PYTHONPATH") {
        Ok(existing) if !existing.is_empty() => format!("{plugin}:{existing}"),
        _ => plugin,
    }
}

/// Run `python3 <kuru_py> set-review on|off` with cwd=repo.
pub fn set_review(cfg: &RunConfig, on: bool) -> Result<()> {
    let flag = if on { "on" } else { "off" };
    let out = Command::new(&cfg.python)
        .args([cfg.kuru_py.as_os_str(), std::ffi::OsStr::new("set-review"), std::ffi::OsStr::new(flag)])
        .current_dir(&cfg.repo)
        .env("KURU_PY", &cfg.kuru_py)
        .env("CLAUDE_PLUGIN_ROOT", &cfg.plugin_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .with_context(|| format!("run set-review {flag}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let stdout = String::from_utf8_lossy(&out.stdout);
        bail!(
            "set-review {flag} failed ({}): {}{}",
            out.status,
            err.trim(),
            if stdout.trim().is_empty() {
                String::new()
            } else {
                format!(" / {}", stdout.trim())
            }
        );
    }
    Ok(())
}

/// Read review policy via `kuru next --all --json` (`.review` on first object or meta).
pub fn read_review(cfg: &RunConfig) -> Result<Option<bool>> {
    if !cfg.kuru_py.is_file() {
        return Ok(None);
    }
    let out = Command::new(&cfg.python)
        .args([
            cfg.kuru_py.as_os_str(),
            std::ffi::OsStr::new("next"),
            std::ffi::OsStr::new("--all"),
            std::ffi::OsStr::new("--json"),
        ])
        .current_dir(&cfg.repo)
        .env("KURU_PY", &cfg.kuru_py)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .with_context(|| "kuru next --all --json")?;
    if !out.status.success() {
        // Fallback: ledger.json meta.review
        return read_review_from_ledger(&cfg.repo);
    }
    let text = String::from_utf8_lossy(&out.stdout);
    // next --all --json may be a list of slice objects with "review" field, or a wrapper.
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
        if let Some(b) = v.get("review").and_then(|x| x.as_bool()) {
            return Ok(Some(b));
        }
        if let Some(arr) = v.as_array() {
            for item in arr {
                if let Some(b) = item.get("review").and_then(|x| x.as_bool()) {
                    return Ok(Some(b));
                }
            }
        }
    }
    read_review_from_ledger(&cfg.repo)
}

fn read_review_from_ledger(repo: &Path) -> Result<Option<bool>> {
    let path = repo.join(".kuru").join("ledger.json");
    if !path.is_file() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&path)?;
    let v: serde_json::Value = serde_json::from_str(&text)?;
    Ok(v.pointer("/meta/review").and_then(|x| x.as_bool()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_status_labels() {
        assert_eq!(RunStatus::Idle.label(), "idle");
        assert!(RunStatus::Running { pid: 42 }.label().contains("42"));
    }

    #[test]
    fn pythonpath_prepends() {
        let p = plugin_pythonpath(Path::new("/plugin"));
        assert!(p.starts_with("/plugin"));
    }
}
