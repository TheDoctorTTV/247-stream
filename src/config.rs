//! Existing JSON settings are kept compatible with the dashboard and Python releases.
use anyhow::{Context, Result};
use serde_json::{json, Map, Value};
use std::{
    fs,
    path::{Path, PathBuf},
};

pub fn text(v: &Value, key: &str, fallback: &str) -> String {
    v.get(key)
        .and_then(Value::as_str)
        .unwrap_or(fallback)
        .trim()
        .to_owned()
}
pub fn boolean(v: &Value, key: &str, fallback: bool) -> bool {
    match v.get(key) {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0) != 0.0,
        Some(Value::String(s)) => match s.to_lowercase().as_str() {
            "true" | "1" | "yes" | "on" => true,
            "false" | "0" | "no" | "off" => false,
            _ => fallback,
        },
        _ => fallback,
    }
}
pub fn integer(v: &Value, key: &str, fallback: i64) -> i64 {
    v.get(key)
        .and_then(|n| n.as_i64().or_else(|| n.as_str()?.parse().ok()))
        .unwrap_or(fallback)
}
fn choice(v: &Value, key: &str, allowed: &[&str], default: &str) -> Value {
    let mut s = text(v, key, default);
    if [
        "encoder_preference",
        "yt_auth_browser",
        "app_update_channel",
        "theme",
    ]
    .contains(&key)
    {
        s = s.to_lowercase();
    }
    json!(if allowed.contains(&s.as_str()) {
        s
    } else {
        default.to_owned()
    })
}
pub fn bitrate(v: &Value) -> i64 {
    let raw = v
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| v.to_string());
    let number: String = raw
        .chars()
        .skip_while(|c| !c.is_ascii_digit())
        .take_while(char::is_ascii_digit)
        .collect();
    let n = number.parse::<i64>().unwrap_or(2500).clamp(1500, 25000);
    // Python round uses ties-to-even, including halfway bitrate steps.
    let steps = (n - 1500) / 500;
    let remainder = (n - 1500) % 500;
    1500 + 500 * (steps + i64::from(remainder > 250 || (remainder == 250 && steps % 2 == 1)))
}
fn entries(v: &Value, keys: &[&str]) -> Vec<String> {
    let items: Vec<Value> = match v {
        Value::String(s) => s.lines().map(|s| json!(s)).collect(),
        Value::Array(a) => a.clone(),
        _ => vec![],
    };
    let mut out = Vec::new();
    for item in items {
        let s = if item.is_object() {
            keys.iter()
                .map(|k| text(&item, k, ""))
                .find(|s| !s.is_empty())
                .unwrap_or_default()
        } else {
            item.as_str().unwrap_or("").trim().to_owned()
        };
        if !s.is_empty() && !out.contains(&s) {
            out.push(s);
        }
    }
    out
}
fn resolve(v: &Value, list: &str, selected: &str, keys: &[&str]) -> (Vec<String>, String) {
    let mut items = entries(&v[list], keys);
    let mut current = text(v, selected, "");
    if items.is_empty() && !current.is_empty() {
        items.push(current.clone());
    }
    if !items.is_empty() && (items.len() == 1 || !items.contains(&current)) {
        current = items[0].clone();
    }
    (items, current)
}
fn names(v: &Value, list: &str, name_key: &str, valid: &[String], keys: &[&str]) -> Value {
    let mut out = Map::new();
    if let Some(m) = v[name_key].as_object() {
        for (k, n) in m {
            if valid.contains(k) && n.as_str().is_some_and(|s| !s.trim().is_empty()) {
                out.insert(k.clone(), json!(n.as_str().unwrap().trim()));
            }
        }
    }
    // Older configurations can carry names inside their entries or a name list.
    for field in [name_key, list] {
        if let Some(items) = v[field].as_array() {
            for item in items {
                let id = keys
                    .iter()
                    .map(|k| text(item, k, ""))
                    .find(|s| !s.is_empty())
                    .unwrap_or_default();
                let name = text(item, "name", "");
                if valid.contains(&id) && !name.is_empty() && !out.contains_key(&id) {
                    out.insert(id, json!(name));
                }
            }
        }
    }
    Value::Object(out)
}
pub fn normalize(v: &Value) -> Value {
    let (sources, source) = resolve(
        v,
        "sources",
        "playlist_url",
        &["url", "source", "playlist_url"],
    );
    let (keys, key) = resolve(
        v,
        "stream_keys",
        "stream_key",
        &["stream_key", "key", "value"],
    );
    let mut services = Vec::new();
    let items: Vec<Value> = if let Some(m) = v["stream_url_services"].as_object() {
        m.iter()
            .map(|(k, v)| json!({"name":k,"rtmp_base":v}))
            .collect()
    } else {
        v["stream_url_services"]
            .as_array()
            .cloned()
            .unwrap_or_default()
    };
    for item in items {
        let name = ["name", "label", "title"]
            .iter()
            .map(|k| text(&item, k, ""))
            .find(|s| !s.is_empty())
            .unwrap_or_default();
        let url = ["rtmp_base", "url", "value"]
            .iter()
            .map(|k| text(&item, k, ""))
            .find(|s| !s.is_empty())
            .unwrap_or_default();
        if !name.is_empty()
            && !url.trim_end_matches('/').is_empty()
            && !services.iter().any(|s: &Value| {
                text(s, "rtmp_base", "").trim_end_matches('/') == url.trim_end_matches('/')
            })
        {
            services.push(json!({"name":name,"rtmp_base":url}));
        }
    }
    let mut out = json!({
        "playlist_url":source,"sources":sources,"source_names":names(v,"sources","source_names",&sources,&["url","source","playlist_url"]),
        "stream_key":key,"stream_keys":keys,"stream_key_names":names(v,"stream_keys","stream_key_names",&keys,&["stream_key","key","value"]),
        "rtmp_base":text(v,"rtmp_base","rtmp://a.rtmp.youtube.com/live2"),"stream_url_services":services,
        "video_bitrate":format!("{}k",bitrate(&v["video_bitrate"])),"update_download_cap_mbps":50,
        "yt_auth_profile":text(v,"yt_auth_profile","")
    });
    for (k, allowed, default) in [
        (
            "resolution",
            &["480p", "720p", "1080p", "1440p", "2160p"][..],
            "720p",
        ),
        (
            "buffer_mode",
            &["Low", "Medium", "High", "Ultra"][..],
            "Medium",
        ),
        (
            "encoder_preference",
            &[
                "auto",
                "libx264",
                "h264_nvenc",
                "h264_qsv",
                "h264_amf",
                "h264_vaapi",
            ][..],
            "auto",
        ),
        (
            "yt_auth_browser",
            &[
                "auto", "firefox", "chrome", "edge", "chromium", "brave", "vivaldi", "opera",
            ][..],
            "auto",
        ),
        (
            "app_update_channel",
            &["release", "prerelease"][..],
            "release",
        ),
        ("theme", &["blue", "purple", "red", "mono"][..], "blue"),
    ] {
        out[k] = choice(v, k, allowed, default);
    }
    out["app_update_selected_version"] = json!(text(v, "app_update_selected_version", ""));
    let fps = integer(v, "framerate", 30);
    out["framerate"] = json!(if [30, 60].contains(&fps) { fps } else { 30 });
    for (k, default) in [
        ("overlay_titles", true),
        ("shuffle", false),
        ("log_to_file", false),
        ("ffmpeg_log_to_file", false),
        ("debug_logging", false),
        ("remember", true),
        ("check_updates_startup", true),
        ("auto_app_updates", false),
        ("yt_auth_enabled", false),
        ("youtube_persistent_output", true),
    ] {
        out[k] = json!(boolean(v, k, default));
    }
    out
}
pub fn merge(base: &Value, payload: &Value) -> Result<Value> {
    let obj = payload
        .as_object()
        .context("Settings must be a JSON object")?;
    let mut out = base.as_object().cloned().unwrap_or_default();
    let normalized = normalize(payload);
    for (k, v) in normalized.as_object().unwrap() {
        if obj.contains_key(k) {
            out.insert(k.clone(), v.clone());
        }
    }
    for (list, selected) in [("sources", "playlist_url"), ("stream_keys", "stream_key")] {
        if obj.contains_key(list) && !obj.contains_key(selected) {
            out.insert(selected.to_owned(), normalized[selected].clone());
        }
    }
    if obj.contains_key("app_update_channel")
        && out.get("app_update_channel") != base.get("app_update_channel")
    {
        out.insert("app_update_selected_version".into(), json!(""));
    }
    if obj.contains_key("video_bitrate") || obj.contains_key("buffer_mode") {
        out.remove("bufsize");
    }
    out.insert("update_download_cap_mbps".into(), json!(50));
    Ok(Value::Object(out))
}
pub fn load(dir: &Path) -> Result<Value> {
    let path = dir.join("config.json");
    if !path.exists() {
        return Ok(json!({}));
    }
    let v: Value = serde_json::from_slice(&fs::read(&path).context("Read config.json")?)
        .context("Parse config.json")?;
    anyhow::ensure!(v.is_object(), "config.json must contain an object");
    Ok(v)
}
pub fn save(dir: &Path, v: &Value) -> Result<()> {
    let temp = dir.join("config.json.tmp");
    fs::write(&temp, serde_json::to_vec_pretty(v)?)?;
    fs::rename(temp, dir.join("config.json"))?;
    Ok(())
}
#[derive(Clone)]
pub struct StreamConfig {
    pub settings: Value,
    pub source: String,
    pub key: String,
    pub base: String,
    pub height: i64,
    pub fps: i64,
    pub bitrate: String,
    pub bufsize: String,
    pub audio_bitrate: String,
    pub title_file: PathBuf,
    pub encoder: String,
}
impl StreamConfig {
    pub fn new(raw: &Value, dir: &Path) -> Self {
        let mut v = normalize(raw);
        v["yt_auth_allow_unauth_fallback"] =
            json!(boolean(raw, "yt_auth_allow_unauth_fallback", true));
        let mode = text(&v, "buffer_mode", "Medium");
        let mult = match mode.as_str() {
            "Low" => 2,
            "High" => 4,
            "Ultra" => 5,
            _ => 3,
        };
        if raw.get("video_bitrate").is_none() {
            v["video_bitrate"] = json!(match text(&v, "resolution", "720p").as_str() {
                "480p" => "1500k",
                "1080p" => "6000k",
                "1440p" => "9000k",
                "2160p" => "25000k",
                _ => "2500k",
            });
        }
        let audio_bitrate = text(raw, "audio_bitrate", "128k");
        let audio_bitrate = if audio_bitrate.is_empty() {
            "128k".to_owned()
        } else {
            audio_bitrate
        };
        let title_file = text(raw, "title_file", "current_title.txt");
        let title_file = if title_file.is_empty() {
            "current_title.txt".to_owned()
        } else {
            title_file
        };
        let height = text(&v, "resolution", "720p")
            .trim_end_matches('p')
            .parse()
            .unwrap_or(720);
        Self {
            source: text(&v, "playlist_url", ""),
            key: text(&v, "stream_key", ""),
            base: text(&v, "rtmp_base", ""),
            height,
            fps: integer(&v, "framerate", 30),
            bitrate: text(&v, "video_bitrate", "2500k"),
            bufsize: format!("{}k", bitrate(&v["video_bitrate"]) * mult),
            audio_bitrate,
            title_file: dir.join(title_file),
            encoder: "libx264".into(),
            settings: v,
        }
    }
    pub fn url(&self) -> String {
        format!("{}/{}", self.base.trim_end_matches('/'), self.key)
    }
    pub fn youtube(&self) -> bool {
        reqwest::Url::parse(&self.base)
            .ok()
            .and_then(|u| u.host_str().map(str::to_owned))
            .is_some_and(|h| h.contains("youtube"))
    }
    pub fn bridge(&self) -> bool {
        (self.base.starts_with("rtmp://") || self.base.starts_with("rtmps://"))
            && (!self.youtube() || boolean(&self.settings, "youtube_persistent_output", true))
    }
    pub fn buffers(&self) -> [&'static str; 4] {
        match text(&self.settings, "buffer_mode", "Medium").as_str() {
            "Low" => ["15M", "5000000", "2048k", "3000000"],
            "High" => ["40M", "15000000", "6144k", "12000000"],
            "Ultra" => ["50M", "30000000", "8192k", "25000000"],
            _ => ["25M", "10000000", "4096k", "7000000"],
        }
    }
}
