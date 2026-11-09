//! Central log filtering/redaction, bounded dashboard history and independent file sinks.
use crate::{config, logfiles::LogFile, APP_NAME, VERSION};
use chrono::Local;
use regex::Regex;
use serde_json::{json, Value};
use std::{
    collections::{HashSet, VecDeque},
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
    pub debug: bool,
    logs: VecDeque<String>,
    other: VecDeque<String>,
    ffmpeg: VecDeque<String>,
    app_file: Option<LogFile>,
    ffmpeg_file: Option<LogFile>,
    secrets: HashSet<String>,
}
impl Default for State {
    fn default() -> Self {
        Self {
            streaming: false,
            status: "Idle".into(),
            meta: json!({"mode":"headless"}),
            updated: now(),
            debug: false,
            logs: VecDeque::new(),
            other: VecDeque::new(),
            ffmpeg: VecDeque::new(),
            app_file: None,
            ffmpeg_file: None,
            secrets: HashSet::new(),
        }
    }
}
impl State {
    pub fn snapshot(&self) -> Value {
        json!({"app_name":APP_NAME,"app_version":VERSION,"streaming":self.streaming,"status":self.status,"updated_at":self.updated,"meta":self.meta,"logs":self.logs,"logs_other":self.other,"logs_ffmpeg":self.ffmpeg})
    }
    pub fn register_settings(&mut self, v: &Value) {
        let normalized = config::normalize(v);
        let key = config::text(&normalized, "stream_key", "");
        if !key.is_empty() {
            self.secrets.insert(key);
        }
        if let Some(keys) = normalized["stream_keys"].as_array() {
            for key in keys
                .iter()
                .filter_map(Value::as_str)
                .filter(|s| !s.is_empty())
            {
                self.secrets.insert(key.to_owned());
            }
        }
        // Preserve old values in memory so an active worker remains redacted after settings change.
        fn visit(v: &Value, out: &mut HashSet<String>) {
            if let Some(map) = v.as_object() {
                for (k, value) in map {
                    let k = k.to_lowercase();
                    if [
                        "password",
                        "passwd",
                        "secret",
                        "token",
                        "api_key",
                        "authorization",
                        "cookie",
                    ]
                    .iter()
                    .any(|word| k.contains(word))
                    {
                        if let Some(value) = value.as_str().filter(|s| !s.is_empty()) {
                            out.insert(value.to_owned());
                        }
                    }
                    visit(value, out);
                }
            } else if let Some(items) = v.as_array() {
                for item in items {
                    visit(item, out);
                }
            } else if let Some(value) = v.as_str() {
                if let Ok(url) = reqwest::Url::parse(value) {
                    if let Some(password) = url.password().filter(|s| !s.is_empty()) {
                        out.insert(password.to_owned());
                    }
                    for (name, value) in url.query_pairs() {
                        if ["token", "key", "pass", "secret", "auth", "signature", "sig"]
                            .iter()
                            .any(|word| name.to_lowercase().contains(word))
                            && !value.is_empty()
                        {
                            out.insert(value.into_owned());
                        }
                    }
                }
            }
        }
        visit(v, &mut self.secrets);
    }
    pub fn sanitize(&self, raw: &str) -> String {
        let mut output = redact(raw, "");
        let mut secrets: Vec<&String> = self.secrets.iter().collect();
        secrets.sort_by_key(|s| std::cmp::Reverse(s.len()));
        for secret in secrets {
            output = output.replace(secret.as_str(), "[REDACTED]");
            let escaped: String = reqwest::Url::parse("https://redaction.invalid/")
                .map(|mut u| {
                    u.query_pairs_mut().append_pair("value", secret);
                    u.query()
                        .unwrap_or("")
                        .trim_start_matches("value=")
                        .to_owned()
                })
                .unwrap_or_default();
            if !escaped.is_empty() {
                output = output.replace(escaped.as_str(), "[REDACTED]");
            }
        }
        output
    }
    pub fn files(&mut self, dir: &Path, v: &Value, rotate: bool) -> anyhow::Result<()> {
        self.register_settings(v);
        self.debug = config::boolean(v, "debug_logging", false);
        if config::boolean(v, "log_to_file", false) {
            if self.app_file.is_none() {
                self.app_file = Some(LogFile::open(dir, "app", 4 * 1024 * 1024, true)?);
            } else if rotate {
                self.app_file.as_mut().unwrap().rotate()?;
            }
        } else {
            self.app_file = None;
        }
        if config::boolean(v, "ffmpeg_log_to_file", false) {
            if self.ffmpeg_file.is_none() {
                self.ffmpeg_file = Some(LogFile::open(dir, "ffmpeg", 16 * 1024 * 1024, true)?);
            } else if rotate {
                self.ffmpeg_file.as_mut().unwrap().rotate()?;
            }
        } else {
            self.ffmpeg_file = None;
        }
        Ok(())
    }
    pub fn log(&mut self, raw: &str, key: &str) {
        self.emit(raw, key, None);
    }
    pub fn ffmpeg_log(&mut self, raw: &str) {
        self.emit(raw, "", Some(true));
    }
    fn emit(&mut self, raw: &str, key: &str, kind: Option<bool>) {
        if raw.starts_with("[DEBUG]") && !self.debug {
            return;
        }
        if !key.is_empty() {
            self.secrets.insert(key.to_owned());
        }
        let raw = self.sanitize(raw);
        static STATS: OnceLock<Regex> = OnceLock::new();
        let stats = STATS.get_or_init(|| Regex::new(r"([A-Za-z_]+)=\s*([^\s]+)").unwrap());
        for line in raw
            .split(['\r', '\n'])
            .map(str::trim)
            .filter(|s| !s.is_empty())
        {
            let ffmpeg = kind.unwrap_or_else(|| {
                line.contains("[CMD] ffmpeg")
                    || (![
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
                    .any(|p| line.starts_with(p)))
            });
            let tagged = [
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
            .any(|p| line.starts_with(p));
            let (level, message) = if tagged {
                let i = line.find(']').unwrap();
                (line[1..i].to_owned(), line[i + 1..].trim().to_owned())
            } else if ffmpeg {
                (
                    "FFMPEG".into(),
                    if line.starts_with("frame=") {
                        stats
                            .captures_iter(line)
                            .map(|m| format!("{}={}", &m[1], &m[2]))
                            .collect::<Vec<_>>()
                            .join(" | ")
                    } else {
                        line.to_owned()
                    },
                )
            } else {
                ("LOG".into(), line.to_owned())
            };
            let formatted = format!(
                "[{}] {:<7} {}",
                Local::now().format("%Y-%m-%d %H:%M:%S%.3f %:z"),
                level,
                message
            );
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
            let sink = if ffmpeg {
                &mut self.ffmpeg_file
            } else {
                &mut self.app_file
            };
            if sink
                .as_mut()
                .is_some_and(|file| file.line(&formatted).is_err())
            {
                *sink = None;
                let _=writeln!(std::io::stderr().lock(),"[WARN] Log file writing failed; this file sink was disabled until logging is reconfigured.");
            }
        }
        self.updated = now();
    }
}
/// Sanitization is applied before output to terminal, dashboard, or disk. Never dump request headers.
pub fn redact(raw: &str, key: &str) -> String {
    static HEADER: OnceLock<Regex> = OnceLock::new();
    static URL: OnceLock<Regex> = OnceLock::new();
    static SECRET: OnceLock<Regex> = OnceLock::new();
    static AUTH: OnceLock<Regex> = OnceLock::new();
    let header=HEADER.get_or_init(||Regex::new(r"(?i)(?:\b(?:authorization|proxy-authorization|set-cookie)\b|\bcookie\s*[:=]|-headers\b|-cookies\b)").unwrap());
    let url = URL.get_or_init(|| {
        Regex::new(r#"(?i)\b(?:https?|rtmps?|rtsp|srt|ftp)://[^\s<>"']+"#).unwrap()
    });
    let secret=SECRET.get_or_init(||Regex::new(r#"(?i)(\b(?:stream[_-]?key|api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token|token|signature|sig)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)"#).unwrap());
    let auth =
        AUTH.get_or_init(|| Regex::new(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9+/=_\-.]+").unwrap());
    raw.split(['\r', '\n'])
        .map(|line| {
            if header.is_match(line) {
                return "[REDACTED_SENSITIVE_HEADER]".to_owned();
            }
            let cleaned = url.replace_all(line, |c: &regex::Captures| {
                reqwest::Url::parse(&c[0])
                    .ok()
                    .and_then(|u| {
                        u.host_str().map(|host| {
                            // Read the original authority because URL normalization drops
                            // explicit default ports such as :80 and :443.
                            let authority = c[0]
                                .split_once("://")
                                .map(|(_, rest)| rest)
                                .unwrap_or("")
                                .split(['/', '?', '#'])
                                .next()
                                .unwrap_or("")
                                .rsplit('@')
                                .next()
                                .unwrap_or("");
                            let port = if authority.starts_with('[') {
                                authority.split_once("]:").map(|(_, port)| port)
                            } else {
                                authority.rsplit_once(':').map(|(_, port)| port)
                            };
                            let port = port
                                .filter(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
                                .map(|p| format!(":{p}"))
                                .unwrap_or_default();
                            let host = if host.contains(':') {
                                format!("[{}]", host.trim_matches(['[', ']']))
                            } else {
                                host.to_owned()
                            };
                            format!("{}://{host}{port}/[REDACTED_URL]", u.scheme())
                        })
                    })
                    .unwrap_or_else(|| "[REDACTED_URL]".into())
            });
            let cleaned = auth.replace_all(&cleaned, "[REDACTED_AUTH]");
            let mut cleaned = secret.replace_all(&cleaned, "${1}[REDACTED]").into_owned();
            if !key.is_empty() {
                cleaned = cleaned.replace(key, "[REDACTED_STREAM_KEY]");
            }
            if let Ok(home) = std::env::var("HOME") {
                if !home.is_empty() {
                    cleaned = cleaned.replace(home.as_str(), "~");
                }
            }
            cleaned
        })
        .collect::<Vec<_>>()
        .join("\n")
}
pub type SharedState = Mutex<State>;
