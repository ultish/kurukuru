//! Master-detail Ratatui layout: left task/stage list, right detail + log + modals.

use crate::config::{Backend, RunConfig};
use crate::control::RunStatus;
use crate::events::{log_path, read_log_tail};
use crate::viewmodel::{
    format_wait, overview_rows, pipeline_bar, stage_glyph, BoardState, StageAttempt, STAGE_ORDER,
    TreeRow,
};
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Focus {
    /// Browse targets + slices
    Overview,
    /// Inside a slice: navigate stage attempts, view each log
    Slice { id: String },
}

/// Centered modal overlays (Esc closes).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Modal {
    None,
    Help,
    BackendPicker { selected: usize },
    ConfirmStart,
    Error(String),
}

pub struct UiState {
    pub list_state: ListState,
    pub stage_state: ListState,
    pub focus: Focus,
    pub waiting_filter: bool,
    pub log_scroll: u16,
    pub status_msg: String,
    pub modal: Modal,
}

impl Default for UiState {
    fn default() -> Self {
        let mut list_state = ListState::default();
        list_state.select(Some(0));
        let mut stage_state = ListState::default();
        stage_state.select(Some(0));
        Self {
            list_state,
            stage_state,
            focus: Focus::Overview,
            waiting_filter: false,
            log_scroll: 0,
            status_msg: String::new(),
            modal: Modal::None,
        }
    }
}

pub fn draw(
    frame: &mut Frame,
    board: &BoardState,
    ui: &mut UiState,
    run_dir: Option<&Path>,
    cfg: &RunConfig,
    run_status: &RunStatus,
) {
    let root = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Min(5),
            Constraint::Length(1),
        ])
        .split(frame.area());

    draw_header(frame, root[0], board, ui, cfg, run_status);
    match &ui.focus {
        Focus::Overview => {
            let rows = overview_rows(board, ui.waiting_filter);
            clamp_list(&mut ui.list_state, rows.len());
            draw_overview_body(frame, root[1], board, ui, &rows, run_dir);
        }
        Focus::Slice { id } => {
            let id = id.clone();
            draw_slice_body(frame, root[1], board, ui, &id, run_dir);
        }
    }
    draw_footer(frame, root[2], board, ui, run_status);

    match &ui.modal {
        Modal::None => {}
        Modal::Help => draw_help_modal(frame),
        Modal::BackendPicker { selected } => draw_backend_picker(frame, *selected, cfg),
        Modal::ConfirmStart => draw_confirm_start(frame, cfg, run_status),
        Modal::Error(msg) => draw_error_modal(frame, msg),
    }
}

fn clamp_list(state: &mut ListState, n: usize) {
    if n == 0 {
        state.select(None);
    } else {
        let sel = state.selected().unwrap_or(0).min(n - 1);
        state.select(Some(sel));
    }
}

fn draw_header(
    frame: &mut Frame,
    area: Rect,
    board: &BoardState,
    ui: &UiState,
    cfg: &RunConfig,
    run_status: &RunStatus,
) {
    let c = board.counts();
    // Prefer live board event review; fall back to RunConfig cache.
    let review = match board.review.or(cfg.review) {
        Some(true) => "on",
        Some(false) => "off",
        None => "?",
    };
    let run_id = if board.run_id.is_empty() {
        "—"
    } else {
        &board.run_id
    };
    let backend = cfg.backend_label();
    let status = run_status.label();
    let mode = match &ui.focus {
        Focus::Overview => "overview",
        Focus::Slice { .. } => "slice",
    };
    // Compact header: kuru-board · r_… · mock · review on · idle · [s]tart …
    let title = format!(
        " kuru-board · {run_id} · {backend} · review {review} · {status} · {mode} · {}r {}w {}✓ · [s]tart [b]ackend [R]eview ? ",
        c.running, c.waiting, c.shipped,
    );
    let p = Paragraph::new(title).style(
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
    );
    frame.render_widget(p, area);
}

fn draw_footer(
    frame: &mut Frame,
    area: Rect,
    board: &BoardState,
    ui: &UiState,
    run_status: &RunStatus,
) {
    let keys = if ui.modal != Modal::None {
        " Esc close · Enter confirm · j/k move "
    } else {
        match &ui.focus {
            Focus::Overview => {
                " j/k · Enter slice · s start · S/x stop · b backend · R review · ? help · q quit"
            }
            Focus::Slice { .. } => {
                " j/k stage · Esc back · s start · S/x stop · ? help · q quit"
            }
        }
    };
    let filt = if ui.waiting_filter {
        " [w:blockers]"
    } else {
        ""
    };
    let run_hint = if run_status.is_running() {
        " ●"
    } else {
        ""
    };
    let msg = if ui.status_msg.is_empty() {
        board.last_detail.as_str()
    } else {
        ui.status_msg.as_str()
    };
    let line = format!("{keys}{filt}{run_hint}  │  {msg}");
    let p = Paragraph::new(line).style(Style::default().fg(Color::DarkGray));
    frame.render_widget(p, area);
}

fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let popup = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup[1])[1]
}

fn dim_overlay(frame: &mut Frame) {
    // Full-area dark layer so the centered modal reads as an overlay.
    frame.render_widget(
        Paragraph::new("").style(Style::default().bg(Color::Rgb(0, 0, 0))),
        frame.area(),
    );
}

fn draw_help_modal(frame: &mut Frame) {
    dim_overlay(frame);
    let area = centered_rect(70, 70, frame.area());
    frame.render_widget(Clear, area);

    let lines = vec![
        Line::from(Span::styled(
            " Kurukuru board — keybinds ",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        Line::from(Span::styled(
            "Overview",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from("  j/k  ↓/↑     move selection"),
        Line::from("  Enter / l    open slice stages + logs"),
        Line::from("  Space / Tab  expand/collapse target"),
        Line::from("  h/←  l/→     collapse / expand or open"),
        Line::from("  w            filter blockers only"),
        Line::from("  r            reload / poll events"),
        Line::from("  PgUp/PgDn    scroll log preview"),
        Line::from(""),
        Line::from(Span::styled(
            "Slice drill-in",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from("  j/k          select stage attempt"),
        Line::from("  Enter / l    focus log for stage"),
        Line::from("  Esc / h      back to overview"),
        Line::from(""),
        Line::from(Span::styled(
            "Run control",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from("  s            start board run (confirm)"),
        Line::from("  S / x        stop board child (SIGTERM→KILL)"),
        Line::from("  b            backend picker (mock|claude|grok|cmd)"),
        Line::from("  B            cycle backend"),
        Line::from("  R            toggle review on/off (kuru set-review)"),
        Line::from(""),
        Line::from(Span::styled(
            "Global",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from("  ?            this help"),
        Line::from("  Esc          close modal (then leave slice)"),
        Line::from("  q / Ctrl-C   quit (does not stop a detached run)"),
        Line::from(""),
        Line::from(Span::styled(
            " Esc to close ",
            Style::default().fg(Color::DarkGray),
        )),
    ];

    let block = Block::default()
        .borders(Borders::ALL)
        .title(" help ")
        .border_style(Style::default().fg(Color::Cyan));
    let p = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(p, area);
}

fn draw_backend_picker(frame: &mut Frame, selected: usize, cfg: &RunConfig) {
    dim_overlay(frame);
    let area = centered_rect(50, 40, frame.area());
    frame.render_widget(Clear, area);

    let mut lines = vec![
        Line::from(Span::styled(
            " Select backend ",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
    ];
    for (i, b) in Backend::ALL.iter().enumerate() {
        let marker = if i == selected { "› " } else { "  " };
        let cur = if *b == cfg.backend { " (current)" } else { "" };
        let style = if i == selected {
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(Color::Gray)
        };
        lines.push(Line::from(Span::styled(
            format!("{marker}{}{cur}", b.label()),
            style,
        )));
    }
    lines.push(Line::from(""));
    if cfg.backend == Backend::Cmd {
        let tmpl = cfg
            .backend_cmd
            .as_deref()
            .unwrap_or("(set --backend-cmd on CLI)");
        lines.push(Line::from(Span::styled(
            format!(" cmd template: {tmpl}"),
            Style::default().fg(Color::DarkGray),
        )));
    }
    lines.push(Line::from(Span::styled(
        " j/k move · Enter select · Esc cancel ",
        Style::default().fg(Color::DarkGray),
    )));

    let block = Block::default()
        .borders(Borders::ALL)
        .title(" backend ")
        .border_style(Style::default().fg(Color::Yellow));
    frame.render_widget(Paragraph::new(lines).block(block), area);
}

fn draw_confirm_start(frame: &mut Frame, cfg: &RunConfig, run_status: &RunStatus) {
    dim_overlay(frame);
    let area = centered_rect(60, 45, frame.area());
    frame.render_widget(Clear, area);

    let review = cfg.review_label();
    let warn = if run_status.is_running() {
        "  ⚠ a run is already active — stop it first (S/x)"
    } else {
        ""
    };
    let cmd_note = if cfg.backend == Backend::Cmd {
        format!(
            "  cmd: {}",
            cfg.backend_cmd.as_deref().unwrap_or("(missing!)")
        )
    } else {
        String::new()
    };

    let lines = vec![
        Line::from(Span::styled(
            " Start board run? ",
            Style::default()
                .fg(Color::Green)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        Line::from(format!("  backend:  {}", cfg.backend_label())),
        Line::from(format!("  review:   {review}")),
        Line::from(format!("  repo:     {}", cfg.repo.display())),
        Line::from(format!("  plugin:   {}", cfg.plugin_dir.display())),
        Line::from(format!("  max_tries:{}  check_contract:{}", cfg.max_tries, cfg.check_contract)),
        Line::from(cmd_note),
        Line::from(Span::styled(
            warn.to_string(),
            Style::default().fg(Color::Red),
        )),
        Line::from(""),
        Line::from("  Spawns: python3 -m board run -y --ui plain …"),
        Line::from("  TUI will follow the new .kuru/runs/r_* events."),
        Line::from(""),
        Line::from(Span::styled(
            " Enter confirm · Esc cancel ",
            Style::default().fg(Color::DarkGray),
        )),
    ];

    let block = Block::default()
        .borders(Borders::ALL)
        .title(" confirm start ")
        .border_style(Style::default().fg(Color::Green));
    frame.render_widget(Paragraph::new(lines).block(block), area);
}

fn draw_error_modal(frame: &mut Frame, msg: &str) {
    dim_overlay(frame);
    let area = centered_rect(75, 60, frame.area());
    frame.render_widget(Clear, area);

    let mut lines = vec![
        Line::from(Span::styled(
            " board run failed to start ",
            Style::default()
                .fg(Color::Red)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
    ];
    lines.extend(msg.lines().map(|l| Line::from(l.to_string())));
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        " Esc / Enter to close ",
        Style::default().fg(Color::DarkGray),
    )));

    let block = Block::default()
        .borders(Borders::ALL)
        .title(" error ")
        .border_style(Style::default().fg(Color::Red));
    let p = Paragraph::new(lines)
        .block(block)
        .wrap(Wrap { trim: false });
    frame.render_widget(p, area);
}

fn draw_overview_body(
    frame: &mut Frame,
    area: Rect,
    board: &BoardState,
    ui: &mut UiState,
    rows: &[TreeRow],
    run_dir: Option<&Path>,
) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(40), Constraint::Percentage(60)])
        .split(area);

    let items: Vec<ListItem> = if rows.is_empty() {
        vec![ListItem::new(Line::from(Span::styled(
            if board.finished || !board.last_detail.is_empty() {
                "(no slices in this run — board may already be clear; press s after new slices)"
            } else {
                "(no run loaded yet — press s to start, or wait for events)"
            },
            Style::default().fg(Color::DarkGray),
        )))]
    } else {
        rows.iter()
            .map(|r| {
                let style = row_style(board, r);
                ListItem::new(Line::from(Span::styled(r.label.clone(), style)))
            })
            .collect()
    };

    let list = List::new(items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" slices · Enter to open ")
                .border_style(Style::default().fg(Color::DarkGray)),
        )
        .highlight_style(
            Style::default()
                .bg(Color::Rgb(40, 40, 55))
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        )
        .highlight_symbol("› ");

    frame.render_stateful_widget(list, cols[0], &mut ui.list_state);

    let selected = ui.list_state.selected().and_then(|i| rows.get(i));
    // Preview: if slice selected, show stages + latest log; pick last attempt
    let stage_hint = selected
        .and_then(|r| r.slice_id.as_ref())
        .and_then(|sid| board.slices.get(sid))
        .and_then(|sl| sl.attempts.last().map(|a| a.name.as_str()));
    draw_detail_panel(
        frame,
        cols[1],
        board,
        selected,
        stage_hint,
        run_dir,
        ui.log_scroll,
        " detail (Enter = full stage list) ",
    );
}

fn draw_slice_body(
    frame: &mut Frame,
    area: Rect,
    board: &BoardState,
    ui: &mut UiState,
    sid: &str,
    run_dir: Option<&Path>,
) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(42), Constraint::Percentage(58)])
        .split(area);

    let Some(sl) = board.slices.get(sid) else {
        let p = Paragraph::new(format!("slice {sid} not found")).block(
            Block::default()
                .borders(Borders::ALL)
                .title(" stages "),
        );
        frame.render_widget(p, area);
        return;
    };

    let attempts = sl.drill_stages();
    clamp_list(&mut ui.stage_state, attempts.len());

    let items: Vec<ListItem> = attempts
        .iter()
        .map(|a| ListItem::new(Line::from(Span::styled(attempt_label(a), attempt_style(a)))))
        .collect();

    let title = format!(" {sid} stages · Esc back ");
    let list = List::new(items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(title)
                .border_style(Style::default().fg(Color::Cyan)),
        )
        .highlight_style(
            Style::default()
                .bg(Color::Rgb(40, 40, 55))
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        )
        .highlight_symbol("› ");

    frame.render_stateful_widget(list, cols[0], &mut ui.stage_state);

    let sel_attempt = ui
        .stage_state
        .selected()
        .and_then(|i| attempts.get(i));

    draw_stage_detail(frame, cols[1], sl, sel_attempt, run_dir, ui.log_scroll);
}

fn attempt_label(a: &StageAttempt) -> String {
    let g = match a.status.as_str() {
        "running" => "●",
        "failed" => "✗",
        "done" => "✓",
        _ => "·",
    };
    let try_s = if a.try_n > 0 {
        format!(" try {}", a.try_n)
    } else {
        String::new()
    };
    let el = a
        .elapsed_ms
        .map(|ms| format!("  {ms}ms"))
        .unwrap_or_default();
    let led = a
        .ledger_status
        .as_ref()
        .map(|s| format!(" → {s}"))
        .unwrap_or_default();
    let note = if a.note.is_empty() {
        String::new()
    } else {
        let short: String = a.note.chars().take(40).collect();
        format!("  {short}")
    };
    format!("{g} {}{}{}{}{}", a.name, try_s, led, el, note)
}

fn attempt_style(a: &StageAttempt) -> Style {
    match a.status.as_str() {
        "running" => Style::default().fg(Color::Cyan),
        "failed" => Style::default().fg(Color::Red),
        "done" => Style::default().fg(Color::Green),
        _ => Style::default().fg(Color::DarkGray),
    }
}

fn draw_stage_detail(
    frame: &mut Frame,
    area: Rect,
    sl: &crate::viewmodel::SliceState,
    attempt: Option<&StageAttempt>,
    run_dir: Option<&Path>,
    log_scroll: u16,
) {
    let block = Block::default()
        .borders(Borders::ALL)
        .title(" stage log ")
        .border_style(Style::default().fg(Color::DarkGray));
    let inner = block.inner(area);
    frame.render_widget(block, area);

    let panes = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(8), Constraint::Min(3)])
        .split(inner);

    let mut meta = vec![
        Line::from(vec![
            Span::styled(
                sl.id.clone(),
                Style::default()
                    .fg(Color::White)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw("  "),
            Span::styled(sl.title.clone(), Style::default().fg(Color::Gray)),
        ]),
        Line::from(format!(
            "ledger: {}  ·  {}  ·  pipeline: {}",
            if sl.ledger_status.is_empty() {
                "—"
            } else {
                &sl.ledger_status
            },
            format_wait(sl),
            pipeline_bar(sl)
        )),
    ];

    if let Some(a) = attempt {
        meta.push(Line::from(Span::styled(
            format!(
                "selected: {}{}  status={}",
                a.name,
                if a.try_n > 0 {
                    format!(" try {}", a.try_n)
                } else {
                    String::new()
                },
                a.status
            ),
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        )));
        if let Some(ref led) = a.ledger_status {
            meta.push(Line::from(format!("ledger after stage: {led}")));
        }
        if !a.note.is_empty() {
            meta.push(Line::from(format!("note: {}", a.note)));
        }
        if let Some(ms) = a.elapsed_ms {
            meta.push(Line::from(format!("elapsed: {ms}ms")));
        }
        // show agent from latest StageState for this name
        if let Some(st) = sl.stages.get(&a.name) {
            if let Some(ag) = &st.agent {
                meta.push(Line::from(format!(
                    "agent: {}  backend={}  pid={}",
                    ag.role,
                    ag.backend,
                    ag.pid
                        .map(|p| p.to_string())
                        .unwrap_or_else(|| "—".into())
                )));
            }
        }
    } else {
        meta.push(Line::from("Select a stage on the left."));
    }

    frame.render_widget(Paragraph::new(meta).wrap(Wrap { trim: false }), panes[0]);

    let stage_name = attempt.map(|a| a.name.as_str()).unwrap_or("build");
    let mut log_lines: Vec<Line<'static>> = Vec::new();
    if let Some(rd) = run_dir {
        let path = log_path(rd, &sl.id, stage_name);
        log_lines.push(Line::from(Span::styled(
            format!(
                "{} · {}  (latest log for this stage name)",
                stage_name,
                path.display()
            ),
            Style::default().fg(Color::DarkGray),
        )));
        if a_is_pending(attempt) {
            log_lines.push(Line::from(Span::styled(
                "(stage not run yet — no log)",
                Style::default().fg(Color::DarkGray),
            )));
        } else {
            for line in read_log_tail(&path, 120) {
                log_lines.push(Line::from(line));
            }
        }
    } else {
        log_lines.push(Line::from("(no run dir — press s to start)"));
    }

    let log = Paragraph::new(log_lines)
        .block(
            Block::default()
                .borders(Borders::TOP)
                .title(format!(" log · {stage_name} "))
                .border_style(Style::default().fg(Color::DarkGray)),
        )
        .wrap(Wrap { trim: false })
        .scroll((log_scroll, 0));
    frame.render_widget(log, panes[1]);
}

fn a_is_pending(a: Option<&StageAttempt>) -> bool {
    matches!(a.map(|x| x.status.as_str()), Some("pending") | None)
}

fn draw_detail_panel(
    frame: &mut Frame,
    area: Rect,
    board: &BoardState,
    selected: Option<&TreeRow>,
    stage_for_log: Option<&str>,
    run_dir: Option<&Path>,
    log_scroll: u16,
    title: &str,
) {
    let block = Block::default()
        .borders(Borders::ALL)
        .title(title)
        .border_style(Style::default().fg(Color::DarkGray));
    let inner = block.inner(area);
    frame.render_widget(block, area);

    let panes = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(12), Constraint::Min(3)])
        .split(inner);

    let (meta_lines, log_lines) = match selected {
        None => (
            vec![
                Line::from("Select a slice on the left. Press Enter to open stages."),
                Line::from(Span::styled(
                    "No run yet? Press s to start a board run.",
                    Style::default().fg(Color::DarkGray),
                )),
            ],
            vec![Line::from("")],
        ),
        Some(row) if row.kind == "target" => {
            let tkey = row.target.as_deref().unwrap_or("?");
            let busy = board.target_busy_slice(tkey).unwrap_or("—");
            let n = board
                .targets
                .get(tkey)
                .map(|t| t.slice_ids.len())
                .unwrap_or(0);
            (
                vec![
                    Line::from(Span::styled(
                        format!("target:{tkey}"),
                        Style::default()
                            .fg(Color::Blue)
                            .add_modifier(Modifier::BOLD),
                    )),
                    Line::from(format!("slices: {n}")),
                    Line::from(format!("busy: {busy}")),
                    Line::from(""),
                    Line::from(Span::styled(
                        "Select a slice and press Enter to inspect stages + logs.",
                        Style::default().fg(Color::DarkGray),
                    )),
                ],
                vec![],
            )
        }
        Some(row) => {
            let sid = row.slice_id.as_deref().unwrap_or("");
            detail_for_slice_preview(board, sid, stage_for_log, run_dir)
        }
    };

    frame.render_widget(
        Paragraph::new(meta_lines).wrap(Wrap { trim: false }),
        panes[0],
    );

    let log = Paragraph::new(log_lines)
        .block(
            Block::default()
                .borders(Borders::TOP)
                .title(" log (latest for active stage) ")
                .border_style(Style::default().fg(Color::DarkGray)),
        )
        .wrap(Wrap { trim: false })
        .scroll((log_scroll, 0));
    frame.render_widget(log, panes[1]);
}

fn detail_for_slice_preview(
    board: &BoardState,
    sid: &str,
    stage_for_log: Option<&str>,
    run_dir: Option<&Path>,
) -> (Vec<Line<'static>>, Vec<Line<'static>>) {
    let Some(sl) = board.slices.get(sid) else {
        return (vec![Line::from(format!("unknown slice {sid}"))], vec![]);
    };

    let mut meta = Vec::new();
    meta.push(Line::from(vec![
        Span::styled(
            sl.id.clone(),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw("  "),
        Span::styled(sl.title.clone(), Style::default().fg(Color::Gray)),
    ]));
    meta.push(Line::from(format!(
        "target={}  ·  deps={}  ·  try {}/{}",
        sl.mutex_target,
        if sl.depends_on.is_empty() {
            "none".into()
        } else {
            sl.depends_on.join(",")
        },
        sl.tries,
        sl.max_tries
    )));
    meta.push(Line::from(format!(
        "ledger: {}  ·  {}",
        if sl.ledger_status.is_empty() {
            "—"
        } else {
            &sl.ledger_status
        },
        format_wait(sl)
    )));
    meta.push(Line::from(format!("pipeline: {}", pipeline_bar(sl))));
    meta.push(Line::from(Span::styled(
        "Stages (Enter to open & pick logs):",
        Style::default().fg(Color::DarkGray),
    )));

    // Chronological attempts if any, else compact glyphs
    if sl.attempts.is_empty() {
        let mut stage_spans = Vec::new();
        for name in STAGE_ORDER {
            let st = sl.stages.get(*name);
            if *name == "check" || *name == "repair" {
                if st.is_none() || st.unwrap().status == "pending" {
                    continue;
                }
            }
            let g = stage_glyph(st);
            let color = match st.map(|s| s.status.as_str()) {
                Some("running") => Color::Cyan,
                Some("done") => Color::Green,
                Some("failed") => Color::Red,
                _ => Color::DarkGray,
            };
            stage_spans.push(Span::styled(
                format!("{g}{name} "),
                Style::default().fg(color),
            ));
        }
        if stage_spans.is_empty() {
            meta.push(Line::from(Span::styled(
                "(no stages yet)",
                Style::default().fg(Color::DarkGray),
            )));
        } else {
            meta.push(Line::from(stage_spans));
        }
    } else {
        for a in &sl.attempts {
            meta.push(Line::from(Span::styled(
                attempt_label(a),
                attempt_style(a),
            )));
        }
    }

    if let Some(ag) = sl.live_agent() {
        meta.push(Line::from(Span::styled(
            format!(
                "agent: {}  backend={}  pid={}  ● live",
                ag.role,
                ag.backend,
                ag.pid
                    .map(|p| p.to_string())
                    .unwrap_or_else(|| "—".into())
            ),
            Style::default().fg(Color::Magenta),
        )));
    }

    let stage_name = stage_for_log.unwrap_or_else(|| {
        sl.active_stage()
            .map(|s| s.name.as_str())
            .or_else(|| sl.attempts.last().map(|a| a.name.as_str()))
            .unwrap_or("build")
    });

    let mut log_lines: Vec<Line<'static>> = Vec::new();
    if let Some(rd) = run_dir {
        let path = log_path(rd, &sl.id, stage_name);
        log_lines.push(Line::from(Span::styled(
            format!("{stage_name} · {}", path.display()),
            Style::default().fg(Color::DarkGray),
        )));
        for line in read_log_tail(&path, 80) {
            log_lines.push(Line::from(line));
        }
    } else {
        log_lines.push(Line::from("(no run dir — press s to start)"));
    }

    (meta, log_lines)
}

fn row_style(board: &BoardState, row: &TreeRow) -> Style {
    if row.kind == "target" {
        return Style::default().fg(Color::Blue);
    }
    if let Some(sid) = &row.slice_id {
        if let Some(sl) = board.slices.get(sid) {
            if sl.finished {
                return match sl.outcome.as_deref() {
                    Some("shipped") => Style::default().fg(Color::Green),
                    Some("capped") => Style::default().fg(Color::Yellow),
                    _ => Style::default().fg(Color::Red),
                };
            }
            if sl.started && sl.wait_reason.is_none() {
                return Style::default().fg(Color::Cyan);
            }
            if sl.wait_reason.is_some() {
                return Style::default().fg(Color::DarkGray);
            }
        }
    }
    Style::default().fg(Color::Gray)
}
