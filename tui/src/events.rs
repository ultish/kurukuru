//! Read board NDJSON event streams (file tail / static load).

use anyhow::{Context, Result};
use serde_json::Value;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

/// Find the newest run dir under `.kuru/runs/`.
pub fn latest_run_dir(repo: &Path) -> Option<PathBuf> {
    let runs = repo.join(".kuru").join("runs");
    let mut best: Option<(std::time::SystemTime, PathBuf)> = None;
    let rd = std::fs::read_dir(&runs).ok()?;
    for ent in rd.flatten() {
        let p = ent.path();
        if !p.is_dir() {
            continue;
        }
        if !p.join("events.ndjson").is_file() {
            continue;
        }
        let meta = ent.metadata().ok()?;
        let m = meta.modified().or_else(|_| meta.created()).ok()?;
        if best.as_ref().map(|(t, _)| m > *t).unwrap_or(true) {
            best = Some((m, p));
        }
    }
    best.map(|(_, p)| p)
}

pub fn load_all_events(path: &Path) -> Result<Vec<Value>> {
    let f = File::open(path).with_context(|| format!("open {}", path.display()))?;
    let reader = BufReader::new(f);
    let mut out = Vec::new();
    for line in reader.lines() {
        let line = line?;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match serde_json::from_str::<Value>(line) {
            Ok(v) => out.push(v),
            Err(_) => continue,
        }
    }
    Ok(out)
}

/// Incremental NDJSON tailer.
pub struct EventTail {
    path: PathBuf,
    pos: u64,
    buf: String,
}

impl EventTail {
    /// Open a tailer starting at `pos` (0 = start; use file length to skip existing lines).
    pub fn open_at(path: impl Into<PathBuf>, pos: u64) -> Result<Self> {
        let path = path.into();
        Ok(Self {
            path,
            pos,
            buf: String::new(),
        })
    }

    /// Read any newly appended complete lines as JSON values.
    pub fn poll(&mut self) -> Result<Vec<Value>> {
        if !self.path.is_file() {
            return Ok(vec![]);
        }
        let mut f = File::open(&self.path)
            .with_context(|| format!("open {}", self.path.display()))?;
        let len = f.metadata()?.len();
        if len < self.pos {
            // file truncated / rotated
            self.pos = 0;
            self.buf.clear();
        }
        f.seek(SeekFrom::Start(self.pos))?;
        let mut chunk = String::new();
        f.read_to_string(&mut chunk)?;
        self.pos = f.stream_position()?;
        self.buf.push_str(&chunk);

        let mut out = Vec::new();
        while let Some(idx) = self.buf.find('\n') {
            let line = self.buf[..idx].trim().to_string();
            self.buf = self.buf[idx + 1..].to_string();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                out.push(v);
            }
        }
        Ok(out)
    }
}

pub fn log_path(run_dir: &Path, slice_id: &str, stage: &str) -> PathBuf {
    run_dir.join(slice_id).join(format!("{stage}.log"))
}

pub fn read_log_tail(path: &Path, max_lines: usize) -> Vec<String> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return vec!["(no log yet)".into()];
    };
    let lines: Vec<&str> = text.lines().collect();
    let start = lines.len().saturating_sub(max_lines);
    lines[start..]
        .iter()
        .map(|l| {
            let s = l.chars().take(200).collect::<String>();
            s
        })
        .collect()
}
