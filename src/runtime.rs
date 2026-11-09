//! Owns application state, worker lifetime, and update/restart coordination.
use crate::{
    config::{self, text, StreamConfig},
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
};
struct Running {
    control: Arc<Control>,
    thread: JoinHandle<()>,
}
pub struct App {
    pub dir: PathBuf,
    pub development: bool,
    pub state: SharedState,
    config: Mutex<Value>,
    worker: Mutex<Option<Running>>,
    pub provision_lock: Mutex<()>,
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
            development,
            state: Mutex::new(state),
            config: Mutex::new(cfg),
            worker: Mutex::new(None),
            provision_lock: Mutex::new(()),
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
        let key = text(&self.settings(), "stream_key", "");
        self.state.lock().unwrap().log(line, &key);
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
            let mut job = self.app_job.lock().unwrap();
            if !config::boolean(&job, "running", false) {
                job["last_result"] = Value::Null;
                job["last_error"] = json!("");
            }
        }
        {
            let mut state = self.state.lock().unwrap();
            state.meta["source"] = normalized["playlist_url"].clone();
            state.meta["resolution"] = normalized["resolution"].clone();
            state.meta["fps"] = normalized["framerate"].clone();
        }
        if self.is_streaming() {
            self.log("[INFO] Web settings saved. Changes apply on next start.");
        }
        Ok(normalized)
    }
    pub fn start(self: &Arc<Self>) -> Result<()> {
        let mut running = self.worker.lock().unwrap();
        if running.as_ref().is_some_and(|r| !r.thread.is_finished()) {
            return Ok(());
        }
        if let Some(old) = running.take() {
            let _ = old.thread.join();
        }
        anyhow::ensure!(
            !config::boolean(&self.binary_job.lock().unwrap(), "running", false),
            "Wait for the binary operation to finish before starting"
        );
        let cfg = StreamConfig::new(&self.raw_settings(), &self.dir);
        anyhow::ensure!(
            !cfg.source.is_empty() && !cfg.base.is_empty() && !cfg.key.is_empty(),
            "Set a source, RTMP URL and stream key before starting"
        );
        {
            let settings = self.settings();
            let mut state = self.state.lock().unwrap();
            state.files(&self.dir, &settings, true)?;
            state.streaming = true;
            state.status = "Starting".into();
            state.meta = json!({"mode":"headless","source":cfg.source,"resolution":format!("{}p",cfg.height),"fps":cfg.fps.to_string()});
        }
        let control = Arc::new(Control::default());
        let worker_control = Arc::clone(&control);
        let app = Arc::clone(self);
        self.log("[INFO] Starting stream (headless)...");
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
        self.restart.store(!systemd, Ordering::Release);
        self.shutdown.store(true, Ordering::Release);
        Ok(())
    }
}
