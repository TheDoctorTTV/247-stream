//! Owns application state, worker lifetime, and update/restart coordination.
use crate::{
    config::{self, StreamConfig},
    process::Control,
    state::{SharedState, State},
    updates,
    worker::Worker,
};
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::Instant,
};
struct Running {
    control: Arc<Control>,
    thread: JoinHandle<()>,
}
pub struct App {
    pub dir: PathBuf,
    pub started: Instant,
    pub shutdown_reason: Mutex<&'static str>,
    pub development: bool,
    pub state: SharedState,
    config: Mutex<Value>,
    worker: Mutex<Option<Running>>,
    pub provision_lock: Mutex<()>,
    pub fallback_image_lock: Mutex<()>,
    pub app_job: Mutex<Value>,
    pub binary_job: Mutex<Value>,
    pub shutdown: AtomicBool,
    pub restart: AtomicBool,
}
impl App {
    pub fn new(dir: PathBuf, development: bool) -> Result<Arc<Self>> {
        fs::create_dir_all(&dir)?;
        let cfg = config::load(&dir)?;
        let mut state = State::default();
        state.files(&dir, &cfg, true)?;
        Ok(Arc::new(Self {
            dir,
            started: Instant::now(),
            shutdown_reason: Mutex::new("shutdown requested"),
            development,
            state: Mutex::new(state),
            config: Mutex::new(cfg),
            worker: Mutex::new(None),
            provision_lock: Mutex::new(()),
            fallback_image_lock: Mutex::new(()),
            app_job: Mutex::new(updates::empty_job()),
            binary_job: Mutex::new(updates::empty_job()),
            shutdown: AtomicBool::new(false),
            restart: AtomicBool::new(false),
        }))
    }
    pub fn settings(&self) -> Value {
        config::normalize(&self.config.lock().unwrap())
    }
    pub fn raw_settings(&self) -> Value {
        self.config.lock().unwrap().clone()
    }
    pub fn log(&self, line: &str) {
        self.state.lock().unwrap().log(line, "");
    }
    pub fn register_secret(&self, secret: &str) {
        self.state.lock().unwrap().register_secret(secret);
    }
    pub fn debug_enabled(&self) -> bool {
        self.state.lock().unwrap().debug
    }
    pub fn debug(&self, message: &str) {
        self.log(&format!("[DEBUG] {message}"));
    }
    pub fn ffmpeg_log(&self, context: &str, line: &str) {
        self.state
            .lock()
            .unwrap()
            .ffmpeg_log(&format!("({context}) {line}"));
    }
    pub fn sanitize(&self, message: &str) -> String {
        self.state.lock().unwrap().sanitize(message)
    }
    pub fn status(&self, status: &str) {
        {
            let mut state = self.state.lock().unwrap();
            state.status = status.into();
            state.updated = crate::state::now();
        }
        self.log(&format!("[STATUS] {status}"));
    }
    pub fn is_streaming(&self) -> bool {
        self.state.lock().unwrap().streaming
    }
    pub fn save_settings(&self, payload: &Value) -> Result<Value> {
        let previous = self.settings();
        let debug_before = self.debug_enabled();
        let normalized = {
            let mut cfg = self.config.lock().unwrap();
            let merged = config::merge(&cfg, payload)?;
            config::save(&self.dir, &merged)?;
            *cfg = merged;
            config::normalize(&cfg)
        };
        self.state
            .lock()
            .unwrap()
            .files(&self.dir, &normalized, false)?;
        {
            let mut state = self.state.lock().unwrap();
            state.meta["source"] = normalized["playlist_url"].clone();
            state.meta["resolution"] = normalized["resolution"].clone();
            state.meta["fps"] = normalized["framerate"].clone();
        }
        if ["log_to_file", "ffmpeg_log_to_file"]
            .iter()
            .any(|key| previous[*key] != normalized[*key])
        {
            self.log(&format!(
                "[INFO] File logging configured: app={}, FFmpeg={}, directory=logs/.",
                config::boolean(&normalized, "log_to_file", false),
                config::boolean(&normalized, "ffmpeg_log_to_file", false)
            ));
        }
        if debug_before != self.debug_enabled() {
            self.log(if self.debug_enabled() {
                "[INFO] Debug logging enabled."
            } else {
                "[INFO] Debug logging disabled."
            });
        }
        if self.is_streaming() {
            self.log("[INFO] Web settings saved. Streaming changes apply on next start; logging changes apply immediately.");
        }
        Ok(normalized)
    }
    pub fn remember_update_selection(&self, channel: &str, version: &str) -> Result<()> {
        let mut cfg = self.config.lock().unwrap();
        let mut next = cfg.clone();
        next["app_update_channel"] = json!(channel);
        next["app_update_selected_version"] = json!(version);
        config::save(&self.dir, &next)?;
        *cfg = next;
        Ok(())
    }
    pub fn start(self: &Arc<Self>) -> Result<()> {
        self.start_mode(false, None)
    }
    pub fn start_preview(self: &Arc<Self>, source: Option<String>) -> Result<()> {
        self.start_mode(true, source)
    }
    fn start_mode(
        self: &Arc<Self>,
        local_preview: bool,
        preview_source: Option<String>,
    ) -> Result<()> {
        let mut running = self.worker.lock().unwrap();
        if running.as_ref().is_some_and(|r| !r.thread.is_finished()) {
            anyhow::ensure!(!local_preview, "Stop the current stream before starting a local test; it will not be interrupted automatically");
            return Ok(());
        }
        if let Some(old) = running.take() {
            let _ = old.thread.join();
        }
        anyhow::ensure!(
            !config::boolean(&self.binary_job.lock().unwrap(), "running", false),
            "Wait for the binary operation to finish before starting"
        );
        let mut cfg = StreamConfig::new(&self.raw_settings(), &self.dir);
        if local_preview {
            crate::debug::prepare_preview(self)?;
            cfg.local_preview = true;
            if let Some(source) = preview_source {
                cfg.source = source.trim().to_owned();
            }
            cfg.base = "preview://local".into();
            cfg.key.clear();
        }
        anyhow::ensure!(!cfg.source.is_empty(), "Select a source before starting");
        anyhow::ensure!(
            local_preview || !cfg.base.is_empty(),
            "Set a streaming destination before starting"
        );
        if !local_preview {
            let destination =
                reqwest::Url::parse(&cfg.base).context("Invalid streaming destination URL")?;
            anyhow::ensure!(
                matches!(destination.scheme(), "rtmp" | "rtmps")
                    && destination.host_str().is_some(),
                "Streaming destinations must use RTMP or RTMPS, including custom platforms"
            );
        }
        if !local_preview && (cfg.twitch() || cfg.youtube()) {
            anyhow::ensure!(
                !cfg.key.is_empty(),
                "Set a stream key for the selected destination"
            );
        }
        {
            let settings = self.settings();
            let mut state = self.state.lock().unwrap();
            state.files(&self.dir, &settings, true)?;
            state.streaming = true;
            state.status = "Starting".into();
            state.meta = json!({"mode":if local_preview{"local_preview"}else{"headless"},"source":cfg.source,"resolution":format!("{}p",cfg.height),"fps":cfg.fps.to_string()});
        }
        let control = Arc::new(Control {
            local_preview,
            preview_id: if local_preview {
                crate::state::now().to_bits()
            } else {
                0
            },
            ..Control::default()
        });
        let worker_control = Arc::clone(&control);
        let app = Arc::clone(self);
        self.log(&format!(
            "[INFO] Starting {}: app version {}...",
            if local_preview {
                "local preview of the selected source (no publication to a destination)"
            } else {
                "stream (headless)"
            },
            crate::VERSION
        ));
        let handle = thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                Worker::run(Arc::clone(&app), cfg, Arc::clone(&worker_control))
            }));
            let was_stopped = worker_control.stopped();
            worker_control.stop.store(true, Ordering::Release);
            match result {
                Ok(Err(e)) if !was_stopped => {
                    app.log(&format!("[ERROR] Stream worker failed: {e:#}"))
                }
                Err(_) => app.log("[ERROR] Stream worker panicked."),
                _ => {}
            }
            {
                let mut state = app.state.lock().unwrap();
                state.streaming = false;
                state.status = "Stopped".into();
                state.updated = crate::state::now();
            }
            app.log("[INFO] Worker finished.");
        });
        *running = Some(Running {
            control,
            thread: handle,
        });
        Ok(())
    }
    pub fn debug_status(&self) -> Value {
        let running = self.worker.lock().unwrap();
        let Some(worker) = running.as_ref().filter(|w| !w.thread.is_finished()) else {
            return json!({"running":false,"local_preview":false,"source_ready":false});
        };
        let mut info = crate::debug::control_status(&worker.control);
        info["running"] = json!(!worker.control.stopped());
        info["local_preview"] = json!(worker.control.local_preview);
        info["preview_id"] = json!(worker.control.preview_id.to_string());
        info["preview_ready"] = json!(
            worker.control.local_preview
                && crate::debug::preview_dir(self).join("index.m3u8").is_file()
        );
        info["preview_url"] = if worker.control.local_preview {
            json!("/api/debug/preview/index.m3u8")
        } else {
            Value::Null
        };
        info
    }
    pub fn inject_failure(&self, payload: &Value) -> Result<Value> {
        let kind = crate::debug::FaultKind::parse(&config::text(payload, "kind", ""))?;
        let running = self.worker.lock().unwrap();
        let worker = running
            .as_ref()
            .filter(|w| !w.thread.is_finished())
            .context("No stream is running")?;
        let control = &worker.control;
        let mut fault = control.fault.lock().unwrap();
        let pid = control.source_pid.load(Ordering::Acquire);
        anyhow::ensure!(
            !control.stopped() && pid != 0 && control.source_ready.load(Ordering::Acquire),
            "Wait for an active source using persistent output"
        );
        if let Some(value) = payload.get("source_pid") {
            let expected = value
                .as_u64()
                .context("source_pid must be a positive integer")?;
            anyhow::ensure!(
                expected == pid as u64,
                "Source changed; refresh debug status and retry"
            );
        }
        anyhow::ensure!(
            fault.pending.is_none() && !fault.active && fault.advance.is_none(),
            "A fault is already pending or active; wait for source recovery"
        );
        fault.pending = Some(crate::debug::FaultRequest { pid, kind });
        self.log(&format!(
            "[WARN] Debug source failure queued: kind={}, source_pid={pid}.",
            kind.name()
        ));
        Ok(json!({"queued":true,"kind":kind.name(),"source_pid":pid}))
    }
    pub fn skip_preview(&self, payload: &Value) -> Result<()> {
        let expected = payload["source_pid"]
            .as_u64()
            .context("source_pid is required")?;
        let running = self.worker.lock().unwrap();
        let worker = running
            .as_ref()
            .filter(|w| !w.thread.is_finished())
            .context("No local test stream is running")?;
        let control = &worker.control;
        let mut fault = control.fault.lock().unwrap();
        anyhow::ensure!(
            control.local_preview && !control.stopped(),
            "No local test stream is running"
        );
        let pid = control.source_pid.load(Ordering::Acquire);
        anyhow::ensure!(
            pid != 0
                && pid as u64 == expected
                && control.source_ready.load(Ordering::Acquire)
                && fault.skippable,
            "Wait for an active playlist item in the local test stream"
        );
        anyhow::ensure!(
            fault.advance.is_none() && fault.pending.is_none() && !fault.active,
            "A debug action is already pending; wait for the source to advance or recover"
        );
        fault.advance = Some(pid);
        self.log("[INFO] Local test skip requested; playing the transition before the next item.");
        Ok(())
    }
    pub fn stop_preview(&self) -> Result<()> {
        {
            let running = self.worker.lock().unwrap();
            anyhow::ensure!(
                running
                    .as_ref()
                    .is_some_and(|w| !w.thread.is_finished() && w.control.local_preview),
                "No local test stream is running"
            );
            running
                .as_ref()
                .unwrap()
                .control
                .stop
                .store(true, Ordering::Release);
        }
        self.status("Stopping local preview");
        Ok(())
    }
    pub fn stop(&self) {
        if let Some(running) = self.worker.lock().unwrap().as_ref() {
            running.control.stop.store(true, Ordering::Release);
        }
        if self.is_streaming() {
            self.status("Stopping");
            self.log("[INFO] Stop requested — stopping current stream...");
        }
    }
    pub fn skip(&self) {
        if let Some(running) = self.worker.lock().unwrap().as_ref() {
            running.control.skip.store(true, Ordering::Release);
        }
        self.log("[INFO] Skip requested — advancing to next item...");
    }
    pub fn join_worker(&self) {
        let running = self.worker.lock().unwrap().take();
        if let Some(running) = running {
            running.control.stop.store(true, Ordering::Release);
            let _ = running.thread.join();
        }
    }
    pub fn progress(&self, is_app: bool, message: &str, percent: i64) {
        let lock = if is_app {
            &self.app_job
        } else {
            &self.binary_job
        };
        let mut job = lock.lock().unwrap();
        job["progress_message"] = json!(message);
        job["progress_percent"] = json!(percent.clamp(0, 100));
    }
    pub fn install_update(&self, staged: &Path) -> Result<()> {
        anyhow::ensure!(
            !self.development,
            "Self-install is disabled for Cargo development binaries"
        );
        let current = std::env::current_exe()?.canonicalize()?;
        // Rename replaces the executable inode without modifying a running image.
        crate::process::chmod(staged)?;
        self.stop();
        self.join_worker();
        fs::rename(staged, &current).context("Replace application binary")?;
        let systemd = std::env::var("STREAM247_SYSTEMD")
            .is_ok_and(|v| ["1", "true", "yes", "on"].contains(&v.to_lowercase().as_str()))
            || [
                "INVOCATION_ID",
                "JOURNAL_STREAM",
                "NOTIFY_SOCKET",
                "SYSTEMD_EXEC_PID",
            ]
            .iter()
            .any(|s| std::env::var_os(s).is_some());
        self.log(if systemd {
            "[INFO] Systemd service detected; handing restart back to systemd."
        } else {
            "[INFO] Update installed; restarting application."
        });
        *self.shutdown_reason.lock().unwrap() = "application update";
        self.restart.store(!systemd, Ordering::Release);
        self.shutdown.store(true, Ordering::Release);
        Ok(())
    }
}
