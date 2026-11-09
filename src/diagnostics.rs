//! Opt-in host summaries and aggregated FFmpeg packet progress. No media content is inspected.
use crate::{process, runtime::App, updates, VERSION};
use std::{
    fs,
    path::Path,
    sync::{atomic::Ordering, Arc, Mutex},
    thread,
    time::{Duration, Instant},
};
const MARKER: &str = "__STREAM247_STATS__";
fn field(path: &str, key: &str) -> Option<String> {
    fs::read_to_string(path)
        .ok()?
        .lines()
        .find_map(|line| line.strip_prefix(key).map(|s| s.trim().to_owned()))
}
fn resident(pid: u32) -> String {
    field(&format!("/proc/{pid}/status"), "VmRSS:").unwrap_or_else(|| "unavailable".into())
}
fn system(app: &App) {
    let os = field("/etc/os-release", "PRETTY_NAME=")
        .unwrap_or_else(|| std::env::consts::OS.into())
        .trim_matches('"')
        .to_owned();
    let kernel =
        fs::read_to_string("/proc/sys/kernel/osrelease").unwrap_or_else(|_| "unknown".into());
    let cpu = field("/proc/cpuinfo", "model name")
        .map(|s| s.trim_start_matches(':').trim().to_owned())
        .unwrap_or_else(|| "unknown".into());
    app.debug(&format!(
        "System: app={VERSION}, os={os}, kernel={}, arch={}, CPU={cpu}, logical_cpus={}",
        kernel.trim(),
        std::env::consts::ARCH,
        thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(1)
    ));
    let mut found = false;
    if let Ok(entries) = fs::read_dir("/sys/class/drm") {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if !name.starts_with("card") || !name[4..].chars().all(|c| c.is_ascii_digit()) {
                continue;
            }
            let device = entry.path().join("device");
            let vendor = fs::read_to_string(device.join("vendor")).unwrap_or_default();
            let id = fs::read_to_string(device.join("device")).unwrap_or_default();
            let driver = fs::read_link(device.join("driver"))
                .ok()
                .and_then(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
                .unwrap_or_else(|| "unknown".into());
            let version = fs::read_to_string(format!("/sys/module/{driver}/version"))
                .unwrap_or_else(|_| "not exposed".into());
            app.debug(&format!(
                "GPU: {name}, vendor={}, device={}, driver={driver}, driver_version={}",
                vendor.trim(),
                id.trim(),
                version.trim()
            ));
            found = true;
        }
    }
    if !found {
        app.debug("GPU: no DRM card information exposed by this environment.");
    }
    for tool in ["ffmpeg", "yt-dlp"] {
        if !app.debug_enabled() {
            break;
        }
        let path = process::find(&app.dir, tool);
        let version = updates::tool_version(path.as_deref(), tool, &app.dir)
            .unwrap_or_else(|| "unavailable".into());
        app.debug(&format!(
            "Tool: {tool}, version={version}, path={}",
            path.map(|p| p.display().to_string())
                .unwrap_or_else(|| "not found".into())
        ));
    }
}
pub fn start(app: Arc<App>) {
    thread::spawn(move || {
        let mut enabled = false;
        let mut last = Instant::now() - Duration::from_secs(30);
        while !app.shutdown.load(Ordering::Acquire) {
            let current = app.debug_enabled();
            if current && !enabled {
                system(&app);
                last = Instant::now() - Duration::from_secs(30);
            }
            if current && last.elapsed() >= Duration::from_secs(30) {
                let memory =
                    field("/proc/meminfo", "MemAvailable:").unwrap_or_else(|| "unavailable".into());
                let total =
                    field("/proc/meminfo", "MemTotal:").unwrap_or_else(|| "unavailable".into());
                let uptime = fs::read_to_string("/proc/uptime")
                    .ok()
                    .and_then(|s| s.split_whitespace().next().map(str::to_owned))
                    .unwrap_or_else(|| "unknown".into());
                let source = {
                    let state = app.state.lock().unwrap();
                    format!(
                        "type={}, transport={}, separate_audio={}",
                        state.meta["source_type"].as_str().unwrap_or("not started"),
                        state.meta["source_transport"]
                            .as_str()
                            .unwrap_or("not started"),
                        state.meta["separate_audio"]
                    )
                };
                app.debug(&format!("Runtime: app_uptime_s={}, system_uptime_s={uptime}, app_RSS={}, memory_available={memory}, memory_total={total}, streaming={}, source={source}",app.started.elapsed().as_secs(),resident(std::process::id()),app.is_streaming()));
                last = Instant::now();
            }
            enabled = current;
            thread::sleep(Duration::from_millis(500));
        }
    });
}
#[derive(Default)]
struct Track {
    packets: u64,
    pts: Option<f64>,
    last: Option<Instant>,
}
struct Measurements {
    video: Track,
    audio: Track,
    last_report: Instant,
    last_warning: Instant,
    section: &'static str,
    info: bool,
    descriptions: Vec<String>,
}
pub struct FfmpegStats {
    context: String,
    pid: u32,
    supported: bool,
    data: Mutex<Measurements>,
}
impl FfmpegStats {
    pub fn new(role: &str, pid: u32, supported: bool) -> Self {
        Self {
            context: format!("{role} pid={pid}"),
            pid,
            supported,
            data: Mutex::new(Measurements {
                video: Track::default(),
                audio: Track::default(),
                last_report: Instant::now() - Duration::from_secs(30),
                last_warning: Instant::now() - Duration::from_secs(30),
                section: "input",
                info: false,
                descriptions: Vec::new(),
            }),
        }
    }
    pub fn context(&self) -> &str {
        &self.context
    }
    /// Consume machine stats and informational headers before any log sink sees them.
    pub fn ingest(&self, line: &str, app: &App) -> bool {
        if let Some(pos) = line.find(MARKER) {
            let words: Vec<_> = line[pos..].split_whitespace().collect();
            if words.len() >= 4 {
                if let (Ok(n), Ok(pts)) = (words[2].parse::<u64>(), words[3].parse::<f64>()) {
                    let mut data = self.data.lock().unwrap();
                    let track = match words[1] {
                        "video" => Some(&mut data.video),
                        "audio" => Some(&mut data.audio),
                        _ => None,
                    };
                    if let Some(track) = track {
                        track.packets = n.saturating_add(1);
                        track.pts = pts.is_finite().then_some(pts);
                        track.last = Some(Instant::now());
                    }
                }
            }
            return true;
        }
        if line.find("frame=").is_some_and(|pos| pos < 32) {
            return false;
        }
        let mut data = self.data.lock().unwrap();
        if line.contains("[info]") {
            data.info = true;
        }
        if ["[warning]", "[error]", "[fatal]", "[panic]"]
            .iter()
            .any(|s| line.contains(s))
        {
            data.info = false;
        }
        if line.contains("Input #") {
            data.section = "input";
        }
        if line.contains("Output #") {
            data.section = "output";
        }
        if line.contains("Stream #") && (line.contains("Video:") || line.contains("Audio:")) {
            let description = format!("{} {}", data.section, line.trim());
            if data.descriptions.len() < 16 && !data.descriptions.contains(&description) {
                data.descriptions.push(description.clone());
                drop(data);
                app.debug(&format!("{} codec: {description}", self.context));
                return true;
            }
        }
        // Only explicit connection retry messages are useful beyond codec descriptions.
        if data.info
            && [
                "Will reconnect",
                "Retrying",
                "reconnect in",
                "reconnecting in",
            ]
            .iter()
            .any(|marker| line.contains(marker))
        {
            drop(data);
            app.debug(&format!("{} network: {line}", self.context));
            return true;
        }
        // Info includes arbitrary source metadata. Do not dump that metadata.
        data.info
    }
    pub fn report(&self, app: &App, force: bool) {
        if !app.debug_enabled() {
            return;
        }
        let mut data = self.data.lock().unwrap();
        if !force && data.last_report.elapsed() < Duration::from_secs(30) {
            return;
        }
        data.last_report = Instant::now();
        let age = |t: &Track| {
            t.last
                .map(|v| format!("{:.2}", v.elapsed().as_secs_f64()))
                .unwrap_or_else(|| "unseen".into())
        };
        let pts = |t: &Track| {
            t.pts
                .map(|v| format!("{v:.3}"))
                .unwrap_or_else(|| "unknown".into())
        };
        let gap = match (data.video.pts, data.audio.pts) {
            (Some(v), Some(a)) => format!("{:.1}", (a - v) * 1000.0),
            _ => "unknown".into(),
        };
        let summary=format!("{} progress: per_track_stats={}, video_packets={}, audio_packets={}, video_pts_s={}, audio_pts_s={}, mux_pts_gap_ms={gap}, video_idle_s={}, audio_idle_s={}, FFmpeg_RSS={}",self.context,self.supported,data.video.packets,data.audio.packets,pts(&data.video),pts(&data.audio),age(&data.video),age(&data.audio),resident(self.pid));
        let stalled = data
            .audio
            .last
            .is_some_and(|t| t.elapsed() > Duration::from_secs(5))
            && data
                .video
                .last
                .is_some_and(|t| t.elapsed() < Duration::from_secs(2));
        let warn = stalled && data.last_warning.elapsed() >= Duration::from_secs(30);
        if warn {
            data.last_warning = Instant::now();
        }
        let descriptions = data.descriptions.clone();
        drop(data);
        app.debug(&summary);
        for description in descriptions {
            app.debug(&format!("{} codec: {description}", self.context));
        }
        if warn {
            app.debug(&format!("{} audio packets have stopped advancing while video continues; inspect preceding input errors.",self.context));
        }
    }
}
pub fn stats_supported(binary: &Path, dir: &Path, control: &process::Control) -> bool {
    process::capture(
        binary,
        &process::args(&["-hide_banner", "-h", "full"]),
        dir,
        Duration::from_secs(8),
        Some(control),
    )
    .is_ok_and(|(status, out, _)| status.success() && out.contains("-stats_mux_pre_fmt"))
}
/// Diagnostic packet records share stderr and are consumed immediately, never written per packet.
pub fn instrument(args: &[String], stats: bool) -> Vec<String> {
    let mut out = args.to_vec();
    if let Some(pos) = out.iter().position(|s| s == "-loglevel") {
        if let Some(level) = out.get_mut(pos + 1) {
            *level = "repeat+level+info".into();
        }
    }
    if stats && !out.is_empty() {
        let options = process::args(&[
            "-stats_mux_pre:v",
            "pipe:2",
            "-stats_mux_pre_fmt:v",
            "__STREAM247_STATS__ video {n} {t}",
            "-stats_mux_pre:a",
            "pipe:2",
            "-stats_mux_pre_fmt:a",
            "__STREAM247_STATS__ audio {n} {t}",
        ]);
        let index = out.len() - 1;
        out.splice(index..index, options);
    }
    out
}
