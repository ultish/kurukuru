//! kuru-board-tui — Ratatui master-detail viewer + control plane for Kurukuru board runs.
//!
//! Left: targets + slices. Right: detail + log tail.
//! Reads NDJSON from `.kuru/runs/<id>/events.ndjson` (live tail or snapshot).
//! Can spawn/stop `python3 -m board run` and toggle review via kuru.py.

mod app;
mod config;
mod control;
mod events;
mod ui;
mod viewmodel;

use crate::app::App;
use crate::config::{discover_plugin_dir, Backend, RunConfig};
use crate::events::{latest_run_dir, load_all_events, EventTail};
use anyhow::{bail, Context, Result};
use clap::Parser;
use crossterm::event::{DisableMouseCapture, EnableMouseCapture};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;
use std::io::{self, stdout};
use std::path::PathBuf;
use std::time::Duration;

#[derive(Parser, Debug)]
#[command(
    name = "kuru-board-tui",
    about = "Ratatui master-detail board for Kurukuru run events",
    long_about = "Watch or drive a board run:\n  \
        kuru-board-tui --repo . --backend mock\n  \
        # then press s to start (spawns board run --ui plain)\n\n\
        Or open a finished run:\n  \
        kuru-board-tui --run-dir .kuru/runs/r_abc --dump"
)]
struct Cli {
    /// Target repo containing .kuru/ (for --follow latest run / start run)
    #[arg(long, default_value = ".")]
    repo: PathBuf,

    /// Explicit run directory (contains events.ndjson)
    #[arg(long)]
    run_dir: Option<PathBuf>,

    /// Explicit events.ndjson path
    #[arg(long)]
    events: Option<PathBuf>,

    /// Follow/tail events (live). Default true when run dir exists.
    #[arg(long, default_value_t = true)]
    follow: bool,

    /// Do not follow; load file once and still allow browsing
    #[arg(long, default_value_t = false)]
    no_follow: bool,

    /// Wait up to N seconds for a run dir / events file to appear
    #[arg(long, default_value_t = 30)]
    wait_secs: u64,

    /// Print a text snapshot of the board (no TTY / raw mode) and exit
    #[arg(long, default_value_t = false)]
    dump: bool,

    /// Plugin / harness root (board/ + scripts/kuru.py). Default: BOARD_PLUGIN_DIR or discover.
    #[arg(long)]
    plugin_dir: Option<PathBuf>,

    /// Default backend for [s]tart: mock | claude | grok | cmd
    #[arg(long, default_value = "mock")]
    backend: String,

    /// Path to kuru.py (default: <plugin-dir>/scripts/kuru.py)
    #[arg(long)]
    kuru_py: Option<PathBuf>,

    /// Per-slice try budget when starting a run
    #[arg(long, default_value_t = 2)]
    max_tries: u32,

    /// Pass --check-contract when starting a run
    #[arg(long, default_value_t = false)]
    check_contract: bool,

    /// Shell template for --backend cmd (also used if backend=cmd at start)
    #[arg(long)]
    backend_cmd: Option<String>,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let follow = cli.follow && !cli.no_follow;

    let repo = cli.repo.canonicalize().unwrap_or_else(|_| cli.repo.clone());

    let plugin_dir = match &cli.plugin_dir {
        Some(p) => p.canonicalize().unwrap_or_else(|_| p.clone()),
        None => discover_plugin_dir(&repo)
            .or_else(|| discover_plugin_dir(&std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))))
            .context(
                "could not find plugin root (board/ + scripts/kuru.py); \
                 set --plugin-dir or BOARD_PLUGIN_DIR",
            )?,
    };

    let mut cfg = RunConfig::new(repo.clone(), plugin_dir, cli.kuru_py.clone());
    cfg.backend = Backend::parse(&cli.backend).ok_or_else(|| {
        anyhow::anyhow!(
            "unknown --backend {:?} (expected mock|claude|grok|cmd)",
            cli.backend
        )
    })?;
    cfg.max_tries = cli.max_tries;
    cfg.check_contract = cli.check_contract;
    cfg.backend_cmd = cli.backend_cmd.clone();

    let (run_dir, events_path) = resolve_paths(&cli, &repo)?;

    let initial = events_path
        .as_ref()
        .map(|p| load_all_events(p).unwrap_or_default())
        .unwrap_or_default();
    let meta_len = events_path
        .as_ref()
        .and_then(|p| std::fs::metadata(p).ok())
        .map(|m| m.len())
        .unwrap_or(0);
    let tail = if follow && !cli.dump {
        events_path
            .as_ref()
            .and_then(|p| EventTail::open_at(p, meta_len).ok())
    } else {
        None
    };

    let mut app = App::new(run_dir, tail, cfg, follow && !cli.dump);
    app.load_events(&initial);
    app.refresh_review();

    if cli.dump {
        if events_path.is_none() {
            bail!("--dump requires an existing run (--run-dir / --events / --repo with runs)");
        }
        dump_snapshot(&app.board);
        return Ok(());
    }

    if !crossterm::tty::IsTty::is_tty(&io::stdin()) {
        bail!("stdin is not a TTY; use an interactive terminal, or pass --dump for a text snapshot");
    }

    let mut terminal = setup_terminal()?;
    let res = app.run(&mut terminal);
    restore_terminal()?;
    res
}

fn dump_snapshot(board: &crate::viewmodel::BoardState) {
    use crate::viewmodel::overview_rows;
    let c = board.counts();
    println!(
        "kuru-board  ·  {}  ·  {}  ·  {}r {}w {}✓",
        board.run_id,
        board.backend,
        c.running,
        c.waiting,
        c.shipped
    );
    println!("{}", "─".repeat(60));
    for row in overview_rows(board, false) {
        let indent = if row.kind == "slice" { "  " } else { "" };
        println!("{indent}{}", row.label);
    }
    println!("{}", "─".repeat(60));
    println!("{}", board.last_detail);
}

/// Resolve optional run_dir + events path. Interactive mode may start with neither.
fn resolve_paths(cli: &Cli, repo: &std::path::Path) -> Result<(Option<PathBuf>, Option<PathBuf>)> {
    if let Some(ev) = &cli.events {
        let run_dir = ev
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from("."));
        if !ev.is_file() && cli.wait_secs > 0 {
            wait_for_file(ev, cli.wait_secs)?;
        }
        if !ev.is_file() {
            if cli.dump {
                bail!("events file not found: {}", ev.display());
            }
            return Ok((Some(run_dir), None));
        }
        return Ok((Some(run_dir), Some(ev.clone())));
    }
    if let Some(rd) = &cli.run_dir {
        let ev = rd.join("events.ndjson");
        if !ev.is_file() && cli.wait_secs > 0 {
            wait_for_file(&ev, cli.wait_secs)?;
        }
        if !ev.is_file() {
            if cli.dump {
                bail!("no events.ndjson in {}", rd.display());
            }
            return Ok((Some(rd.clone()), None));
        }
        return Ok((Some(rd.clone()), Some(ev)));
    }

    // --repo: latest run; for interactive allow idle if none.
    let deadline = std::time::Instant::now() + Duration::from_secs(cli.wait_secs);
    loop {
        if let Some(rd) = latest_run_dir(repo) {
            let ev = rd.join("events.ndjson");
            if ev.is_file() {
                return Ok((Some(rd), Some(ev)));
            }
        }
        if std::time::Instant::now() >= deadline {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }

    if cli.dump {
        bail!(
            "no board run found under {}/.kuru/runs/ (start a run first, or pass --run-dir / --events)",
            repo.display()
        );
    }

    // Interactive idle — user can press s to start.
    Ok((None, None))
}

fn wait_for_file(path: &std::path::Path, secs: u64) -> Result<()> {
    let deadline = std::time::Instant::now() + Duration::from_secs(secs);
    while std::time::Instant::now() < deadline {
        if path.is_file() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    bail!("timed out waiting for {}", path.display());
}

fn setup_terminal() -> Result<Terminal<CrosstermBackend<io::Stdout>>> {
    enable_raw_mode().context("enable raw mode")?;
    let mut out = stdout();
    execute!(out, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(out);
    Terminal::new(backend).context("create terminal")
}

fn restore_terminal() -> Result<()> {
    disable_raw_mode()?;
    execute!(stdout(), LeaveAlternateScreen, DisableMouseCapture)?;
    Ok(())
}
