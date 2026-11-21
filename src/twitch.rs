//! Twitch GoLive negotiation and H.264 Enhanced RTMP output.
//! Protocol reference: OBS GoLiveAPI_PostData.cpp, models/multitrack-video.hpp,
//! MultitrackVideoOutput.cpp. OBS encoder settings are translated explicitly;
//! the response never becomes arbitrary FFmpeg command-line arguments.
use crate::{
    config::{integer, text, StreamConfig},
    process,
    runtime::App,
    VERSION,
};
use anyhow::{bail, Context, Result};
use reqwest::blocking::Client;
use serde_json::{json, Value};
use std::{collections::HashSet, fs, io::Read, path::Path, time::Duration};
const CONFIG_URL: &str = "https://ingest.twitch.tv/api/v3/GetClientConfiguration";
#[derive(Clone)]
pub struct Rendition {
    pub encoder: String,
    pub width: i64,
    pub height: i64,
    pub numerator: i64,
    pub denominator: i64,
    pub bitrate: i64,
    pub keyint: f64,
    pub profile: String,
    pub preset: String,
    pub bframes: Option<i64>,
}
pub struct Plan {
    pub renditions: Vec<Rendition>,
    pub endpoint: String,
    pub tcurl: String,
    pub audio_bitrate: i64,
    pub audio_channels: i64,
    pub audio_rate: i64,
}
fn read(path: impl AsRef<Path>) -> String {
    fs::read_to_string(path)
        .unwrap_or_default()
        .trim()
        .to_owned()
}
fn memory() -> (u64, u64) {
    let raw = read("/proc/meminfo");
    let value = |key: &str| {
        raw.lines()
            .find_map(|l| {
                l.strip_prefix(key)?
                    .split_whitespace()
                    .next()?
                    .parse::<u64>()
                    .ok()
            })
            .unwrap_or(0)
            * 1024
    };
    let mut total = value("MemTotal:");
    let mut free = value("MemAvailable:");
    if let Ok(limit) = read("/sys/fs/cgroup/memory.max").parse::<u64>() {
        if limit > 0 {
            total = total.min(limit);
            free = free.min(
                limit.saturating_sub(
                    read("/sys/fs/cgroup/memory.current")
                        .parse::<u64>()
                        .unwrap_or(0),
                ),
            );
        }
    }
    (total, free)
}
fn capabilities() -> Value {
    let cpu = read("/proc/cpuinfo");
    let mut cores = HashSet::new();
    for block in cpu.split("\n\n") {
        let val = |key: &str| {
            block.lines().find_map(|l| {
                l.split_once(':')
                    .filter(|(k, _)| k.trim() == key)
                    .map(|(_, v)| v.trim().to_owned())
            })
        };
        if let (Some(socket), Some(core)) = (val("physical id"), val("core id")) {
            cores.insert((socket, core));
        }
    }
    let logical = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    let name = cpu
        .lines()
        .find_map(|l| {
            l.strip_prefix("model name")?
                .split_once(':')
                .map(|(_, v)| v.trim())
        })
        .unwrap_or("unknown");
    let speed = cpu
        .lines()
        .find_map(|l| {
            l.strip_prefix("cpu MHz")?
                .split_once(':')?
                .1
                .trim()
                .parse::<f64>()
                .ok()
        })
        .map(|mhz| mhz as u64);
    let (total, free) = memory();
    let mut gpu = Vec::new();
    if let Ok(entries) = fs::read_dir("/sys/class/drm") {
        for entry in entries.flatten() {
            let card = entry.file_name().to_string_lossy().into_owned();
            if !card.starts_with("card")
                || card.len() == 4
                || !card[4..].chars().all(|c| c.is_ascii_digit())
            {
                continue;
            }
            let device = entry.path().join("device");
            let hex = |path: &str| {
                u32::from_str_radix(read(device.join(path)).trim_start_matches("0x"), 16)
                    .unwrap_or(0)
            };
            let vendor = hex("vendor");
            let id = hex("device");
            if vendor == 0 {
                continue;
            }
            let driver = fs::read_link(device.join("driver"))
                .ok()
                .and_then(|p| p.file_name().map(|s| s.to_string_lossy().into_owned()))
                .unwrap_or_default();
            gpu.push(json!({"model":format!("{driver} PCI {vendor:04x}:{id:04x}"),"vendor_id":vendor,"device_id":id,"dedicated_video_memory":read(device.join("mem_info_vram_total")).parse::<u64>().unwrap_or(0),"shared_system_memory":0,"driver_version":read(format!("/sys/module/{driver}/version"))}));
        }
    }
    let kernel = read("/proc/sys/kernel/osrelease");
    json!({"cpu":{"physical_cores":if cores.is_empty(){logical}else{cores.len().min(logical)},"logical_cores":logical,"speed":speed,"name":name},"memory":{"total":total,"free":free},"gaming_features":null,"system":{"version":kernel,"name":"Linux","build":0,"release":kernel,"revision":"","bits":usize::BITS,"arm":std::env::consts::ARCH.starts_with("aarch")||std::env::consts::ARCH.starts_with("arm"),"armEmulation":false},"gpu":if gpu.is_empty(){Value::Null}else{json!(gpu)}})
}
fn encoder(kind: &str) -> Result<String> {
    let kind = kind.to_lowercase();
    anyhow::ensure!(
        !["hevc", "h265", "av1"].iter().any(|s| kind.contains(s)),
        "Twitch returned a codec outside this client's advertised H.264 support"
    );
    let name = if kind == "obs_x264" || kind == "libx264" {
        "libx264"
    } else if kind.contains("nvenc") {
        "h264_nvenc"
    } else if kind.contains("qsv") {
        "h264_qsv"
    } else if kind.contains("vaapi") {
        "h264_vaapi"
    } else if kind.contains("amf") {
        "h264_amf"
    } else {
        bail!("Twitch requested an unsupported video encoder family");
    };
    Ok(name.into())
}
fn positive(v: &Value, key: &str, default: i64, min: i64, max: i64) -> Result<i64> {
    let n = integer(v, key, default);
    anyhow::ensure!((min..=max).contains(&n), "Twitch returned an invalid {key}");
    Ok(n)
}
fn endpoint(app: &App, response: &Value, cfg: &StreamConfig) -> Result<(String, String)> {
    let endpoints = response["ingest_endpoints"]
        .as_array()
        .context("Twitch returned no ingest endpoints")?;
    let chosen = endpoints
        .iter()
        .find(|e| text(e, "protocol", "").eq_ignore_ascii_case("RTMPS"))
        .or_else(|| {
            endpoints
                .iter()
                .find(|e| text(e, "protocol", "").eq_ignore_ascii_case("RTMP"))
        })
        .context("Twitch returned no supported RTMP endpoint")?;
    let auth = text(chosen, "authentication", &cfg.key);
    anyhow::ensure!(!auth.is_empty(), "Twitch returned empty ingest credentials");
    app.register_secret(&auth);
    let key = auth.split('?').next().unwrap_or("");
    app.register_secret(key);
    let template = text(chosen, "url_template", "");
    let mut url = if template.contains("{stream_key}") {
        reqwest::Url::parse(&template.replace("{stream_key}", key))?
    } else {
        let mut url = reqwest::Url::parse(&template)?;
        let path = format!("{}/{}", url.path().trim_end_matches('/'), key);
        url.set_path(&path);
        url
    };
    anyhow::ensure!(
        ["rtmp", "rtmps"].contains(&url.scheme()) && url.host_str().is_some(),
        "Twitch returned an unsupported ingest URL"
    );
    anyhow::ensure!(
        url.username().is_empty() && url.password().is_none(),
        "Unexpected credentials in the Twitch endpoint authority"
    );
    let mut query: Vec<(String, String)> = url
        .query_pairs()
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();
    for key in [&auth, &cfg.key] {
        if let Some((_, suffix)) = key.split_once('?') {
            let parsed = reqwest::Url::parse(&format!("https://query.invalid/?{suffix}"))?;
            for (k, v) in parsed.query_pairs() {
                let pair = (k.into_owned(), v.into_owned());
                if !query.contains(&pair) {
                    query.push(pair);
                }
            }
        }
    }
    let id = text(&response["meta"], "config_id", "");
    app.register_secret(&id);
    if !id.is_empty() {
        query.retain(|(k, _)| k != "clientConfigId");
        query.push(("clientConfigId".into(), id));
    }
    url.set_query(None);
    if !query.is_empty() {
        url.query_pairs_mut()
            .extend_pairs(query.iter().map(|(k, v)| (k.as_str(), v.as_str())));
    }
    let mut tcurl = url.clone();
    let path = tcurl
        .path()
        .rsplit_once('/')
        .map(|(base, _)| base)
        .unwrap_or("")
        .to_owned();
    tcurl.set_path(&path);
    tcurl.set_query(None);
    Ok((
        url.to_string(),
        tcurl.to_string().trim_end_matches('/').to_owned(),
    ))
}
/// Register credentials before extracting any human-readable response fields.
/// In particular, Twitch can return different ingest authentication from the request.
fn register_response_secrets(app: &App, value: &Value) {
    match value {
        Value::Object(fields) => {
            for (name, value) in fields {
                let name = name.to_ascii_lowercase();
                if [
                    "authentication",
                    "authorization",
                    "password",
                    "secret",
                    "token",
                    "cookie",
                    "stream_key",
                    "streamkey",
                    "api_key",
                    "config_id",
                ]
                .iter()
                .any(|part| name.contains(part))
                {
                    if let Some(secret) = value.as_str().filter(|s| !s.is_empty()) {
                        app.register_secret(secret);
                        if let Some((base, query)) = secret.split_once('?') {
                            app.register_secret(base);
                            if let Ok(url) =
                                reqwest::Url::parse(&format!("https://redaction.invalid/?{query}"))
                            {
                                for (_, value) in url.query_pairs() {
                                    app.register_secret(&value);
                                }
                            }
                        }
                    }
                }
                register_response_secrets(app, value);
            }
        }
        Value::Array(items) => {
            for value in items {
                register_response_secrets(app, value);
            }
        }
        _ => {}
    }
}
/// Flatten Twitch's HTML explanation to a bounded, sanitized single line.
/// Never render HTML, log attributes, or copy arbitrary response/configuration JSON.
fn response_text(app: &App, raw: &str, limit: usize) -> String {
    let entities =
        regex::Regex::new(r"&(#x[0-9a-fA-F]+|#X[0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos|nbsp);")
            .unwrap();
    let mut decoded = raw.to_owned();
    // Decode nested entities before stripping tags and redacting secrets.
    for _ in 0..4 {
        let next = entities
            .replace_all(&decoded, |c: &regex::Captures<'_>| match &c[1] {
                "amp" => "&".into(),
                "lt" => "<".into(),
                "gt" => ">".into(),
                "quot" => "\"".into(),
                "apos" => "'".into(),
                "nbsp" => " ".into(),
                code => {
                    let number = if code.starts_with("#x") || code.starts_with("#X") {
                        u32::from_str_radix(&code[2..], 16).ok()
                    } else {
                        code.strip_prefix('#').and_then(|v| v.parse::<u32>().ok())
                    };
                    number
                        .and_then(char::from_u32)
                        .map(|v| v.to_string())
                        .unwrap_or_else(|| " ".into())
                }
            })
            .into_owned();
        if next == decoded {
            break;
        }
        decoded = next;
    }
    let scripts =
        regex::Regex::new(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>").unwrap();
    let tags = regex::Regex::new(r"(?s)<[^>]*>").unwrap();
    let without_scripts = scripts.replace_all(&decoded, " ");
    let plain = tags.replace_all(&without_scripts, " ");
    // Sanitize before truncation so a long credential cannot leak a truncated prefix.
    let safe = app.sanitize(&plain);
    let line = safe.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut result: String = line
        .chars()
        .filter(|c| !c.is_control())
        .take(limit)
        .collect();
    if line.chars().count() > limit {
        result.push_str(" [truncated]");
    }
    result
}
fn response_reason(app: &App, response: &Value) -> String {
    let mut messages = Vec::new();
    for path in [
        "/status/html_en_us",
        "/status/message",
        "/status/reason",
        "/message",
        "/error/message",
        "/error_description",
        "/error",
        "/detail",
    ] {
        if let Some(raw) = response.pointer(path).and_then(Value::as_str) {
            let message = response_text(app, raw, 2048);
            if !message.is_empty() && !messages.contains(&message) {
                messages.push(message);
            }
        }
    }
    // Some API validation errors use a detail array. Its `input` field can contain
    // credentials or the entire request; only the explicit message/type are allowed.
    if let Some(items) = response["detail"].as_array() {
        for item in items.iter().take(16) {
            for field in ["msg", "type"] {
                if let Some(raw) = item[field].as_str() {
                    let message = response_text(app, raw, 512);
                    if !message.is_empty() && !messages.contains(&message) {
                        messages.push(message);
                    }
                }
            }
        }
    }
    if messages.is_empty() {
        "No explanation provided by Twitch".into()
    } else {
        response_text(app, &messages.join("; "), 4096)
    }
}
pub fn negotiate(app: &App, cfg: &StreamConfig, binary: &Path) -> Result<Plan> {
    let version = crate::updates::tool_version(Some(binary), "ffmpeg", &app.dir)
        .context("Cannot determine FFmpeg version for Enhanced Broadcasting")?;
    anyhow::ensure!(crate::updates::ffmpeg_version_supported(&version), "Twitch Enhanced Broadcasting requires an upstream FFmpeg 8 or newer build with multitrack FLV support");
    let max_tracks = integer(&cfg.settings, "twitch_enhanced_max_tracks", 0);
    let max_bitrate = integer(&cfg.settings, "twitch_enhanced_max_bitrate_kbps", 0);
    let width = ((cfg.height * 16 / 9) + 1) / 2 * 2;
    let request = json!({
        "service": "IVS",
        "schema_version": "2025-01-25",
        "authentication": cfg.key.split('?').next().unwrap_or(""),
        "client": {"name": "stream247", "version": VERSION, "supported_codecs": ["h264"]},
        "capabilities": capabilities(),
        "preferences": {
            "maximum_aggregate_bitrate": if max_bitrate == 0 { Value::Null } else { json!(max_bitrate * 1000) },
            "maximum_video_tracks": if max_tracks == 0 { Value::Null } else { json!(max_tracks) },
            "vod_track_audio": false,
            "composition_gpu_index": null,
            "audio_samples_per_sec": 44100,
            "audio_channels": 2,
            "audio_max_buffering_ms": 1000,
            "audio_fixed_buffering": false,
            "canvases": [{
                "width": width, "height": cfg.height,
                "canvas_width": width, "canvas_height": cfg.height,
                "framerate": {"numerator": cfg.fps, "denominator": 1}
            }]
        }
    });
    app.log("[INFO] Negotiating Twitch Enhanced Broadcasting settings...");
    // No raw requests/responses are logged: both contain stream/session credentials.
    let client = Client::builder()
        .user_agent(format!("Stream247/{VERSION}"))
        .timeout(Duration::from_secs(5))
        .redirect(reqwest::redirect::Policy::none())
        .build()?;
    register_response_secrets(app, &json!({"authentication": cfg.key}));
    let response = client
        .post(CONFIG_URL)
        .json(&request)
        .send()
        .context("Twitch Enhanced Broadcasting configuration request failed")?;
    let http_status = response.status();
    // Consume a bounded response even on HTTP errors so JSON validation/authentication
    // explanations are available. Never include the raw body in logs or errors.
    const MAX_RESPONSE_BYTES: usize = 256 * 1024;
    let mut body = Vec::new();
    response
        .take((MAX_RESPONSE_BYTES + 1) as u64)
        .read_to_end(&mut body)
        .context("Could not read Twitch Enhanced Broadcasting response")?;
    app.debug(&format!(
        "Twitch Enhanced Broadcasting response: HTTP {}, received_bytes={}.",
        http_status.as_u16(),
        body.len()
    ));
    anyhow::ensure!(
        body.len() <= MAX_RESPONSE_BYTES,
        "Twitch configuration response exceeded the 256 KiB limit (HTTP {})",
        http_status.as_u16()
    );
    let response: Value = serde_json::from_slice(&body).map_err(|error| {
        // JSON parser details can contain response data; report only position/category.
        anyhow::anyhow!("Invalid Twitch configuration JSON: HTTP {}, category={:?}, line={}, column={}; response body withheld", http_status.as_u16(), error.classify(), error.line(), error.column())
    })?;
    register_response_secrets(app, &response);
    // OBS GoLiveApi::Config.status is optional: successful configuration responses
    // can omit it or set it to null. Only an explicit status can signal rejection.
    let has_status = response.get("status").is_some_and(|value| !value.is_null());
    let status = if has_status {
        text(&response["status"], "result", "invalid")
    } else {
        "not provided (optional)".into()
    };
    let safe_status = response_text(app, &status, 128);
    let status = status.to_ascii_lowercase();
    let reason = response_reason(app, &response);
    app.debug(&format!(
        "Twitch Enhanced Broadcasting configuration: status={safe_status}; explanation={reason}"
    ));
    anyhow::ensure!(
        http_status.is_success(),
        "Twitch configuration request failed: HTTP {}; status={safe_status}; reason={reason}",
        http_status.as_u16()
    );
    anyhow::ensure!(!has_status || ["success", "warning"].contains(&status.as_str()), "Twitch declined Enhanced Broadcasting configuration: status={safe_status}; reason={reason}");
    app.debug(&format!(
        "Twitch configuration shape: status_present={has_status}, video_configurations={:?}, ingest_endpoints={:?}, live_audio_tracks={:?}; validating supported settings.",
        response["encoder_configurations"].as_array().map(Vec::len),
        response["ingest_endpoints"].as_array().map(Vec::len),
        response["audio_configurations"]["live"].as_array().map(Vec::len)
    ));
    if status == "warning" {
        app.log(&format!(
            "[WARN] Twitch Enhanced Broadcasting configuration warning: {reason}"
        ));
    }
    let configs = response["encoder_configurations"]
        .as_array()
        .context("Twitch returned no video encoder configurations")?;
    anyhow::ensure!(
        !configs.is_empty() && configs.len() <= 6,
        "Twitch requested an unsupported rendition count (supported: 1–6)"
    );
    anyhow::ensure!(
        max_tracks == 0 || configs.len() <= max_tracks as usize,
        "Twitch returned more video tracks than the configured maximum"
    );
    let mut renditions = Vec::new();
    for config in configs {
        anyhow::ensure!(
            integer(config, "canvas_index", 0) == 0,
            "Additional canvases are not supported"
        );
        for (field, allowed) in [
            ("colorspace", &["VIDEO_CS_DEFAULT", "VIDEO_CS_709"][..]),
            ("range", &["VIDEO_RANGE_DEFAULT", "VIDEO_RANGE_PARTIAL"][..]),
            (
                "format",
                &[
                    "VIDEO_FORMAT_NONE",
                    "VIDEO_FORMAT_I420",
                    "VIDEO_FORMAT_NV12",
                ][..],
            ),
        ] {
            if let Some(value) = config[field].as_str() {
                anyhow::ensure!(allowed.contains(&value), "Twitch requested unsupported video {field}; this output supports SDR BT.709 limited-range 4:2:0");
            }
        }
        let enc = encoder(&text(config, "type", ""))?;
        let w = positive(config, "width", 0, 16, width)?;
        let h = positive(config, "height", 0, 16, cfg.height)?;
        anyhow::ensure!(
            w % 2 == 0 && h % 2 == 0,
            "Twitch returned odd video dimensions"
        );
        let num = positive(&config["framerate"], "numerator", cfg.fps, 1, 120000)?;
        let den = positive(&config["framerate"], "denominator", 1, 1, 10000)?;
        let fps = num as f64 / den as f64;
        anyhow::ensure!(
            fps >= 1.0 && fps <= cfg.fps as f64 + 0.01,
            "Unsupported rendition frame rate"
        );
        let settings = &config["settings"];
        anyhow::ensure!(
            text(settings, "rate_control", "CBR").eq_ignore_ascii_case("CBR"),
            "Only CBR Enhanced Broadcasting configurations are supported"
        );
        let bitrate = positive(settings, "bitrate", 0, 100, 100000)?;
        let keyint = settings["keyint_sec"].as_f64().unwrap_or(2.0);
        anyhow::ensure!(
            keyint.is_finite() && (1.0..=10.0).contains(&keyint),
            "Unsupported Twitch keyframe interval"
        );
        let mut profile = text(settings, "profile", "high").to_lowercase();
        anyhow::ensure!(
            ["high", "main", "baseline", "constrained_baseline"].contains(&profile.as_str()),
            "Unsupported H.264 profile in Twitch response"
        );
        if profile == "constrained_baseline" {
            profile = "baseline".into();
        }
        let preset = text(
            settings,
            "preset",
            if enc == "libx264" {
                "veryfast"
            } else if enc == "h264_nvenc" {
                "p5"
            } else {
                ""
            },
        );
        let preset = if enc == "h264_nvenc" {
            let value = text(settings, "preset2", &preset).to_lowercase();
            match value.as_str() {
                "quality" => "p5".into(),
                "maxquality" => "p7".into(),
                "performance" => "p3".into(),
                _ => value,
            }
        } else {
            preset.to_lowercase()
        };
        if enc == "libx264" {
            anyhow::ensure!(
                [
                    "ultrafast",
                    "superfast",
                    "veryfast",
                    "faster",
                    "fast",
                    "medium",
                    "slow",
                    "slower",
                    "veryslow"
                ]
                .contains(&preset.as_str()),
                "Unsupported x264 preset in Twitch response"
            );
        }
        if enc == "h264_nvenc" {
            anyhow::ensure!(
                [
                    "p1",
                    "p2",
                    "p3",
                    "p4",
                    "p5",
                    "p6",
                    "p7",
                    "default",
                    "slow",
                    "medium",
                    "fast",
                    "hq",
                    "hp",
                    "ll",
                    "llhq",
                    "llhp",
                    "lossless",
                    "losslesshp"
                ]
                .contains(&preset.as_str()),
                "Unsupported NVENC preset in Twitch response"
            );
        }
        let bframes = if settings.get("bf").is_some() {
            Some(positive(settings, "bf", 0, 0, 4)?)
        } else if settings.get("bframes").is_some() {
            Some(positive(settings, "bframes", 0, 0, 4)?)
        } else {
            None
        };
        renditions.push(Rendition {
            encoder: enc,
            width: w,
            height: h,
            numerator: num,
            denominator: den,
            bitrate,
            keyint,
            profile,
            preset,
            bframes,
        });
    }
    let live = response["audio_configurations"]["live"]
        .as_array()
        .context("Twitch returned no live audio configuration")?;
    anyhow::ensure!(
        live.len() == 1,
        "This Enhanced Broadcasting implementation supports one live AAC audio track"
    );
    let audio = &live[0];
    anyhow::ensure!(
        text(audio, "codec", "").eq_ignore_ascii_case("aac"),
        "Twitch requested an unsupported audio codec"
    );
    anyhow::ensure!(
        integer(audio, "track_id", 0) == 0,
        "Nonzero Twitch audio track IDs are not supported"
    );
    let audio_bitrate = positive(&audio["settings"], "bitrate", 128, 32, 320)?;
    let audio_channels = positive(audio, "channels", 2, 1, 2)?;
    let total = renditions.iter().map(|r| r.bitrate).sum::<i64>() + audio_bitrate;
    anyhow::ensure!(
        max_bitrate == 0 || total <= max_bitrate,
        "Twitch returned an aggregate bitrate above the configured limit"
    );
    let families: HashSet<_> = renditions
        .iter()
        .map(|r| r.encoder.as_str())
        .filter(|s| *s != "libx264")
        .collect();
    anyhow::ensure!(
        families.len() <= 1,
        "Mixed hardware encoder families are not supported in one enhanced output"
    );
    let (endpoint, tcurl) = endpoint(app, &response, cfg)?;
    app.log(&format!("[INFO] Twitch Enhanced Broadcasting configured: {} local video tracks, {total} kbps aggregate audio/video.",renditions.len()));
    for (index, r) in renditions.iter().enumerate() {
        app.log(&format!(
            "[INFO] Twitch rendition {index}: {}x{} {}/{} fps, H.264 {}, {} kbps.",
            r.width, r.height, r.numerator, r.denominator, r.encoder, r.bitrate
        ));
    }
    Ok(Plan {
        renditions,
        endpoint,
        tcurl,
        audio_bitrate,
        audio_channels,
        audio_rate: 44100,
    })
}
impl Plan {
    pub fn encoder_args(&self) -> Vec<String> {
        let mut args =
            process::args(&["-hide_banner", "-loglevel", "warning", "-stats", "-nostdin"]);
        if self.renditions.iter().any(|r| r.encoder == "h264_vaapi") {
            args.extend(process::args(&["-vaapi_device", "/dev/dri/renderD128"]));
        } else if self.renditions.iter().any(|r| r.encoder == "h264_qsv")
            && Path::new("/dev/dri/renderD128").exists()
        {
            args.extend(process::args(&[
                "-init_hw_device",
                "qsv=hw:/dev/dri/renderD128",
            ]));
        }
        args.extend(process::args(&[
            "-fflags", "+genpts", "-f", "mpegts", "-i", "pipe:0",
        ]));
        let count = self.renditions.len();
        let mut filters = Vec::new();
        if count > 1 {
            filters.push(format!(
                "[0:v:0]split={count}{}",
                (0..count)
                    .map(|i| format!("[eb_in_{i}]"))
                    .collect::<String>()
            ));
        }
        for (i, r) in self.renditions.iter().enumerate() {
            let input = if count == 1 {
                "[0:v:0]".into()
            } else {
                format!("[eb_in_{i}]")
            };
            let pixel = if r.encoder == "h264_vaapi" {
                "format=nv12,hwupload"
            } else if r.encoder == "h264_qsv" {
                "format=nv12"
            } else {
                "format=yuv420p"
            };
            filters.push(format!("{input}scale={}:{}:force_original_aspect_ratio=decrease:flags=bicubic:out_color_matrix=bt709:out_range=tv,pad={}:{}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={}/{},{pixel}[eb_v_{i}]",r.width,r.height,r.width,r.height,r.numerator,r.denominator));
        }
        args.extend(process::args(&["-filter_complex", &filters.join(";")]));
        for i in 0..count {
            args.extend(process::args(&["-map", &format!("[eb_v_{i}]")]));
        }
        args.extend(process::args(&["-map", "0:a:0"]));
        for (i, r) in self.renditions.iter().enumerate() {
            let set = |name: &str| format!("-{name}:v:{i}");
            let rate = format!("{}k", r.bitrate);
            let buffer = format!("{}k", r.bitrate * 2);
            let gop = ((r.numerator as f64 / r.denominator as f64) * r.keyint)
                .round()
                .max(1.0) as i64;
            args.extend(process::args(&[
                &set("c"),
                &r.encoder,
                &set("b"),
                &rate,
                &set("minrate"),
                &rate,
                &set("maxrate"),
                &rate,
                &set("bufsize"),
                &buffer,
                &set("g"),
                &gop.to_string(),
                &set("keyint_min"),
                &gop.to_string(),
                &set("force_key_frames"),
                &format!("expr:gte(t,n_forced*{})", r.keyint),
                &set("profile"),
                &r.profile,
                &set("colorspace"),
                "bt709",
                &set("color_primaries"),
                "bt709",
                &set("color_trc"),
                "bt709",
                &set("color_range"),
                "tv",
            ]));
            if let Some(bf) = r.bframes {
                args.extend(process::args(&[&set("bf"), &bf.to_string()]));
            }
            if r.encoder == "libx264" {
                args.extend(process::args(&[
                    &set("preset"),
                    &r.preset,
                    &set("sc_threshold"),
                    "0",
                    &set("x264-params"),
                    "nal-hrd=cbr:force-cfr=1:scenecut=0",
                ]));
            } else if r.encoder == "h264_nvenc" {
                args.extend(process::args(&[
                    &set("preset"),
                    &r.preset,
                    &set("rc"),
                    "cbr",
                    &set("forced-idr"),
                    "1",
                ]));
            }
        }
        args.extend(process::args(&[
            "-af",
            "aresample=async=1:first_pts=0",
            "-c:a",
            "aac",
            "-b:a",
            &format!("{}k", self.audio_bitrate),
            "-ar",
            &self.audio_rate.to_string(),
            "-ac",
            &self.audio_channels.to_string(),
        ]));
        args
    }
}
