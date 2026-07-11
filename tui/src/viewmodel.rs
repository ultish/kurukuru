//! Pure NDJSON event → hierarchical board state (port of board/ui/viewmodel.py).

use serde_json::Value;
use std::collections::HashMap;

pub const STAGE_ORDER: &[&str] = &["check", "repair", "build", "verify", "review", "ship"];
pub const PIPELINE_BAR: &[&str] = &["check", "build", "verify", "review", "ship"];

#[derive(Debug, Clone, Default)]
pub struct AgentState {
    pub role: String,
    pub backend: String,
    pub pid: Option<i64>,
    pub alive: bool,
}

#[derive(Debug, Clone)]
pub struct StageState {
    pub name: String,
    /// pending | running | done | failed
    pub status: String,
    pub try_n: i64,
    pub ledger_status: Option<String>,
    pub elapsed_ms: Option<i64>,
    pub note: String,
    pub agent: Option<AgentState>,
}

impl StageState {
    fn new(name: &str) -> Self {
        Self {
            name: name.to_string(),
            status: "pending".into(),
            try_n: 0,
            ledger_status: None,
            elapsed_ms: None,
            note: String::new(),
            agent: None,
        }
    }
}

/// One execution of a stage (supports build↔verify loops as separate rows).
#[derive(Debug, Clone)]
pub struct StageAttempt {
    pub name: String,
    pub try_n: i64,
    /// running | done | failed
    pub status: String,
    pub ledger_status: Option<String>,
    pub elapsed_ms: Option<i64>,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct SliceState {
    pub id: String,
    pub title: String,
    pub mutex_target: String,
    pub depends_on: Vec<String>,
    pub ledger_status: String,
    pub wait_reason: Option<String>,
    pub wait_detail: String,
    pub outcome: Option<String>,
    pub outcome_reason: String,
    pub tries: i64,
    pub max_tries: i64,
    pub stages: HashMap<String, StageState>,
    /// Chronological stage attempts (including retries).
    pub attempts: Vec<StageAttempt>,
    pub started: bool,
    pub finished: bool,
}

impl SliceState {
    fn new(id: &str, max_tries: i64) -> Self {
        Self {
            id: id.to_string(),
            title: String::new(),
            mutex_target: "default".into(),
            depends_on: vec![],
            ledger_status: String::new(),
            wait_reason: None,
            wait_detail: String::new(),
            outcome: None,
            outcome_reason: String::new(),
            tries: 0,
            max_tries,
            stages: HashMap::new(),
            attempts: Vec::new(),
            started: false,
            finished: false,
        }
    }

    fn ensure_stage(&mut self, name: &str) -> &mut StageState {
        self.stages
            .entry(name.to_string())
            .or_insert_with(|| StageState::new(name))
    }

    fn push_attempt_start(&mut self, name: &str, try_n: i64) {
        self.attempts.push(StageAttempt {
            name: name.to_string(),
            try_n,
            status: "running".into(),
            ledger_status: None,
            elapsed_ms: None,
            note: String::new(),
        });
    }

    fn finish_last_attempt(
        &mut self,
        name: &str,
        status: &str,
        ledger: Option<String>,
        elapsed: Option<i64>,
        note: String,
    ) {
        if let Some(a) = self
            .attempts
            .iter_mut()
            .rev()
            .find(|a| a.name == name && a.status == "running")
        {
            a.status = status.into();
            a.ledger_status = ledger;
            a.elapsed_ms = elapsed;
            a.note = note;
            return;
        }
        // Defensive: no matching running attempt
        self.attempts.push(StageAttempt {
            name: name.to_string(),
            try_n: self.tries,
            status: status.into(),
            ledger_status: ledger,
            elapsed_ms: elapsed,
            note,
        });
    }

    /// Stages to show in drill list: chronological attempts, or skeleton if none yet.
    pub fn drill_stages(&self) -> Vec<StageAttempt> {
        if !self.attempts.is_empty() {
            return self.attempts.clone();
        }
        // Skeleton pipeline for not-yet-started slices
        PIPELINE_BAR
            .iter()
            .map(|name| StageAttempt {
                name: (*name).to_string(),
                try_n: 0,
                status: "pending".into(),
                ledger_status: None,
                elapsed_ms: None,
                note: String::new(),
            })
            .collect()
    }

    pub fn active_stage(&self) -> Option<&StageState> {
        for name in STAGE_ORDER.iter().rev() {
            if let Some(st) = self.stages.get(*name) {
                if st.status == "running" {
                    return Some(st);
                }
            }
        }
        None
    }

    pub fn live_agent(&self) -> Option<&AgentState> {
        for st in self.stages.values() {
            if let Some(a) = &st.agent {
                if a.alive {
                    return Some(a);
                }
            }
        }
        None
    }
}

#[derive(Debug, Clone)]
pub struct TargetState {
    pub expanded: bool,
    pub slice_ids: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct BoardState {
    pub run_id: String,
    pub review: Option<bool>,
    pub backend: String,
    pub max_tries: i64,
    pub started_ts: Option<String>,
    pub finished: bool,
    pub exit_code: Option<i64>,
    pub shipped: Vec<String>,
    pub capped: Vec<String>,
    pub target_order: Vec<String>,
    pub targets: HashMap<String, TargetState>,
    pub slices: HashMap<String, SliceState>,
    pub last_event_type: String,
    pub last_detail: String,
    pub commit_message: String,
    pub commit_ok: Option<bool>,
}

impl BoardState {
    pub fn new() -> Self {
        Self {
            max_tries: 2,
            ..Default::default()
        }
    }

    fn ensure_target(&mut self, key: &str) -> &mut TargetState {
        if !self.targets.contains_key(key) {
            self.targets.insert(
                key.to_string(),
                TargetState {
                    expanded: true,
                    slice_ids: vec![],
                },
            );
            self.target_order.push(key.to_string());
        }
        self.targets.get_mut(key).unwrap()
    }

    pub fn counts(&self) -> Counts {
        let mut c = Counts::default();
        c.total = self.slices.len();
        for sl in self.slices.values() {
            if sl.finished {
                match sl.outcome.as_deref() {
                    Some("shipped") => c.shipped += 1,
                    Some("capped") => c.capped += 1,
                    _ => c.stuck += 1,
                }
            } else if sl.started && sl.wait_reason.is_none() {
                c.running += 1;
            } else {
                c.waiting += 1;
            }
        }
        c
    }

    pub fn target_busy_slice(&self, key: &str) -> Option<&str> {
        let t = self.targets.get(key)?;
        for sid in &t.slice_ids {
            if let Some(sl) = self.slices.get(sid) {
                if sl.started && !sl.finished && sl.wait_reason.is_none() {
                    return Some(sid.as_str());
                }
            }
        }
        None
    }
}

#[derive(Debug, Clone, Default)]
pub struct Counts {
    pub running: usize,
    pub waiting: usize,
    pub shipped: usize,
    pub capped: usize,
    pub stuck: usize,
    pub total: usize,
}

#[derive(Debug, Clone)]
pub struct TreeRow {
    pub kind: String, // target | slice
    pub label: String,
    pub target: Option<String>,
    pub slice_id: Option<String>,
}

pub fn apply_event(state: &mut BoardState, event: &Value) {
    let t = event.get("type").and_then(|v| v.as_str()).unwrap_or("");
    state.last_event_type = t.to_string();
    if let Some(rid) = event.get("run_id").and_then(|v| v.as_str()) {
        state.run_id = rid.to_string();
    }

    match t {
        "run.planned" => on_planned(state, event),
        "run.started" => {
            if let Some(ts) = event.get("ts").and_then(|v| v.as_str()) {
                state.started_ts = Some(ts.to_string());
            }
            if let Some(b) = event.get("backend").and_then(|v| v.as_str()) {
                state.backend = b.to_string();
            }
            if let Some(r) = event.get("review") {
                state.review = r.as_bool();
            }
            state.last_detail = format!("run started ({})", state.backend);
        }
        "run.finished" => {
            state.finished = true;
            state.exit_code = event.get("exit_code").and_then(|v| v.as_i64());
            state.shipped = string_list(event.get("shipped"));
            state.capped = string_list(event.get("capped"));
            let c = state.counts();
            state.last_detail = format!(
                "run finished  shipped={} capped={} stuck={}  ({})",
                c.shipped,
                c.capped,
                c.stuck,
                state.shipped.join(",")
            );
        }
        "slice.started" => {
            let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(sl) = slice_mut(state, id) {
                sl.started = true;
                sl.wait_reason = None;
                sl.wait_detail.clear();
                if let Some(tgt) = event.get("target").and_then(|v| v.as_str()) {
                    // rehome simplified: just set target
                    sl.mutex_target = if tgt.is_empty() {
                        "default".into()
                    } else {
                        tgt.into()
                    };
                }
                state.last_detail = format!("{} started", id);
            }
        }
        "slice.waiting" => {
            let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(sl) = slice_mut(state, id) {
                sl.wait_reason = Some(
                    event
                        .get("reason")
                        .and_then(|v| v.as_str())
                        .unwrap_or("wait")
                        .to_string(),
                );
                sl.wait_detail = detail_to_string(event.get("detail"));
                state.last_detail = format!(
                    "{} waiting ({}: {})",
                    id,
                    sl.wait_reason.as_deref().unwrap_or("?"),
                    sl.wait_detail
                );
            }
        }
        "slice.finished" => {
            let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(sl) = slice_mut(state, id) {
                sl.finished = true;
                sl.outcome = Some(
                    event
                        .get("outcome")
                        .and_then(|v| v.as_str())
                        .unwrap_or("stuck")
                        .to_string(),
                );
                sl.outcome_reason = event
                    .get("reason")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if let Some(s) = event.get("status").and_then(|v| v.as_str()) {
                    sl.ledger_status = s.to_string();
                }
                if let Some(tr) = event.get("tries").and_then(|v| v.as_i64()) {
                    sl.tries = tr;
                }
                sl.wait_reason = None;
                for st in sl.stages.values_mut() {
                    if st.status == "running" {
                        st.status = if sl.outcome.as_deref() == Some("shipped") {
                            "done".into()
                        } else {
                            "failed".into()
                        };
                    }
                    if let Some(a) = &mut st.agent {
                        a.alive = false;
                    }
                }
                state.last_detail = format!(
                    "{} {}",
                    id,
                    sl.outcome.as_deref().unwrap_or("?")
                );
            }
        }
        "stage.started" => {
            let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let stage = event.get("stage").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(sl) = slice_mut(state, id) {
                if !stage.is_empty() {
                    let try_n = event
                        .get("try")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(sl.tries);
                    if let Some(tr) = event.get("try").and_then(|v| v.as_i64()) {
                        sl.tries = sl.tries.max(tr);
                    }
                    {
                        let st = sl.ensure_stage(stage);
                        st.status = "running".into();
                        st.try_n = try_n;
                    }
                    sl.push_attempt_start(stage, try_n);
                    sl.wait_reason = None;
                    state.last_detail = format!("{} {} …", id, stage);
                }
            }
        }
        "stage.finished" => {
            let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let stage = event.get("stage").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(sl) = slice_mut(state, id) {
                if !stage.is_empty() {
                    let ledger = event
                        .get("ledger_status")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string());
                    if let Some(ref l) = ledger {
                        sl.ledger_status = l.clone();
                    }
                    let elapsed = event.get("elapsed_ms").and_then(|v| v.as_i64());
                    let note = event
                        .get("note")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let exit = event.get("exit_code").and_then(|v| v.as_i64());
                    let note_l = note.to_lowercase();
                    let failed = !matches!(exit, None | Some(0))
                        || note_l.contains("reject")
                        || note_l.contains("blocked")
                        || note_l.contains("no_verdict")
                        || note_l.contains("flagged");
                    let status = if failed { "failed" } else { "done" };
                    {
                        let st = sl.ensure_stage(stage);
                        st.ledger_status = ledger.clone();
                        st.elapsed_ms = elapsed;
                        st.note = note.clone();
                        st.status = status.into();
                        if let Some(a) = &mut st.agent {
                            a.alive = false;
                        }
                    }
                    sl.finish_last_attempt(stage, status, ledger.clone(), elapsed, note);
                    state.last_detail = format!(
                        "{} {} → {}  ({}ms)",
                        id,
                        stage,
                        ledger.as_deref().unwrap_or("?"),
                        elapsed.unwrap_or(0)
                    );
                }
            }
        }
        "backend.spawn" => {
            let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let stage = event.get("stage").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(sl) = slice_mut(state, id) {
                if !stage.is_empty() {
                    let backend = event
                        .get("backend")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let role = event
                        .get("role")
                        .and_then(|v| v.as_str())
                        .unwrap_or(stage)
                        .to_string();
                    let pid = event.get("pid").and_then(|v| v.as_i64());
                    let st = sl.ensure_stage(stage);
                    if st.status == "pending" {
                        st.status = "running".into();
                    }
                    st.agent = Some(AgentState {
                        role: role.clone(),
                        backend,
                        pid,
                        alive: true,
                    });
                    state.last_detail = format!("{} agent {}", id, role);
                }
            }
        }
        "backend.exited" => {
            let id = event.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let stage = event.get("stage").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(sl) = slice_mut(state, id) {
                if !stage.is_empty() {
                    let st = sl.ensure_stage(stage);
                    if let Some(a) = &mut st.agent {
                        a.alive = false;
                        if let Some(pid) = event.get("pid").and_then(|v| v.as_i64()) {
                            a.pid = Some(pid);
                        }
                    }
                }
            }
        }
        "commit.started" => {
            state.commit_message = event
                .get("message")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            state.last_detail = format!("commit: {}", state.commit_message);
        }
        "commit.finished" => {
            state.commit_ok = event.get("ok").and_then(|v| v.as_bool());
            state.last_detail = format!(
                "commit: {}",
                if state.commit_ok == Some(true) {
                    "ok"
                } else {
                    "skip/fail"
                }
            );
        }
        _ => {}
    }
}

fn on_planned(state: &mut BoardState, event: &Value) {
    if let Some(r) = event.get("review") {
        state.review = r.as_bool();
    }
    if let Some(m) = event.get("max_tries").and_then(|v| v.as_i64()) {
        state.max_tries = m;
    }

    let bags: &[(&str, Option<&str>)] = &[
        ("actionable", None),
        ("waiting_deps", Some("deps")),
        ("blocked_at_start", Some("blocked_at_start")),
        ("draft", Some("draft")),
    ];

    for (bag, default_wait) in bags {
        let Some(arr) = event.get(*bag).and_then(|v| v.as_array()) else {
            continue;
        };
        for raw in arr {
            let Some(obj) = raw.as_object() else { continue };
            let sid = obj
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            if sid.is_empty() {
                continue;
            }
            let mut mt = obj
                .get("mutex_target")
                .or_else(|| obj.get("target"))
                .and_then(|v| v.as_str())
                .unwrap_or("default")
                .to_string();
            if mt.is_empty() || mt == "None" || mt == "null" {
                mt = "default".into();
            }
            let mut sl = SliceState::new(&sid, state.max_tries);
            sl.title = obj
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            sl.mutex_target = mt.clone();
            sl.depends_on = string_list(obj.get("depends_on").or_else(|| obj.get("unmet_deps")));
            sl.ledger_status = obj
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            match *default_wait {
                Some("deps") => {
                    sl.wait_reason = Some("deps".into());
                    sl.wait_detail = sl.depends_on.join(",");
                }
                Some("blocked_at_start") => {
                    sl.wait_reason = Some("blocked_at_start".into());
                    sl.outcome = Some("blocked".into());
                }
                Some("draft") => sl.wait_reason = Some("draft".into()),
                _ => {}
            }
            if obj.get("waiting_reason").and_then(|v| v.as_str()) == Some("deps") {
                sl.wait_reason = Some("deps".into());
            }
            state.slices.insert(sid.clone(), sl);
            let tgt = state.ensure_target(&mt);
            if !tgt.slice_ids.contains(&sid) {
                tgt.slice_ids.push(sid);
            }
        }
    }

    // Already-done slices (board run with empty actionable set still lists these).
    // Without this, a "nothing to do" run leaves the TUI blank.
    for sid in string_list(event.get("done_ids")) {
        if state.slices.contains_key(&sid) {
            if let Some(sl) = state.slices.get_mut(&sid) {
                sl.finished = true;
                sl.started = true;
                sl.outcome = Some("shipped".into());
                if sl.ledger_status.is_empty() {
                    sl.ledger_status = "done".into();
                }
            }
            continue;
        }
        let mut sl = SliceState::new(&sid, state.max_tries);
        sl.title = sid.clone();
        sl.ledger_status = "done".into();
        sl.finished = true;
        sl.started = true;
        sl.outcome = Some("shipped".into());
        sl.mutex_target = "default".into();
        state.slices.insert(sid.clone(), sl);
        let tgt = state.ensure_target("default");
        if !tgt.slice_ids.contains(&sid) {
            tgt.slice_ids.push(sid);
        }
    }

    let n_act = event
        .get("actionable")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let n_wait = event
        .get("waiting_deps")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let n_done = string_list(event.get("done_ids")).len();
    state.last_detail = if n_act == 0 && n_wait == 0 && n_done > 0 {
        format!(
            "planned: board clear — {n_done} already done (nothing to build). Press s after adding slices."
        )
    } else {
        format!(
            "planned: {} actionable, {} waiting, {} done, review={}",
            n_act,
            n_wait,
            n_done,
            if state.review == Some(true) {
                "on"
            } else {
                "off"
            }
        )
    };
}

fn slice_mut<'a>(state: &'a mut BoardState, sid: &str) -> Option<&'a mut SliceState> {
    if sid.is_empty() {
        return None;
    }
    let sid = if sid.to_uppercase().starts_with("SL-") {
        sid.to_uppercase()
    } else {
        sid.to_string()
    };
    if !state.slices.contains_key(&sid) {
        let sl = SliceState::new(&sid, state.max_tries);
        state.slices.insert(sid.clone(), sl);
        let tgt = state.ensure_target("default");
        if !tgt.slice_ids.contains(&sid) {
            tgt.slice_ids.push(sid.clone());
        }
    }
    state.slices.get_mut(&sid)
}

fn string_list(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(|x| {
                if let Some(s) = x.as_str() {
                    Some(s.to_string())
                } else if let Some(obj) = x.as_object() {
                    obj.get("id")
                        .and_then(|i| i.as_str())
                        .map(|s| s.to_string())
                } else {
                    None
                }
            })
            .collect(),
        _ => vec![],
    }
}

fn detail_to_string(v: Option<&Value>) -> String {
    match v {
        None => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(|x| x.as_str())
            .collect::<Vec<_>>()
            .join(","),
        Some(other) => other.to_string(),
    }
}

pub fn stage_glyph(st: Option<&StageState>) -> &'static str {
    match st {
        None => "·",
        Some(s) if s.status == "pending" => "·",
        Some(s) if s.status == "running" => "●",
        Some(s) if s.status == "failed" => "✗",
        _ => "✓",
    }
}

pub fn pipeline_bar(sl: &SliceState) -> String {
    let mut parts = Vec::new();
    for name in PIPELINE_BAR {
        let st = sl.stages.get(*name);
        if *name == "check" && (st.is_none() || st.unwrap().status == "pending") {
            continue;
        }
        parts.push(format!("{} {}", name, stage_glyph(st)));
    }
    if parts.is_empty() {
        "·".into()
    } else {
        parts.join("  ")
    }
}

pub fn slice_status_glyph(sl: &SliceState) -> &'static str {
    if sl.finished {
        return match sl.outcome.as_deref() {
            Some("shipped") => "■",
            Some("capped") => "▴",
            _ => "✗",
        };
    }
    if sl.started && sl.wait_reason.is_none() {
        "●"
    } else {
        "·"
    }
}

pub fn format_wait(sl: &SliceState) -> String {
    if sl.finished {
        return match sl.outcome.as_deref() {
            Some("shipped") => "shipped".into(),
            Some("capped") => {
                if sl.outcome_reason.is_empty() {
                    "capped".into()
                } else {
                    format!("capped: {}", sl.outcome_reason)
                }
            }
            other => format!("{}", other.unwrap_or("stuck")),
        };
    }
    if let Some(ref r) = sl.wait_reason {
        if r == "mutex" {
            return format!("waiting (mutex: {})", sl.wait_detail);
        }
        if r == "deps" {
            return format!("waiting (deps: {})", sl.wait_detail);
        }
        return format!("waiting ({r})");
    }
    if sl.started {
        format!("try {}/{}", sl.tries, sl.max_tries)
    } else {
        "queued".into()
    }
}

pub fn overview_rows(state: &BoardState, waiting_filter: bool) -> Vec<TreeRow> {
    let mut rows = Vec::new();
    for tkey in &state.target_order {
        let Some(tgt) = state.targets.get(tkey) else {
            continue;
        };
        let mut slice_ids: Vec<&String> = tgt.slice_ids.iter().collect();
        if waiting_filter {
            slice_ids.retain(|sid| {
                state
                    .slices
                    .get(*sid)
                    .map(|sl| is_blocker(sl))
                    .unwrap_or(false)
            });
            if slice_ids.is_empty() {
                continue;
            }
        }
        let busy = state.target_busy_slice(tkey);
        let lane = if let Some(b) = busy {
            format!("BUSY · {b}")
        } else {
            "IDLE".into()
        };
        let chev = if tgt.expanded { "▼" } else { "▶" };
        rows.push(TreeRow {
            kind: "target".into(),
            label: format!("{chev} target:{tkey}    {lane}"),
            target: Some(tkey.clone()),
            slice_id: None,
        });
        if !tgt.expanded {
            continue;
        }
        for sid in slice_ids {
            let Some(sl) = state.slices.get(sid) else {
                continue;
            };
            let g = slice_status_glyph(sl);
            let title = if sl.title.is_empty() {
                sid.as_str()
            } else {
                sl.title.as_str()
            };
            let wait_s = format_wait(sl);
            let bar = pipeline_bar(sl);
            let label = if bar == "·" {
                format!("{g} {sid}  {title}  ·  {wait_s}")
            } else {
                format!("{g} {sid}  {title}  ·  {bar}  ·  {wait_s}")
            };
            rows.push(TreeRow {
                kind: "slice".into(),
                label,
                target: Some(tkey.clone()),
                slice_id: Some(sid.clone()),
            });
        }
    }
    rows
}

fn is_blocker(sl: &SliceState) -> bool {
    if sl.finished {
        return matches!(sl.outcome.as_deref(), Some("capped" | "stuck" | "blocked"));
    }
    sl.wait_reason.is_some() || !sl.started
}
