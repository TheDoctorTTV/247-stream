//! Port of the existing FFmpeg/yt-dlp worker. Timeline redesign is intentionally separate.
use crate::{
    config::{boolean, text, StreamConfig},
    diagnostics::{self, FfmpegStats},
    process::{self, Control, OwnedChild},
    runtime::App,
    state, updates,
};
use anyhow::{bail, Context, Result};
use chrono::{DateTime, NaiveDate, Utc};
use rand::seq::SliceRandom;
use serde_json::Value;
use std::os::fd::AsFd;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::{ChildStdin, Stdio},
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
    } else if u.trim().ends_with(".m3u8") {
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
        let date = text(&metadata, "upload_date", "");
        let date = NaiveDate::parse_from_str(&date, "%Y%m%d")
            .ok()
            .map(|d| d.format("%Y-%m-%d").to_string())
            .or_else(|| {
                metadata["timestamp"]
                    .as_i64()
                    .and_then(|t| DateTime::<Utc>::from_timestamp(t, 0))
                    .map(|d| d.format("%Y-%m-%d").to_string())
            })
            .unwrap_or_default();
        let (video, audio) = self.urls(id)?;
        Ok(Media {
            title: title.trim().to_owned(),
            date,
            video,
            audio,
        })
    }
}
struct Media {
    title: String,
    date: String,
    video: String,
    audio: Option<String>,
}
struct Bridge {
    process: OwnedChild,
    input: ChildStdin,
}
pub struct Worker {
    app: Arc<App>,
    cfg: StreamConfig,
    control: Arc<Control>,
    ffmpeg: PathBuf,
    resolver: Resolver,
    bridge: Option<Bridge>,
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
        let mut worker = Self {
            app,
            cfg,
            control,
            ffmpeg,
            resolver,
            bridge: None,
            live_opts: true,
            stats_supported,
            telemetry: Mutex::new(BTreeMap::new()),
        };
        worker.execute()
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
        let args = diagnostics::instrument(args, self.stats_supported);
        self.log(&format!(
            "[CMD] ffmpeg: {} {}",
            self.ffmpeg.display(),
            args.join(" ")
        ));
        let mut cmd = process::command(&self.ffmpeg, &args, &self.app.dir);
        cmd.stdout(output);
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
                child.stop();
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
        if self.live_opts {
            args.extend(process::args(&[
                "-rtmp_live",
                "live",
                "-rtmp_tcurl",
                &self.cfg.base,
            ]));
        }
        args.extend(process::args(&["-f", "flv", &self.cfg.url()]));
    }
    fn start_bridge(&mut self) -> Result<()> {
        if let Some(bridge) = &mut self.bridge {
            if bridge.process.0.try_wait()?.is_none() {
                return Ok(());
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
        self.output_options(&mut args);
        let args = diagnostics::instrument(&args, self.stats_supported);
        self.log(&format!("[CMD] ffmpeg (bridge): {}", args.join(" ")));
        let mut cmd = process::command(&self.ffmpeg, &args, &self.app.dir);
        cmd.stdin(Stdio::piped());
        let mut child = OwnedChild(cmd.spawn()?);
        let input = child.0.stdin.take().context("Bridge input pipe missing")?;
        self.watch(&mut child, "bridge");
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
        });
        self.log("[INFO] RTMP bridge started (persistent ingest session active).");
        Ok(())
    }
    fn slate(&mut self, duration: f64) -> Result<()> {
        if !self.cfg.bridge() || self.control.stopped() {
            return Ok(());
        }
        self.start_bridge()?;
        let h = self.cfg.height.max(360);
        let mut w = (h * 16 / 9) + 1;
        w -= w % 2;
        let args = process::args(&[
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-re",
            "-f",
            "lavfi",
            "-i",
            &format!("color=black:s={w}x{h}:rate={}", self.cfg.fps),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=cl=stereo:r=44100",
            "-t",
            &duration.to_string(),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
            "-mpegts_flags",
            "+initial_discontinuity+resend_headers",
            "-f",
            "mpegts",
            "pipe:1",
        ]);
        let fd = self
            .bridge
            .as_ref()
            .unwrap()
            .input
            .as_fd()
            .try_clone_to_owned()?;
        let mut child = self.launch(&args, Stdio::from(fd), "keepalive")?;
        self.wait(
            &mut child,
            Some(Duration::from_secs_f64(duration + 3.0)),
            false,
        )?;
        Ok(())
    }
    fn ffmpeg_args(&self, media: &Media, to_pipe: bool) -> Vec<String> {
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
        if media.video.to_lowercase().contains(".m3u8") {
            args.extend(process::args(&[
                "-http_persistent",
                "0",
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-live_start_index",
                "-3",
            ]));
        }
        args.extend(process::args(&["-re", "-i", &media.video]));
        if let Some(audio) = &media.audio {
            args.extend(process::args(&["-re", "-i", audio]));
        }
        args.extend(process::args(&[
            "-map",
            "0:v:0",
            "-map",
            if media.audio.is_some() {
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
        let mut vf = format!("scale=-2:{}:flags=bicubic", self.cfg.height);
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
            "-max_delay",
            max_delay,
        ]));
        if self.cfg.youtube() {
            args.extend(process::args(&["-minrate", &self.cfg.bitrate]));
            if self.cfg.encoder == "libx264" {
                args.extend(process::args(&["-x264-params", "nal-hrd=cbr:force-cfr=1"]));
            }
        }
        if to_pipe {
            args.extend(process::args(&[
                "-muxdelay",
                "0",
                "-muxpreload",
                "0",
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
        self.app.debug(&format!("Source transport={transport}, separate_audio={}, output_encoder={}, audio_encoder=aac, output_fps={}, output_height={}, audio_rate=44100, channels=2, video_bitrate={}, audio_bitrate={}, buffer_mode={}, persistent_RTMP={persistent}",media.audio.is_some(),self.cfg.encoder,self.cfg.fps,self.cfg.height,self.cfg.bitrate,self.cfg.audio_bitrate,text(&self.cfg.settings,"buffer_mode","Medium")));
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
            self.start_bridge()?;
        }
        for attempt in 0..2 {
            if self.control.stopped() {
                return Ok(());
            }
            let args = self.ffmpeg_args(media, persistent);
            let output = if persistent {
                Stdio::from(
                    self.bridge
                        .as_ref()
                        .unwrap()
                        .input
                        .as_fd()
                        .try_clone_to_owned()?,
                )
            } else {
                Stdio::null()
            };
            let mut child = self.launch(&args, output, "source")?;
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
            if persistent {
                if let Some(bridge) = &mut self.bridge {
                    if let Some(status) = bridge.process.0.try_wait()? {
                        bail!("RTMP bridge exited: {status}");
                    }
                }
            }
            if status.success() {
                break;
            }
            use std::os::unix::process::ExitStatusExt;
            if status.signal().is_some() {
                let local = self.ffmpeg.clone();
                let system = std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
                    .map(|p| p.join("ffmpeg"))
                    .find(|p| {
                        p.is_file() && fs::canonicalize(p).ok() != fs::canonicalize(&local).ok()
                    });
                if let Some(system) = system {
                    self.ffmpeg = system;
                    self.stats_supported =
                        diagnostics::stats_supported(&self.ffmpeg, &self.app.dir, &self.control);
                    self.bridge = None;
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
        self.select_encoder();
        if self.control.stopped() {
            return Ok(());
        }
        self.preflight()?;
        self.app.status("Starting…");
        self.log(&format!("[INFO] Encoder: {}", self.cfg.encoder));
        self.log(&format!("[INFO] Source: {}", self.cfg.source));
        self.log(&format!("[INFO] RTMP: {}", self.cfg.url()));
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
                    title,
                    date: String::new(),
                    video,
                    audio: None,
                },
                false,
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
            self.start_bridge()?;
        }
        while !self.control.stopped() {
            let result = (|| -> Result<()> {
                let out = self.resolver.run(&[
                    "--ignore-errors",
                    if kind == "playlist" {
                        "--flat-playlist"
                    } else {
                        "--no-playlist"
                    },
                    "--get-id",
                    &self.cfg.source,
                ])?;
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
                    self.log(&format!(
                        "[INFO] Item #{} - https://www.youtube.com/watch?v={id}",
                        idx + 1
                    ));
                    let cached = prefetched.take().and_then(|(cached, rx)| {
                        if cached == *id {
                            rx.recv_timeout(Duration::from_millis(if self.cfg.youtube() {
                                1200
                            } else {
                                250
                            }))
                            .ok()
                        } else {
                            None
                        }
                    });
                    if let Some(next) = ids.get(idx + 1) {
                        prefetched = Some((next.clone(), self.prefetch(next.clone())));
                    }
                    let media = cached.unwrap_or_else(|| self.resolver.media(id));
                    let result = media.and_then(|m| self.play(&m, self.cfg.bridge(), true));
                    if let Err(e) = result {
                        if self.control.stopped() {
                            break;
                        }
                        self.log(&format!("[WARN] Stream error for video {id}: {e:#}"));
                        if let Err(e) = self.slate(0.8) {
                            self.log(&format!("[WARN] Keepalive slate failed: {e}"));
                        }
                        self.control
                            .pause(Duration::from_millis(if self.cfg.youtube() {
                                2000
                            } else {
                                250
                            }));
                    }
                }
                Ok(())
            })();
            if let Err(e) = result {
                if !self.control.stopped() {
                    self.log(&format!(
                        "[WARN] Loop error: {e:#}. Retrying in 30 seconds..."
                    ));
                    self.control.pause(Duration::from_secs(30));
                }
            } else if !self.control.stopped() {
                self.log("[INFO] End of playlist. Refreshing IDs and looping...");
            }
        }
        self.bridge = None;
        Ok(())
    }
}
