//! Dashboard snapshots and rotating logs. Stream keys are redacted before storage.
use crate::{config, APP_NAME, VERSION};
use chrono::Local;
use regex::Regex;
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    fs::{self, File},
    io::Write,
    path::Path,
    sync::{Mutex, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};
pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
pub struct State {
    pub streaming: bool,
    pub status: String,
    pub meta: Value,
    pub updated: f64,
    logs: VecDeque<String>,
    other: VecDeque<String>,
    ffmpeg: VecDeque<String>,
    file: Option<File>,
    app_file: bool,
    ffmpeg_file: bool,
}
impl Default for State {
    fn default() -> Self {
        Self {
            streaming: false,
            status: "Idle".into(),
            meta: json!({"mode":"headless"}),
            updated: now(),
            logs: VecDeque::new(),
            other: VecDeque::new(),
            ffmpeg: VecDeque::new(),
            file: None,
            app_file: false,
            ffmpeg_file: false,
        }
    }
}
impl State {
    pub fn snapshot(&self) -> Value {
        json!({"app_name":APP_NAME,"app_version":VERSION,"streaming":self.streaming,"status":self.status,"updated_at":self.updated,"meta":self.meta,"logs":self.logs,"logs_other":self.other,"logs_ffmpeg":self.ffmpeg})
    }
    pub fn files(&mut self, dir: &Path, v: &Value, rotate: bool) -> anyhow::Result<()> {
        self.app_file = config::boolean(v, "log_to_file", false);
        self.ffmpeg_file = config::boolean(v, "ffmpeg_log_to_file", false);
        if !self.app_file && !self.ffmpeg_file {
            self.file = None;
            return Ok(());
        }
        if self.file.is_some() && !rotate {
            return Ok(());
        }
        self.file = None;
        let path = dir.join("latest.log");
        if path.exists() {
            fs::rename(
                &path,
                dir.join(format!(
                    "latest-{}.log",
                    Local::now().format("%Y%m%d-%H%M%S-%3f")
                )),
            )?;
        }
        self.file = Some(File::create(path)?);
        Ok(())
    }
    pub fn log(&mut self, raw: &str, key: &str) {
        let raw = redact(raw, key);
        let ffmpeg = raw.contains("[CMD] ffmpeg")
            || raw.contains("ffmpeg exited with code")
            || ![
                "[INFO]",
                "[WARN]",
                "[ERROR]",
                "[STATUS]",
                "[PREFETCH]",
                "[CMD]",
                "[DETAIL]",
                "[DEBUG]",
            ]
            .iter()
            .any(|p| raw.starts_with(p));
        static STATS: OnceLock<Regex> = OnceLock::new();
        let stats = STATS.get_or_init(|| Regex::new(r"([A-Za-z_]+)=\s*([^\s]+)").unwrap());
        for line in raw
            .split(['\r', '\n'])
            .map(str::trim)
            .filter(|s| !s.is_empty())
        {
            let (level, msg) =
                if line.starts_with('[') && line.find(']').is_some_and(|i| i < 12) && !ffmpeg {
                    let i = line.find(']').unwrap();
                    (line[1..i].to_owned(), line[i + 1..].trim().to_owned())
                } else if ffmpeg {
                    let msg = if line.starts_with("frame=") {
                        stats
                            .captures_iter(line)
                            .map(|m| format!("{}={}", &m[1], &m[2]))
                            .collect::<Vec<_>>()
                            .join(" | ")
                    } else {
                        line.to_owned()
                    };
                    ("FFMPEG".to_owned(), msg)
                } else {
                    ("LOG".to_owned(), line.to_owned())
                };
            let formatted = format!("[{}] {:<7} {}", Local::now().format("%H:%M:%S"), level, msg);
            let _ = writeln!(std::io::stdout().lock(), "{formatted}");
            for bucket in [
                &mut self.logs,
                if ffmpeg {
                    &mut self.ffmpeg
                } else {
                    &mut self.other
                },
            ] {
                bucket.push_back(formatted.clone());
                while bucket.len() > 100 {
                    bucket.pop_front();
                }
            }
            if (ffmpeg && self.ffmpeg_file) || (!ffmpeg && self.app_file) {
                if let Some(f) = &mut self.file {
                    let _ = writeln!(f, "{formatted}");
                    let _ = f.flush();
                }
            }
        }
        self.updated = now();
    }
}
pub fn redact(s: &str, key: &str) -> String {
    let s = if key.is_empty() {
        s.to_owned()
    } else {
        s.replace(key, "[REDACTED_STREAM_KEY]")
    };
    // Signed media URLs contain tokens and are unnecessary for diagnostics.
    static URL: OnceLock<Regex> = OnceLock::new();
    URL.get_or_init(|| Regex::new(r"https?://[^\s]*googlevideo[^\s]*").unwrap())
        .replace_all(&s, "[MEDIA_URL]")
        .into_owned()
}
pub type SharedState = Mutex<State>;
