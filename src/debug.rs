//! Explicit source-only failure injection and local HLS preview management.
use crate::{process::Control, runtime::App};
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::{
    fs,
    path::PathBuf,
    sync::{atomic::Ordering, Arc},
};

#[derive(Clone, Copy)]
pub enum FaultKind {
    AudioDrop,
    VideoDrop,
    ReadError,
    SourcePause,
    SourceExit,
}
impl FaultKind {
    pub fn parse(value: &str) -> Result<Self> {
        Ok(match value {
            "audio_drop" => Self::AudioDrop,
            "video_drop" => Self::VideoDrop,
            "read_error" => Self::ReadError,
            "source_pause" => Self::SourcePause,
            "source_exit" => Self::SourceExit,
            _ => anyhow::bail!("Unknown source failure type"),
        })
    }
    pub fn name(self) -> &'static str {
        match self {
            Self::AudioDrop => "audio_drop",
            Self::VideoDrop => "video_drop",
            Self::ReadError => "read_error",
            Self::SourcePause => "source_pause",
            Self::SourceExit => "source_exit",
        }
    }
}
pub struct FaultRequest {
    pub pid: u32,
    pub kind: FaultKind,
}
#[derive(Default)]
pub struct FaultState {
    pub pending: Option<FaultRequest>,
    pub active: bool,
    pub last: Option<FaultKind>,
}
/// Scope cleanup prevents an injection queued for an old clip affecting its successor.
pub struct SourceScope {
    control: Arc<Control>,
    pid: u32,
}
impl SourceScope {
    pub fn new(control: Arc<Control>, pid: u32) -> Self {
        {
            let mut state = control.fault.lock().unwrap();
            state.pending = None;
            state.active = false;
            control.source_ready.store(false, Ordering::Release);
            control.source_pid.store(pid, Ordering::Release);
        }
        Self { control, pid }
    }
}
impl Drop for SourceScope {
    fn drop(&mut self) {
        let mut state = self.control.fault.lock().unwrap();
        if self.control.source_pid.load(Ordering::Acquire) == self.pid {
            self.control.source_ready.store(false, Ordering::Release);
            self.control.source_pid.store(0, Ordering::Release);
            state.pending = None;
            state.active = false;
        }
    }
}
pub fn control_status(control: &Control) -> Value {
    let fault = control.fault.lock().unwrap();
    json!({"source_pid":control.source_pid.load(Ordering::Acquire),
        "output_pid":control.output_pid.load(Ordering::Acquire),
        "source_ready":control.source_ready.load(Ordering::Acquire),
        "fault_pending":fault.pending.is_some(), "fault_active":fault.active,
        "last_fault":fault.last.map(FaultKind::name),
        "recoveries":control.recoveries.load(Ordering::Acquire)})
}
pub fn preview_dir(app: &App) -> PathBuf {
    app.dir.join("debug-preview")
}
fn is_segment(name: &str) -> bool {
    name.strip_prefix("segment-")
        .and_then(|v| v.strip_suffix(".ts"))
        .is_some_and(|v| v.len() >= 6 && v.len() <= 20 && v.bytes().all(|b| b.is_ascii_digit()))
}
pub fn prepare_preview(app: &App) -> Result<()> {
    let dir = preview_dir(app);
    if let Ok(metadata) = fs::symlink_metadata(&dir) {
        anyhow::ensure!(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "Local preview directory must be a real directory"
        );
    }
    fs::create_dir_all(&dir)?;
    for item in fs::read_dir(&dir)? {
        let item = item?;
        let name = item.file_name().to_string_lossy().into_owned();
        let plain = name.strip_suffix(".tmp").unwrap_or(&name);
        if (plain == "index.m3u8" || is_segment(plain)) && item.file_type()?.is_file() {
            fs::remove_file(item.path())?;
        }
    }
    Ok(())
}
pub fn preview_asset(app: &App, name: &str) -> Result<Option<(&'static str, Vec<u8>)>> {
    let kind = if name == "index.m3u8" {
        "application/vnd.apple.mpegurl"
    } else if is_segment(name) {
        "video/mp2t"
    } else {
        return Ok(None);
    };
    let path = preview_dir(app).join(name);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(meta) => meta,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    anyhow::ensure!(
        metadata.is_file() && metadata.len() <= 64 * 1024 * 1024,
        "Invalid preview asset"
    );
    Ok(Some((
        kind,
        fs::read(path).context("Read local preview asset")?,
    )))
}
