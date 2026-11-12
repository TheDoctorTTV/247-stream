//! FFmpeg/yt-dlp worker with persistent output and supervised source recovery.
use crate::{
    config::{boolean, text, StreamConfig},
    diagnostics::{self, FfmpegStats},
    process::{self, Control, OwnedChild},
    runtime::App,
    state, twitch, updates,
};
mod playout;
mod transport;
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Local, NaiveDate, TimeZone, Utc};
use playout::Producer;
use rand::seq::SliceRandom;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    os::unix::net::UnixStream,
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        atomic::Ordering,
        mpsc::{self, Receiver},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

fn input_type(url: &str) -> &'static str {
    let u = url.to_lowercase();
    if u.contains("youtube.com") || u.contains("youtu.be") {
        if u.contains("list=") {
            "playlist"
        } else if u.contains("watch?v=") || u.contains("youtu.be/") {
            "video"
        } else {
            "unknown"
        }
    } else if reqwest::Url::parse(url)
        .ok()
        .is_some_and(|u| u.path().to_ascii_lowercase().ends_with(".m3u8"))
    {
        "hls"
    } else if u.contains("twitch.tv") {
        "twitch"
    } else {
        "unknown"
    }
}
#[derive(Clone)]
struct Resolver {
    app: Arc<App>,
    binary: PathBuf,
    settings: Value,
    control: Arc<Control>,
    working_cookies: Arc<Mutex<Option<Vec<String>>>>,
}
impl Resolver {
    fn cookie_sets(&self) -> Vec<Vec<String>> {
        if !boolean(&self.settings, "yt_auth_enabled", false) {
            return vec![vec![]];
        }
        let browser = text(&self.settings, "yt_auth_browser", "auto");
        let browsers: Vec<&str> = if browser == "auto" {
            vec![
                "firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera",
            ]
        } else {
            vec![&browser]
        };
        let home = std::env::var("HOME").unwrap_or_default();
        let custom = text(&self.settings, "yt_auth_profile", "").replacen('~', &home, 1);
        let mut sets = Vec::new();
        if let Some(working) = self.working_cookies.lock().unwrap().clone() {
            sets.push(working);
        }
        for b in browsers {
            let mut roots = Vec::new();
            if !custom.is_empty() {
                roots.push(custom.clone());
            }
            let candidates = match b {
                "firefox" => vec![
                    format!("{home}/.var/app/org.mozilla.firefox/.mozilla/firefox"),
                    format!("{home}/snap/firefox/common/.mozilla/firefox"),
                ],
                "chrome" => vec![format!(
                    "{home}/.var/app/com.google.Chrome/config/google-chrome"
                )],
                "chromium" => vec![
                    format!("{home}/.var/app/org.chromium.Chromium/config/chromium"),
                    format!("{home}/snap/chromium/common/chromium"),
                ],
                "brave" => vec![format!(
                    "{home}/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"
                )],
                "edge" => vec![format!(
                    "{home}/.var/app/com.microsoft.Edge/config/microsoft-edge"
                )],
                "vivaldi" => vec![format!(
                    "{home}/.var/app/com.vivaldi.Vivaldi/config/vivaldi"
                )],
                "opera" => vec![format!("{home}/.var/app/com.opera.Opera/config/opera")],
                _ => vec![],
            };
            roots.extend(candidates.into_iter().filter(|p| Path::new(p).exists()));
            let suffixes = if b == "firefox" {
                vec![""]
            } else {
                vec!["", "+basictext", "+gnomekeyring"]
            };
            for suffix in suffixes {
                for spec in std::iter::once(format!("{b}{suffix}"))
                    .chain(roots.iter().map(|p| format!("{b}{suffix}:{p}")))
                {
                    let args = process::args(&["--cookies-from-browser", &spec]);
                    if !sets.contains(&args) {
                        sets.push(args);
                    }
                }
            }
        }
        if boolean(&self.settings, "yt_auth_allow_unauth_fallback", true) {
            sets.push(vec![]);
        }
        sets
    }
    fn run(&self, args: &[&str]) -> Result<String> {
        let mut last = String::new();
        for cookies in self.cookie_sets() {
            if self.control.stopped() {
                bail!("Stopped");
            }
            let mut cmd = cookies.clone();
            cmd.extend(process::args(args));
            let (status, out, err) = process::capture(
                &self.binary,
                &cmd,
                &self.app.dir,
                Duration::from_secs(120),
                Some(&self.control),
            )?;
            if status.success() {
                *self.working_cookies.lock().unwrap() = Some(cookies);
                return Ok(out);
            }
            last = err;
            let lower = last.to_lowercase();
            if cookies.is_empty()
                || ![
                    "cookie",
                    "browser",
                    "profile",
                    "keyring",
                    "permission denied",
                    "database is locked",
                    "failed to decrypt",
                ]
                .iter()
                .any(|s| lower.contains(s))
            {
                break;
            }
            self.app.log(
                "[WARN] Browser cookie authentication failed; trying the next configured fallback.",
            );
        }
        bail!("yt-dlp error: {}", last.trim())
    }
    fn urls(&self, id: &str) -> Result<(String, Option<String>)> {
        let url = format!("https://www.youtube.com/watch?v={id}");
        if let Ok(out) = self.run(&["-g", "-f", "best", "--hls-prefer-native", &url]) {
            if let Some(hls) = out.lines().find(|s| s.contains(".m3u8")) {
                self.app.log(&format!("[INFO] Using HLS manifest for {id}"));
                self.app
                    .debug("Resolved source transport=HLS, combined audio/video input.");
                return Ok((hls.trim().to_owned(), None));
            }
        }
        for format in [
            "bv*+ba/best",
            "best[height<=?1080]",
            "worst[height>=480]",
            "best",
            "",
        ] {
            let args = if format.is_empty() {
                vec!["-g", url.as_str()]
            } else {
                vec!["-g", "-f", format, url.as_str()]
            };
            if let Ok(out) = self.run(&args) {
                let urls: Vec<&str> = out
                    .lines()
                    .map(str::trim)
                    .filter(|s| s.starts_with("http"))
                    .collect();
                if !urls.is_empty() {
                    self.app.log(&format!(
                        "[INFO] Using direct URLs for {id} (format: {format})"
                    ));
                    self.app.debug(&format!(
                        "yt-dlp format={format}, resolved_inputs={}",
                        urls.len()
                    ));
                    return Ok((urls[0].to_owned(), urls.get(1).map(|s| s.to_string())));
                }
            }
        }
        bail!("No playable formats found for video {id}")
    }
    fn media(&self, id: &str) -> Result<Media> {
        let url = format!("https://www.youtube.com/watch?v={id}");
        let metadata = self
            .run(&["-j", &url])
            .ok()
            .and_then(|s| {
                s.lines()
                    .last()
                    .and_then(|s| serde_json::from_str::<Value>(s).ok())
            })
            .unwrap_or(Value::Null);
        let title = if metadata.is_null() {
            self.run(&["--get-title", &url]).unwrap_or(url)
        } else {
            text(&metadata, "title", &url)
        };
        let date = media_date(&metadata, &Local);
        let (video, audio) = self.urls(id)?;
        Ok(Media {
            id: Some(id.to_owned()),
            duration: metadata["duration"]
                .as_f64()
                .filter(|d| d.is_finite() && *d > 0.0),
            audio_expected: text(&metadata, "acodec", "unknown") != "none",
            title: title.trim().to_owned(),
            date,
            video,
            audio,
        })
    }
}
// yt-dlp's date-only fields are UTC. Convert an actual timestamp before
// taking its date so evening uploads keep their local calendar day.
fn media_date<Tz: TimeZone>(metadata: &Value, timezone: &Tz) -> String {
    ["timestamp", "release_timestamp"]
        .iter()
        .find_map(|key| {
            metadata[*key]
                .as_i64()
                .and_then(|t| DateTime::<Utc>::from_timestamp(t, 0))
        })
        .map(|timestamp| {
            timestamp
                .with_timezone(timezone)
                .date_naive()
                .format("%Y-%m-%d")
                .to_string()
        })
        .or_else(|| {
            // A date without a time cannot be reliably timezone-converted.
            NaiveDate::parse_from_str(&text(metadata, "upload_date", ""), "%Y%m%d")
                .ok()
                .map(|date| date.format("%Y-%m-%d").to_string())
        })
        .unwrap_or_default()
}

#[derive(Clone)]
struct Media {
    id: Option<String>,
    duration: Option<f64>,
    audio_expected: bool,
    title: String,
    date: String,
    video: String,
    audio: Option<String>,
}
struct Bridge {
    process: OwnedChild,
    input: UnixStream,
    clock: transport::Clock,
}
pub struct Worker {
    app: Arc<App>,
    cfg: StreamConfig,
    control: Arc<Control>,
    ffmpeg: PathBuf,
    resolver: Resolver,
    bridge: Option<Bridge>,
    enhanced: Option<twitch::Plan>,
    filler: Option<Producer>,
    filler_image: Option<crate::fallback_image::Stamp>,
    filler_image_failed: Option<crate::fallback_image::Stamp>,
    filler_image_checked: Instant,
    transition_asset: Option<crate::fallback_image::Asset>,
    transition_duration: Option<f64>,
    transition_started: Option<Instant>,
    live_opts: bool,
    stats_supported: bool,
    telemetry: Mutex<BTreeMap<u32, Arc<FfmpegStats>>>,
}
impl Worker {
    pub fn run(app: Arc<App>, cfg: StreamConfig, control: Arc<Control>) -> Result<()> {
        {
            let _guard = app.provision_lock.lock().unwrap();
            if control.stopped() {
                return Ok(());
            }
            updates::ensure_binaries(&app, false)?;
        }
        if control.stopped() {
            return Ok(());
        }
        let ffmpeg = process::find(&app.dir, "ffmpeg").context("ffmpeg not found")?;
        let ytdlp = process::find(&app.dir, "yt-dlp").context("yt-dlp not found")?;
        let resolver = Resolver {
            app: Arc::clone(&app),
            binary: ytdlp,
            settings: cfg.settings.clone(),
            control: Arc::clone(&control),
            working_cookies: Arc::new(Mutex::new(None)),
        };
        let stats_supported = diagnostics::stats_supported(&ffmpeg, &app.dir, &control);
        if cfg.bridge() {
            app.log("[INFO] Persistent playout recovery enabled: 8s per-track stall limit, 30s source startup limit, 3 source recovery attempts; fallback retains the output session.");
            app.log("[INFO] Playout timestamp mode: shared A/V transport clock across source/fallback handoffs; bridge discontinuity correction disabled.");
        }
        let mut worker = Self {
            app,
            cfg,
            control,
            ffmpeg,
            resolver,
            bridge: None,
            enhanced: None,
            filler: None,
            filler_image: None,
            filler_image_failed: None,
            filler_image_checked: Instant::now(),
            transition_asset: None,
            transition_duration: None,
            transition_started: None,
            live_opts: true,
            stats_supported,
            telemetry: Mutex::new(BTreeMap::new()),
        };
        let result = worker.execute();
        worker.stop_filler();
        worker.finish_bridge();
        result
    }
    fn finish_bridge(&mut self) {
        if let Some(Bridge {
            mut process, input, ..
        }) = self.bridge.take()
        {
            self.log("[INFO] Closing RTMP input and draining the output before unpublishing...");
            drop(input);
            match process.drain(Duration::from_secs(15)) {
                Ok(Some(status)) => {
                    self.log(&format!("[INFO] RTMP output closed after EOF: {status}."))
                }
                _ => {
                    self.log("[WARN] RTMP drain exceeded its deadline; requesting FFmpeg cleanup.");
                    match process.finish_ffmpeg(Duration::from_secs(5)) {
                        Ok(true) => self.log("[INFO] RTMP output completed FFmpeg shutdown."),
                        _ => self.log("[WARN] RTMP output required a forced stop after graceful shutdown timed out."),
                    }
                }
            }
            self.report_processes(Some(process.0.id()));
            self.control.output_pid.store(0, Ordering::Release);
        }
    }
    fn log(&self, line: &str) {
        self.app.log(&state::redact(line, &self.cfg.key));
    }
    fn ffmpeg_callback(&self, telemetry: Arc<FfmpegStats>) -> Arc<dyn Fn(String) + Send + Sync> {
        let app = Arc::clone(&self.app);
        let key = self.cfg.key.clone();
        let last = Mutex::new(Instant::now() - Duration::from_secs(1));
        Arc::new(move |line: String| {
            if telemetry.ingest(&line, &app) {
                return;
            }
            let mut message = line.trim_start();
            if let Some(pos) = message.find("frame=").filter(|p| *p < 32) {
                message = &message[pos..];
            }
            if message.starts_with("frame=") {
                let mut last = last.lock().unwrap();
                if last.elapsed() < Duration::from_secs(1) {
                    return;
                }
                *last = Instant::now();
            }
            app.ffmpeg_log(telemetry.context(), &state::redact(message, &key));
        })
    }
    fn watch(&self, child: &mut OwnedChild, role: &str) {
        let pid = child.0.id();
        let telemetry = Arc::new(FfmpegStats::new(role, pid, self.stats_supported));
        self.telemetry
            .lock()
            .unwrap()
            .insert(pid, Arc::clone(&telemetry));
        if let Some(stderr) = child.0.stderr.take() {
            process::lines(stderr, self.ffmpeg_callback(telemetry));
        }
        self.app.debug(&format!(
            "FFmpeg process started: role={role}, pid={pid}, per_track_stats={}",
            self.stats_supported
        ));
    }
    fn report_processes(&self, finished: Option<u32>) {
        let values: Vec<_> = self.telemetry.lock().unwrap().values().cloned().collect();
        for value in values {
            value.report(&self.app, finished.is_some());
        }
        if let Some(pid) = finished {
            self.telemetry.lock().unwrap().remove(&pid);
        }
    }
    fn launch(&self, args: &[String], output: Stdio, role: &str) -> Result<OwnedChild> {
        let mut args = diagnostics::instrument(args, self.stats_supported);
        for option in &mut args {
            if option == "-nostdin" {
                *option = "-stdin".into();
            }
        }
        self.log(&format!(
            "[CMD] ffmpeg: {} {}",
            self.ffmpeg.display(),
            args.join(" ")
        ));
        let mut cmd = process::command(&self.ffmpeg, &args, &self.app.dir);
        cmd.stdout(output).stdin(Stdio::piped());
        let mut child = OwnedChild(cmd.spawn().context("Start FFmpeg")?);
        self.watch(&mut child, role);
        Ok(child)
    }
    fn wait(
        &self,
        child: &mut OwnedChild,
        timeout: Option<Duration>,
        skippable: bool,
    ) -> Result<std::process::ExitStatus> {
        let start = Instant::now();
        loop {
            self.report_processes(None);
            if let Some(status) = child.0.try_wait()? {
                self.report_processes(Some(child.0.id()));
                self.app.debug(&format!(
                    "FFmpeg process finished: pid={}, elapsed_s={:.2}, status={status}",
                    child.0.id(),
                    start.elapsed().as_secs_f64()
                ));
                return Ok(status);
            }
            if self.control.stopped()
                || (skippable && self.control.skipped())
                || timeout.is_some_and(|t| start.elapsed() > t)
            {
                self.log(&format!(
                    "[INFO] Requesting graceful FFmpeg stop for pid={}...",
                    child.0.id()
                ));
                if !child.finish_ffmpeg(Duration::from_secs(15))? {
                    self.log("[WARN] FFmpeg required a forced stop after its graceful shutdown deadline.");
                }
                self.report_processes(Some(child.0.id()));
                self.app.debug(&format!(
                    "FFmpeg process cancelled: pid={}, stop={}, skip={}",
                    child.0.id(),
                    self.control.stopped(),
                    self.control.skipped()
                ));
                return Ok(child.0.wait()?);
            }
            thread::sleep(Duration::from_millis(50));
        }
    }
    fn encoder_available(&self, encoder: &str) -> bool {
        let mut args = process::args(&["-hide_banner", "-loglevel", "error", "-nostdin"]);
        if encoder == "h264_vaapi" {
            args.extend(process::args(&["-vaapi_device", "/dev/dri/renderD128"]));
        } else if encoder == "h264_qsv" && Path::new("/dev/dri/renderD128").exists() {
            args.extend(process::args(&[
                "-init_hw_device",
                "qsv=hw:/dev/dri/renderD128",
            ]));
        }
        args.extend(process::args(&[
            "-f",
            "lavfi",
            "-i",
            "color=black:s=320x180:r=30",
            "-t",
            "0.25",
        ]));
        if encoder == "h264_vaapi" {
            args.extend(process::args(&["-vf", "format=nv12,hwupload"]));
        }
        args.extend(process::args(&["-c:v", encoder, "-f", "null", "-"]));
        process::capture(
            &self.ffmpeg,
            &args,
            &self.app.dir,
            Duration::from_secs(8),
            Some(&self.control),
        )
        .is_ok_and(|(s, _, _)| s.success())
    }
    fn select_encoder(&mut self) {
        let requested = text(&self.cfg.settings, "encoder_preference", "auto");
        if requested != "auto" && (requested == "libx264" || self.encoder_available(&requested)) {
            self.cfg.encoder = requested;
            return;
        }
        if requested != "auto" {
            self.log(&format!("[WARN] Requested encoder '{requested}' unavailable; falling back to auto selection."));
        }
        for encoder in ["h264_nvenc", "h264_qsv", "h264_vaapi", "h264_amf"] {
            if self.control.stopped() {
                break;
            }
            if self.encoder_available(encoder) {
                self.cfg.encoder = encoder.into();
                return;
            }
        }
        self.cfg.encoder = "libx264".into();
    }
    fn preflight(&mut self) -> Result<()> {
        if !self.cfg.youtube() {
            self.log("[INFO] Preflight: non-YouTube RTMP target detected; skipping probe.");
            return Ok(());
        }
        self.log("[INFO] Preflight: testing RTMP connectivity...");
        for attempt in 0..2 {
            let args = process::args(&[
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=black:s=320x180:r=30",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=cl=stereo:r=44100",
                "-t",
                "1",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-b:v",
                "1000k",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-f",
                "flv",
                &self.cfg.url(),
            ]);
            let (status, _, err) = process::capture(
                &self.ffmpeg,
                &args,
                &self.app.dir,
                Duration::from_secs(15),
                Some(&self.control),
            )?;
            if status.success() {
                self.log("[INFO] Preflight: RTMP OK");
                return Ok(());
            }
            use std::os::unix::process::ExitStatusExt;
            if status.signal().is_some() {
                self.log("[WARN] FFmpeg preflight crashed; skipping preflight.");
                return Ok(());
            }
            if attempt == 0 && self.cfg.base.starts_with("rtmp://") {
                let mut url = reqwest::Url::parse(&self.cfg.base)?;
                url.set_scheme("rtmps")
                    .map_err(|_| anyhow::anyhow!("Invalid RTMPS scheme"))?;
                let _ = url.set_port(Some(443));
                self.cfg.base = url.to_string().trim_end_matches('/').to_owned();
                self.log("[WARN] RTMP preflight failed; trying RTMPS fallback.");
            } else {
                bail!(
                    "RTMP preflight failed: {}",
                    state::redact(&err, &self.cfg.key)
                );
            }
        }
        Ok(())
    }
    fn output_options(&self, args: &mut Vec<String>) {
        if self.cfg.local_preview {
            let dir = crate::debug::preview_dir(&self.app);
            args.extend(process::args(&[
                "-f",
                "hls",
                "-hls_time",
                "2",
                "-hls_list_size",
                "6",
                "-hls_delete_threshold",
                "2",
                "-hls_start_number_source",
                "epoch",
                "-hls_flags",
                "delete_segments+independent_segments+temp_file",
                "-hls_segment_filename",
                &dir.join("segment-%06d.ts").to_string_lossy(),
                &dir.join("index.m3u8").to_string_lossy(),
            ]));
            return;
        }
        let (url, tcurl) = self
            .enhanced
            .as_ref()
            .map(|plan| (plan.endpoint.clone(), plan.tcurl.clone()))
            .unwrap_or_else(|| (self.cfg.url(), self.cfg.base.clone()));
        if self.live_opts {
            args.extend(process::args(&[
                "-rtmp_live",
                "live",
                "-rtmp_tcurl",
                &tcurl,
            ]));
        }
        args.extend(process::args(&[
            "-flvflags",
            "no_duration_filesize",
            "-f",
            "flv",
            &url,
        ]));
    }
    fn start_bridge(&mut self) -> Result<()> {
        if let Some(bridge) = &mut self.bridge {
            if bridge.process.0.try_wait()?.is_none() {
                return Ok(());
            }
            if self.enhanced.is_some() {
                bail!(
                    "Enhanced Broadcasting RTMP output exited; restart to negotiate a new session"
                );
            }
        }
        if let Some(old) = self.bridge.take() {
            self.report_processes(Some(old.process.0.id()));
        }
        let mut args = process::args(&[
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            "-nostdin",
            "-fflags",
            "+genpts",
            "-f",
            "mpegts",
            "-i",
            "pipe:0",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "copy",
            "-af",
            "aresample=async=1:first_pts=0",
            "-c:a",
            "aac",
            "-b:a",
            &self.cfg.audio_bitrate,
            "-ar",
            "44100",
            "-ac",
            "2",
        ]);
        if let Some(plan) = &self.enhanced {
            args = plan.encoder_args();
        } else {
            self.stabilize_bridge_encoder(&mut args);
        }
        // The feeder owns the continuous program clock. Preserve it; FFmpeg's
        // shared discontinuity correction must not fight audio/video at handoff.
        // This also applies to the Enhanced Broadcasting encoder arguments.
        args.splice(1..1, process::args(&["-copyts", "-start_at_zero"]));
        self.output_options(&mut args);
        let args = diagnostics::instrument(&args, self.stats_supported);
        self.log(&format!("[CMD] ffmpeg (bridge): {}", args.join(" ")));
        let mut cmd = process::command(&self.ffmpeg, &args, &self.app.dir);
        let (input, reader) = UnixStream::pair()?;
        input.set_write_timeout(Some(Duration::from_millis(200)))?;
        cmd.stdin(Stdio::from(std::os::fd::OwnedFd::from(reader)));
        let mut child = OwnedChild(cmd.spawn()?);
        self.watch(&mut child, "bridge");
        self.control
            .output_pid
            .store(child.0.id(), Ordering::Release);
        thread::sleep(Duration::from_millis(150));
        if let Some(status) = child.0.try_wait()? {
            self.report_processes(Some(child.0.id()));
            drop(child);
            if self.live_opts && !self.control.stopped() {
                self.live_opts = false;
                return self.start_bridge();
            }
            bail!("RTMP bridge exited early: {status}");
        }
        self.bridge = Some(Bridge {
            process: child,
            input,
            clock: transport::Clock::new(self.cfg.fps),
        });
        self.log(if self.cfg.local_preview {
            "[INFO] Local preview bridge started; no publication to a streaming destination."
        } else {
            "[INFO] RTMP bridge started (persistent ingest session active)."
        });
        Ok(())
    }
    fn slate(&mut self, duration: f64) -> Result<()> {
        if !self.cfg.bridge() || self.control.stopped() {
            return Ok(());
        }
        let until = Instant::now() + Duration::from_secs_f64(duration);
        while Instant::now() < until && !self.control.stopped() {
            self.pump_filler()?;
            thread::sleep(Duration::from_millis(10));
        }
        Ok(())
    }
    fn ffmpeg_args(&self, media: &Media, to_pipe: bool, seek: f64) -> Vec<String> {
        let [probe, analyze, rtmp_buffer, max_delay] = self.cfg.buffers();
        let mut args = process::args(&[
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            "-nostdin",
            "-probesize",
            probe,
            "-analyzeduration",
            analyze,
        ]);
        if self.cfg.encoder == "h264_vaapi" {
            args.extend(process::args(&["-vaapi_device", "/dev/dri/renderD128"]));
        } else if self.cfg.encoder == "h264_qsv" && Path::new("/dev/dri/renderD128").exists() {
            args.extend(process::args(&[
                "-init_hw_device",
                "qsv=hw:/dev/dri/renderD128",
            ]));
        }
        Self::input_options(&mut args, &media.video, seek);
        args.extend(process::args(&["-re", "-i", &media.video]));
        if let Some(audio) = &media.audio {
            Self::input_options(&mut args, audio, seek);
            args.extend(process::args(&["-re", "-i", audio]));
        } else if !media.audio_expected {
            args.extend(process::args(&[
                "-re",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=cl=stereo:r=44100",
            ]));
        }
        args.extend(process::args(&[
            "-map",
            "0:v:0",
            "-map",
            if media.audio.is_some() || !media.audio_expected {
                "1:a:0"
            } else {
                "0:a:0?"
            },
            "-c:v",
            &self.cfg.encoder,
        ]));
        if self.cfg.encoder == "libx264" {
            args.extend(process::args(&["-preset", "veryfast"]));
        }
        let mut vf = format!(
            "setpts=PTS-STARTPTS,scale=-2:{}:flags=bicubic",
            self.cfg.height
        );
        if boolean(&self.cfg.settings, "overlay_titles", true) {
            let font = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            ]
            .into_iter()
            .find(|p| Path::new(p).exists());
            let font = font
                .map(|p| format!("fontfile='{p}':"))
                .unwrap_or_else(|| "font='Sans':".into());
            let file = self
                .cfg
                .title_file
                .to_string_lossy()
                .replace('\\', "\\\\")
                .replace(':', "\\:")
                .replace('\'', "\\\\'");
            vf.push_str(&format!(",drawtext=textfile='{file}':reload=1:{font}fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:x=10:y=10"));
        }
        vf.push_str(if self.cfg.encoder == "h264_vaapi" {
            ",format=nv12,hwupload"
        } else if self.cfg.encoder == "h264_qsv" {
            ",format=nv12"
        } else {
            ",format=yuv420p"
        });
        let fps = self.cfg.fps.to_string();
        let gop = (self.cfg.fps * 2).to_string();
        args.extend(process::args(&[
            "-fflags",
            "+genpts",
            "-r",
            &fps,
            "-g",
            &gop,
            "-keyint_min",
            &gop,
            "-b:v",
            &self.cfg.bitrate,
            "-maxrate",
            &self.cfg.bitrate,
            "-bufsize",
            &self.cfg.bufsize,
            "-vf",
            &vf,
            "-c:a",
            "aac",
            "-b:a",
            &self.cfg.audio_bitrate,
            "-ar",
            "44100",
            "-ac",
            "2",
            // Private MPEG-TS must match the transition/recovery mux delay.
            // A buffer-mode delay here moves PTS/DTS ahead of PCR; preserving
            // PCR continuity at the next handoff then inserts seconds of dead air.
            "-max_delay",
            if to_pipe { "0" } else { max_delay },
        ]));
        if self.cfg.youtube() {
            args.extend(process::args(&["-minrate", &self.cfg.bitrate]));
            if self.cfg.encoder == "libx264" {
                args.extend(process::args(&["-x264-params", "nal-hrd=cbr:force-cfr=1"]));
            }
        }
        args.extend(process::args(&["-af", "aresample=async=1:first_pts=0"]));
        if let Some(duration) = media.duration {
            args.extend(process::args(&[
                "-t",
                &format!("{:.3}", (duration - seek).max(0.01)),
            ]));
        }
        if !media.audio_expected && media.audio.is_none() {
            args.push("-shortest".into());
        }
        if to_pipe {
            args.extend(process::args(&[
                "-muxdelay",
                "0",
                "-muxpreload",
                "0",
                "-bf",
                "0",
                "-pes_payload_size",
                "0",
                "-mpegts_start_pid",
                "256",
                "-mpegts_flags",
                "+initial_discontinuity+resend_headers",
                "-f",
                "mpegts",
                "pipe:1",
            ]));
        } else {
            args.extend(process::args(&["-rtmp_buffer", rtmp_buffer]));
            self.output_options(&mut args);
        }
        args
    }
    fn play(&mut self, media: &Media, persistent: bool, skippable: bool) -> Result<()> {
        let transport = if media.video.to_lowercase().contains(".m3u8") {
            "HLS"
        } else {
            "direct media"
        };
        {
            let mut state = self.app.state.lock().unwrap();
            state.meta["source_type"] = serde_json::json!(input_type(&self.cfg.source));
            state.meta["source_transport"] = serde_json::json!(transport);
            state.meta["separate_audio"] = serde_json::json!(media.audio.is_some());
        }
        self.app.debug(&format!("Source transport={transport}, separate_audio={}, output_encoder={}, audio_encoder=aac, output_fps={}, output_height={}, audio_rate=44100, channels=2, video_bitrate={}, audio_bitrate={}, buffer_mode={}, persistent_output={persistent}",media.audio.is_some(),self.cfg.encoder,self.cfg.fps,self.cfg.height,self.cfg.bitrate,self.cfg.audio_bitrate,text(&self.cfg.settings,"buffer_mode","Medium")));
        if boolean(&self.cfg.settings, "overlay_titles", true) {
            let title = media.title.replace(['\r', '\n'], " ");
            let suffix = if media.date.is_empty() {
                String::new()
            } else {
                format!(" • {}", media.date)
            };
            let limit = 75usize.saturating_sub(suffix.chars().count());
            let title = if title.chars().count() > limit {
                format!(
                    "{}...",
                    title
                        .chars()
                        .take(limit.saturating_sub(3))
                        .collect::<String>()
                )
            } else {
                title
            };
            process::write_atomic(&self.cfg.title_file, format!("{title}{suffix}").as_bytes())?;
        }
        self.control.skip.store(false, Ordering::Release);
        if persistent {
            // source_once opens the output only after real source A/V is buffered.
            return self.play_resilient(media, skippable);
        }
        for attempt in 0..2 {
            if self.control.stopped() {
                return Ok(());
            }
            let args = self.ffmpeg_args(media, persistent, 0.0);
            let mut child = self.launch(&args, Stdio::null(), "source")?;
            let status = self.wait(&mut child, None, skippable)?;
            if self.control.stopped() || (skippable && self.control.skipped()) {
                return Ok(());
            }
            self.log(&format!(
                "[INFO] ffmpeg exited with code {}",
                status
                    .code()
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| status.to_string())
            ));
            if status.success() {
                break;
            }
            use std::os::unix::process::ExitStatusExt;
            if status.signal().is_some() {
                let local = self.ffmpeg.clone();
                let system = std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
                    .map(|p| p.join("ffmpeg"))
                    .find(|p| {
                        p.is_file()
                            && fs::canonicalize(p).ok() != fs::canonicalize(&local).ok()
                            && updates::tool_version(Some(p), "ffmpeg", &self.app.dir)
                                .as_deref()
                                .is_some_and(updates::ffmpeg_version_supported)
                    });
                if let Some(system) = system {
                    self.ffmpeg = system;
                    self.stats_supported =
                        diagnostics::stats_supported(&self.ffmpeg, &self.app.dir, &self.control);
                    self.finish_bridge();
                    // A negotiated configuration must not silently change encoders or transport.
                    if self.enhanced.is_some() {
                        bail!("Enhanced Broadcasting source encoder crashed; restart to renegotiate the session");
                    }
                    self.select_encoder();
                    bail!("FFmpeg crashed; switched to system FFmpeg for the next item");
                }
            }
            if !persistent && self.live_opts && attempt == 0 {
                self.live_opts = false;
                self.log("[WARN] Retrying without RTMP live protocol options.");
                continue;
            }
            bail!("FFmpeg exited with {status}");
        }
        if !persistent && self.cfg.youtube() {
            self.control
                .pause(Duration::from_millis(if self.control.skipped() {
                    350
                } else {
                    1000
                }));
        }
        Ok(())
    }
    fn prefetch(&self, id: String) -> Receiver<Result<Media>> {
        let (tx, rx) = mpsc::channel();
        let resolver = self.resolver.clone();
        thread::spawn(move || {
            resolver
                .app
                .log(&format!("[PREFETCH] Loading next video: {id}"));
            let result = resolver.media(&id);
            if let Ok(media) = &result {
                resolver
                    .app
                    .log(&format!("[PREFETCH] Ready: {}", media.title));
            }
            let _ = tx.send(result);
        });
        rx
    }
    fn execute(&mut self) -> Result<()> {
        if self.cfg.enhanced() {
            let supports = |path: &Path| {
                updates::tool_version(Some(path), "ffmpeg", &self.app.dir)
                    .as_deref()
                    .is_some_and(updates::ffmpeg_version_supported)
            };
            if !supports(&self.ffmpeg) {
                if let Some(path) =
                    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
                        .map(|p| p.join("ffmpeg"))
                        .find(|p| p.is_file() && supports(p))
                {
                    self.ffmpeg = path;
                    self.stats_supported =
                        diagnostics::stats_supported(&self.ffmpeg, &self.app.dir, &self.control);
                    self.log("[INFO] Using a system FFmpeg 8+ build for Enhanced Broadcasting.");
                }
            }
        }
        self.select_encoder();
        if self.control.stopped() {
            return Ok(());
        }
        if self.cfg.enhanced() {
            self.app.status("Negotiating Twitch Enhanced Broadcasting…");
            let plan = twitch::negotiate(&self.app, &self.cfg, &self.ffmpeg)?;
            let mut checked = std::collections::HashSet::new();
            for rendition in &plan.renditions {
                if checked.insert(rendition.encoder.clone())
                    && !self.encoder_available(&rendition.encoder)
                {
                    bail!(
                        "A Twitch-negotiated video encoder is unavailable on this server: {}",
                        rendition.encoder
                    );
                }
            }
            self.enhanced = Some(plan);
        }
        self.preflight()?;
        self.app.status("Starting…");
        self.log(&format!("[INFO] Encoder: {}", self.cfg.encoder));
        self.log(&format!("[INFO] Source: {}", self.cfg.source));
        if self.cfg.local_preview {
            self.log("[INFO] Output: local HLS preview only; configured streaming destination is unused.");
        } else {
            self.log(&format!("[INFO] RTMP: {}", self.cfg.url()));
        }
        self.log(&format!(
            "[INFO] Output: {}p@{} ~{} video + {} audio",
            self.cfg.height, self.cfg.fps, self.cfg.bitrate, self.cfg.audio_bitrate
        ));
        self.log(&format!(
            "[INFO] yt-dlp auth: {}",
            if boolean(&self.cfg.settings, "yt_auth_enabled", false) {
                text(&self.cfg.settings, "yt_auth_browser", "auto")
            } else {
                "none".into()
            }
        ));
        let kind = input_type(&self.cfg.source);
        self.app.debug(&format!(
            "Source type={kind}; FFmpeg={}, yt-dlp={}, per_track_stats={}",
            self.ffmpeg.display(),
            self.resolver.binary.display(),
            self.stats_supported
        ));
        if kind == "twitch" || kind == "hls" {
            let video = if kind == "twitch" {
                self.resolver
                    .run(&["-f", "best", "-g", &self.cfg.source])?
                    .trim()
                    .to_owned()
            } else {
                self.cfg.source.clone()
            };
            let title = if kind == "twitch" {
                format!(
                    "Twitch • {}",
                    self.cfg
                        .source
                        .trim_end_matches('/')
                        .rsplit('/')
                        .next()
                        .unwrap_or("Live")
                )
            } else {
                "HLS Live Stream".into()
            };
            self.play(
                &Media {
                    id: None,
                    duration: None,
                    audio_expected: true,
                    title,
                    date: String::new(),
                    video,
                    audio: None,
                },
                self.cfg.bridge(),
                false,
            )?;
            self.log("[INFO] Source ended. Stopping relay.");
            return Ok(());
        }
        anyhow::ensure!(
            kind == "playlist" || kind == "video",
            "Unsupported source URL"
        );
        if self.cfg.bridge() {
            self.app.status("Preparing source…");
            self.log("[INFO] Waiting for selected-source audio/video before opening the output; startup fallback is disabled.");
        }
        while !self.control.stopped() {
            let mut enhanced_output_failed = false;
            let result = (|| -> Result<()> {
                let source = self.cfg.source.clone();
                let out = self.resolve_with_filler(
                    move |resolver| {
                        resolver.run(&[
                            "--ignore-errors",
                            if kind == "playlist" {
                                "--flat-playlist"
                            } else {
                                "--no-playlist"
                            },
                            "--get-id",
                            &source,
                        ])
                    },
                    false,
                )?;
                let mut ids: Vec<String> = out
                    .lines()
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_owned)
                    .collect();
                anyhow::ensure!(!ids.is_empty(), "No video IDs found");
                self.log(&format!("[INFO] Found {} videos in playlist", ids.len()));
                if boolean(&self.cfg.settings, "shuffle", false) {
                    ids.shuffle(&mut rand::thread_rng());
                }
                let mut prefetched: Option<(String, Receiver<Result<Media>>)> = None;
                for (idx, id) in ids.iter().enumerate() {
                    if self.control.stopped() {
                        break;
                    }
                    self.control.skip.store(false, Ordering::Release);
                    self.log(&format!(
                        "[INFO] Item #{} - https://www.youtube.com/watch?v={id}",
                        idx + 1
                    ));
                    let cached = if let Some((cached, rx)) = prefetched.take() {
                        if cached == *id {
                            let timeout =
                                Duration::from_millis(if self.cfg.youtube() { 1200 } else { 250 });
                            // Keep forwarding transition/fallback packets while
                            // waiting on the prefetch worker, including at loop wrap.
                            self.resolve_with_filler(
                                move |_| Ok(rx.recv_timeout(timeout).ok()),
                                false,
                            )?
                        } else {
                            None
                        }
                    } else {
                        None
                    };
                    if let Some(next) = ids.get(idx + 1) {
                        prefetched = Some((next.clone(), self.prefetch(next.clone())));
                    }
                    let media = match cached {
                        Some(media) => media,
                        None => {
                            let id = id.clone();
                            self.resolve_with_filler(move |resolver| resolver.media(&id), true)
                        }
                    };
                    let attempted_play = media.is_ok();
                    let result = media.and_then(|m| self.play(&m, self.cfg.bridge(), true));
                    if let Err(e) = result {
                        if self.control.stopped() {
                            break;
                        }
                        if attempted_play && self.enhanced.is_some() {
                            let dead = match self.bridge.as_mut() {
                                Some(bridge) => bridge.process.0.try_wait()?.is_some(),
                                // No output has been opened during initial source preparation.
                                None => false,
                            };
                            if dead {
                                enhanced_output_failed = true;
                                return Err(e.context("Enhanced Broadcasting output stopped"));
                            }
                        }
                        self.stop_filler();
                        self.log(&format!("[WARN] Stream error for video {id}: {e:#}"));
                        if let Err(e) = self.slate(0.8) {
                            self.log(&format!("[WARN] Keepalive slate failed: {e}"));
                        }
                        if self.cfg.bridge() {
                            self.slate(0.25)?;
                        } else {
                            self.control.pause(Duration::from_secs(2));
                        }
                    }
                }
                Ok(())
            })();
            if let Err(e) = result {
                if enhanced_output_failed {
                    return Err(e);
                }
                if !self.control.stopped() {
                    self.stop_filler();
                    self.log(&format!(
                        "[WARN] Loop error: {e:#}. Retrying in 30 seconds..."
                    ));
                    if self.cfg.bridge() {
                        self.slate(30.0)?;
                    } else {
                        self.control.pause(Duration::from_secs(30));
                    }
                }
            } else if !self.control.stopped() {
                self.log("[INFO] End of playlist. Refreshing IDs and looping...");
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod date_tests {
    use super::*;
    use chrono::FixedOffset;
    use serde_json::json;

    #[test]
    fn evening_upload_uses_local_day_instead_of_utc_upload_date() {
        let timezone = FixedOffset::west_opt(4 * 3600).unwrap();
        let timestamp = DateTime::parse_from_rfc3339("2026-10-02T01:30:00Z")
            .unwrap()
            .timestamp();
        assert_eq!(
            media_date(
                &json!({"timestamp": timestamp, "upload_date": "20261002"}),
                &timezone
            ),
            "2026-10-01"
        );
    }

    #[test]
    fn timezone_conversion_can_advance_the_day_and_year() {
        let timezone = FixedOffset::east_opt(9 * 3600).unwrap();
        let timestamp = DateTime::parse_from_rfc3339("2026-12-31T23:30:00Z")
            .unwrap()
            .timestamp();
        assert_eq!(
            media_date(
                &json!({"timestamp": timestamp, "upload_date": "20261231"}),
                &timezone
            ),
            "2027-01-01"
        );
    }

    #[test]
    fn release_timestamp_is_used_when_upload_timestamp_is_missing() {
        let timestamp = DateTime::parse_from_rfc3339("2026-10-02T01:30:00Z")
            .unwrap()
            .timestamp();
        assert_eq!(
            media_date(&json!({"release_timestamp": timestamp}), &Utc),
            "2026-10-02"
        );
    }

    #[test]
    fn missing_or_invalid_timestamps_fall_back_to_date_without_shifting() {
        let timezone = FixedOffset::west_opt(5 * 3600).unwrap();
        for metadata in [
            json!({"upload_date": "20261001"}),
            json!({"timestamp": i64::MAX, "upload_date": "20261001"}),
        ] {
            assert_eq!(media_date(&metadata, &timezone), "2026-10-01");
        }
        assert_eq!(
            media_date(&json!({"upload_date": "invalid"}), &timezone),
            ""
        );
        assert_eq!(media_date(&Value::Null, &timezone), "");
    }
}
