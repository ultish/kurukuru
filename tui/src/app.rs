//! Event loop: poll NDJSON + draw master-detail UI + run control.

use crate::config::{Backend, RunConfig};
use crate::control::{self, RunManager};
use crate::events::{load_all_events, EventTail};
use crate::ui::{self, Focus, Modal, UiState};
use crate::viewmodel::{apply_event, overview_rows, BoardState};
use anyhow::Result;
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use ratatui::DefaultTerminal;
use std::path::PathBuf;
use std::time::{Duration, Instant};

pub struct App {
    pub board: BoardState,
    pub ui: UiState,
    pub run_dir: Option<PathBuf>,
    pub tail: Option<EventTail>,
    pub should_quit: bool,
    pub cfg: RunConfig,
    pub runs: RunManager,
    pub follow: bool,
}

impl App {
    pub fn new(
        run_dir: Option<PathBuf>,
        tail: Option<EventTail>,
        cfg: RunConfig,
        follow: bool,
    ) -> Self {
        Self {
            board: BoardState::new(),
            ui: UiState::default(),
            run_dir,
            tail,
            should_quit: false,
            cfg,
            runs: RunManager::default(),
            follow,
        }
    }

    pub fn load_events(&mut self, events: &[serde_json::Value]) {
        for ev in events {
            apply_event(&mut self.board, ev);
        }
        self.ensure_selection();
    }

    /// Refresh workspace review policy into cfg + board if missing.
    pub fn refresh_review(&mut self) {
        match control::read_review(&self.cfg) {
            Ok(Some(b)) => {
                self.cfg.review = Some(b);
                if self.board.review.is_none() {
                    self.board.review = Some(b);
                }
            }
            Ok(None) => {}
            Err(e) => {
                self.ui.status_msg = format!("review read: {e}");
            }
        }
    }

    fn ensure_selection(&mut self) {
        match &self.ui.focus {
            Focus::Overview => {
                let rows = overview_rows(&self.board, self.ui.waiting_filter);
                if rows.is_empty() {
                    self.ui.list_state.select(None);
                } else if self.ui.list_state.selected().is_none() {
                    self.ui.list_state.select(Some(0));
                } else if let Some(i) = self.ui.list_state.selected() {
                    if i >= rows.len() {
                        self.ui.list_state.select(Some(rows.len() - 1));
                    }
                }
            }
            Focus::Slice { id } => {
                let n = self
                    .board
                    .slices
                    .get(id)
                    .map(|s| s.drill_stages().len())
                    .unwrap_or(0);
                if n == 0 {
                    self.ui.stage_state.select(None);
                } else if self.ui.stage_state.selected().is_none() {
                    // Prefer last attempt (most recent stage)
                    self.ui.stage_state.select(Some(n - 1));
                } else if let Some(i) = self.ui.stage_state.selected() {
                    if i >= n {
                        self.ui.stage_state.select(Some(n - 1));
                    }
                }
            }
        }
    }

    pub fn poll_tail(&mut self) -> Result<()> {
        if let Some(tail) = &mut self.tail {
            let events = tail.poll()?;
            for ev in &events {
                apply_event(&mut self.board, ev);
            }
            if !events.is_empty() {
                self.ensure_selection();
            }
        }
        Ok(())
    }

    /// After spawn: attach to a new run dir when events.ndjson appears.
    fn poll_new_run(&mut self) {
        if !self.runs.waiting_for_run() {
            return;
        }
        if let Some(rd) = self.runs.take_new_run_dir(&self.cfg.repo) {
            self.attach_run_dir(rd);
        }
    }

    fn attach_run_dir(&mut self, rd: PathBuf) {
        let ev = rd.join("events.ndjson");
        // Reset board state for the new run
        self.board = BoardState::new();
        if let Ok(events) = load_all_events(&ev) {
            self.load_events(&events);
        }
        let meta_len = std::fs::metadata(&ev).map(|m| m.len()).unwrap_or(0);
        self.tail = if self.follow {
            EventTail::open_at(&ev, meta_len).ok()
        } else {
            None
        };
        self.run_dir = Some(rd.clone());
        self.ui.status_msg = format!("following {}", rd.display());
        // Sync backend display from board if present
        if !self.board.backend.is_empty() {
            if let Some(b) = Backend::parse(&self.board.backend) {
                self.cfg.backend = b;
            }
        }
        if let Some(r) = self.board.review {
            self.cfg.review = Some(r);
        }
    }

    pub fn run(&mut self, terminal: &mut DefaultTerminal) -> Result<()> {
        let tick = Duration::from_millis(100);
        let mut last = Instant::now();
        while !self.should_quit {
            terminal.draw(|f| {
                ui::draw(
                    f,
                    &self.board,
                    &mut self.ui,
                    self.run_dir.as_deref(),
                    &self.cfg,
                    &self.runs.status,
                )
            })?;

            let timeout = tick.saturating_sub(last.elapsed());
            if event::poll(timeout)? {
                if let Event::Key(key) = event::read()? {
                    if key.kind == KeyEventKind::Press {
                        self.on_key(key.code, key.modifiers);
                    }
                }
            }
            if last.elapsed() >= tick {
                self.runs.poll();
                self.poll_new_run();
                self.poll_tail()?;
                last = Instant::now();
            }
        }
        Ok(())
    }

    fn on_key(&mut self, code: KeyCode, mods: KeyModifiers) {
        // Global quit (never Esc)
        if matches!(code, KeyCode::Char('c')) && mods.contains(KeyModifiers::CONTROL) {
            self.should_quit = true;
            return;
        }
        if code == KeyCode::Char('q') && self.ui.modal == Modal::None {
            self.should_quit = true;
            return;
        }

        // Modal takes priority: Esc closes, etc.
        if self.ui.modal != Modal::None {
            self.on_key_modal(code);
            return;
        }

        // Global control keys (work in overview + slice)
        match code {
            KeyCode::Char('?') => {
                self.ui.modal = Modal::Help;
                return;
            }
            KeyCode::Char('b') => {
                let selected = Backend::ALL
                    .iter()
                    .position(|b| *b == self.cfg.backend)
                    .unwrap_or(0);
                self.ui.modal = Modal::BackendPicker { selected };
                return;
            }
            KeyCode::Char('B') => {
                self.cfg.backend = self.cfg.backend.next();
                self.ui.status_msg = format!("backend → {}", self.cfg.backend_label());
                return;
            }
            KeyCode::Char('R') => {
                self.toggle_review();
                return;
            }
            KeyCode::Char('s') => {
                self.ui.modal = Modal::ConfirmStart;
                return;
            }
            KeyCode::Char('S') | KeyCode::Char('x') => {
                self.stop_run();
                return;
            }
            _ => {}
        }

        match self.ui.focus.clone() {
            Focus::Overview => self.on_key_overview(code),
            Focus::Slice { id } => self.on_key_slice(code, &id),
        }
    }

    fn on_key_modal(&mut self, code: KeyCode) {
        match self.ui.modal.clone() {
            Modal::None => {}
            Modal::Help => {
                if matches!(
                    code,
                    KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q') | KeyCode::Char('?')
                ) {
                    self.ui.modal = Modal::None;
                }
            }
            Modal::BackendPicker { selected } => match code {
                KeyCode::Esc => {
                    self.ui.modal = Modal::None;
                }
                KeyCode::Char('j') | KeyCode::Down => {
                    let n = Backend::ALL.len();
                    let next = (selected + 1).min(n.saturating_sub(1));
                    self.ui.modal = Modal::BackendPicker { selected: next };
                }
                KeyCode::Char('k') | KeyCode::Up => {
                    let next = selected.saturating_sub(1);
                    self.ui.modal = Modal::BackendPicker { selected: next };
                }
                KeyCode::Enter | KeyCode::Char(' ') => {
                    if let Some(b) = Backend::ALL.get(selected) {
                        self.cfg.backend = *b;
                        self.ui.status_msg = format!("backend → {}", self.cfg.backend_label());
                        if *b == Backend::Cmd
                            && self
                                .cfg
                                .backend_cmd
                                .as_ref()
                                .map(|s| s.is_empty())
                                .unwrap_or(true)
                        {
                            self.ui.status_msg =
                                "backend → cmd (set --backend-cmd before start)".into();
                        }
                    }
                    self.ui.modal = Modal::None;
                }
                _ => {}
            },
            Modal::ConfirmStart => match code {
                KeyCode::Esc => {
                    self.ui.modal = Modal::None;
                    self.ui.status_msg = "start cancelled".into();
                }
                KeyCode::Enter | KeyCode::Char('y') | KeyCode::Char('s') => {
                    self.ui.modal = Modal::None;
                    self.start_run();
                }
                _ => {}
            },
        }
    }

    fn start_run(&mut self) {
        if self.runs.is_running() {
            self.ui.status_msg = "already running — press S/x to stop first".into();
            return;
        }
        match self.runs.start(&self.cfg) {
            Ok(()) => {
                let pid = self.runs.pid().unwrap_or(0);
                self.ui.status_msg = format!(
                    "started board run pid={pid} backend={} — waiting for events…",
                    self.cfg.backend_label()
                );
            }
            Err(e) => {
                self.ui.status_msg = format!("start failed: {e}");
            }
        }
    }

    fn stop_run(&mut self) {
        if !self.runs.is_running() {
            self.ui.status_msg = "no active board child".into();
            return;
        }
        match self.runs.stop() {
            Ok(()) => {
                self.ui.status_msg = "stopped board run".into();
            }
            Err(e) => {
                self.ui.status_msg = format!("stop: {e}");
            }
        }
    }

    fn toggle_review(&mut self) {
        let currently = self
            .cfg
            .review
            .or(self.board.review)
            .unwrap_or(true);
        let next = !currently;
        match control::set_review(&self.cfg, next) {
            Ok(()) => {
                self.cfg.review = Some(next);
                self.board.review = Some(next);
                self.ui.status_msg = format!(
                    "review {}",
                    if next { "on" } else { "off" }
                );
            }
            Err(e) => {
                self.ui.status_msg = format!("set-review failed: {e}");
            }
        }
    }

    fn on_key_overview(&mut self, code: KeyCode) {
        let rows = overview_rows(&self.board, self.ui.waiting_filter);
        let n = rows.len();
        match code {
            KeyCode::Esc => {
                // already overview, no modal — ignore (q quits)
            }
            KeyCode::Char('j') | KeyCode::Down => {
                let i = self.ui.list_state.selected().unwrap_or(0);
                if n > 0 {
                    self.ui.list_state.select(Some((i + 1).min(n - 1)));
                }
                self.ui.log_scroll = 0;
            }
            KeyCode::Char('k') | KeyCode::Up => {
                let i = self.ui.list_state.selected().unwrap_or(0);
                if n > 0 {
                    self.ui.list_state.select(Some(i.saturating_sub(1)));
                }
                self.ui.log_scroll = 0;
            }
            KeyCode::Enter => {
                if let Some(i) = self.ui.list_state.selected() {
                    if let Some(row) = rows.get(i) {
                        if row.kind == "target" {
                            if let Some(t) = row.target.as_ref() {
                                if let Some(tgt) = self.board.targets.get_mut(t) {
                                    tgt.expanded = !tgt.expanded;
                                }
                            }
                        } else if let Some(sid) = row.slice_id.clone() {
                            self.enter_slice(&sid);
                        }
                    }
                }
            }
            KeyCode::Char(' ') | KeyCode::Tab => {
                // Expand/collapse target only
                if let Some(i) = self.ui.list_state.selected() {
                    if let Some(row) = rows.get(i) {
                        if let Some(t) = row.target.as_ref() {
                            if let Some(tgt) = self.board.targets.get_mut(t) {
                                tgt.expanded = !tgt.expanded;
                            }
                        }
                    }
                }
            }
            KeyCode::Char('h') | KeyCode::Left => {
                if let Some(i) = self.ui.list_state.selected() {
                    if let Some(row) = rows.get(i) {
                        if let Some(t) = row.target.as_ref() {
                            if let Some(tgt) = self.board.targets.get_mut(t) {
                                tgt.expanded = false;
                            }
                        }
                    }
                }
            }
            KeyCode::Char('l') | KeyCode::Right => {
                if let Some(i) = self.ui.list_state.selected() {
                    if let Some(row) = rows.get(i) {
                        if row.kind == "slice" {
                            if let Some(sid) = row.slice_id.clone() {
                                self.enter_slice(&sid);
                                return;
                            }
                        }
                        if let Some(t) = row.target.as_ref() {
                            if let Some(tgt) = self.board.targets.get_mut(t) {
                                tgt.expanded = true;
                            }
                        }
                    }
                }
            }
            KeyCode::Char('w') => {
                self.ui.waiting_filter = !self.ui.waiting_filter;
                self.ui.list_state.select(Some(0));
                self.ui.status_msg = if self.ui.waiting_filter {
                    "filter: blockers only".into()
                } else {
                    "filter: all".into()
                };
            }
            KeyCode::PageDown | KeyCode::Char('J') => {
                self.ui.log_scroll = self.ui.log_scroll.saturating_add(3);
            }
            KeyCode::PageUp | KeyCode::Char('K') => {
                self.ui.log_scroll = self.ui.log_scroll.saturating_sub(3);
            }
            KeyCode::Char('r') => {
                if let Err(e) = self.poll_tail() {
                    self.ui.status_msg = format!("reload error: {e}");
                } else {
                    self.ui.status_msg = "reloaded events".into();
                }
            }
            _ => {}
        }
    }

    fn on_key_slice(&mut self, code: KeyCode, sid: &str) {
        let n = self
            .board
            .slices
            .get(sid)
            .map(|s| s.drill_stages().len())
            .unwrap_or(0);
        match code {
            KeyCode::Esc | KeyCode::Backspace | KeyCode::Char('h') | KeyCode::Left => {
                self.ui.focus = Focus::Overview;
                self.ui.log_scroll = 0;
                self.ui.status_msg = format!("left {sid}");
                self.ensure_selection();
            }
            KeyCode::Char('j') | KeyCode::Down => {
                let i = self.ui.stage_state.selected().unwrap_or(0);
                if n > 0 {
                    self.ui.stage_state.select(Some((i + 1).min(n - 1)));
                }
                self.ui.log_scroll = 0;
            }
            KeyCode::Char('k') | KeyCode::Up => {
                let i = self.ui.stage_state.selected().unwrap_or(0);
                if n > 0 {
                    self.ui.stage_state.select(Some(i.saturating_sub(1)));
                }
                self.ui.log_scroll = 0;
            }
            KeyCode::Enter | KeyCode::Char('l') | KeyCode::Right => {
                // Already viewing selected stage log — nudge status
                if let Some(sl) = self.board.slices.get(sid) {
                    let attempts = sl.drill_stages();
                    if let Some(i) = self.ui.stage_state.selected() {
                        if let Some(a) = attempts.get(i) {
                            self.ui.status_msg = format!(
                                "{} · {} · log .kuru/runs/…/{}/{}.log",
                                sid, a.name, sid, a.name
                            );
                        }
                    }
                }
            }
            KeyCode::PageDown | KeyCode::Char('J') => {
                self.ui.log_scroll = self.ui.log_scroll.saturating_add(5);
            }
            KeyCode::PageUp | KeyCode::Char('K') => {
                self.ui.log_scroll = self.ui.log_scroll.saturating_sub(5);
            }
            KeyCode::Char('r') => {
                if let Err(e) = self.poll_tail() {
                    self.ui.status_msg = format!("reload error: {e}");
                } else {
                    self.ui.status_msg = "reloaded events".into();
                    self.ensure_selection();
                }
            }
            KeyCode::Char('w') => {
                // ignore filter inside slice
            }
            _ => {}
        }
    }

    fn enter_slice(&mut self, sid: &str) {
        self.ui.focus = Focus::Slice { id: sid.to_string() };
        self.ui.log_scroll = 0;
        // Select last attempt (most recent stage in the pipeline / retry loop)
        let n = self
            .board
            .slices
            .get(sid)
            .map(|s| s.drill_stages().len())
            .unwrap_or(0);
        if n > 0 {
            self.ui.stage_state.select(Some(n - 1));
        } else {
            self.ui.stage_state.select(Some(0));
        }
        self.ui.status_msg = format!("opened {sid} — j/k stages, Esc back");
    }
}
