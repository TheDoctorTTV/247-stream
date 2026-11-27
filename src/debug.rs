//! Explicit source-only failure injection and local HLS preview management.
use crate::{process::Control, runtime::App};
use anyhow::Result;
use serde_json::{json, Value};
use std::{
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
    pub advance: Option<u32>,
    pub skippable: bool,
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
    pub fn new(control: Arc<Control>, pid: u32, skippable: bool) -> Self {
        {
            let mut state = control.fault.lock().unwrap();
            state.advance = None;
            state.skippable = skippable;
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
            state.advance = None;
            state.skippable = false;
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
        "can_skip":control.local_preview && !control.stopped() && fault.skippable
            && control.source_ready.load(Ordering::Acquire) && fault.advance.is_none()
            && fault.pending.is_none() && !fault.active,
        "last_fault":fault.last.map(FaultKind::name),
        "recoveries":control.recoveries.load(Ordering::Acquire)})
}
pub fn preview_dir(app: &App) -> PathBuf {
    app.dir.join("debug-preview")
}
pub fn prepare_preview(app: &App) -> Result<()> {
    crate::output::prepare_hls(&preview_dir(app), "Local preview")
}
pub fn preview_asset(app: &App, name: &str) -> Result<Option<(&'static str, Vec<u8>)>> {
    crate::output::hls_asset(&preview_dir(app), name)
}
