//! Source recovery and a single MPEG-TS feeder for the persistent output.
//! Each producer has its own socket; only this controller writes bridge media.
use super::{
    input_type,
    transport::{Epoch, PACKET},
    Media, Resolver, Worker,
};
use crate::{
    diagnostics::MediaProgress,
    process::{self, OwnedChild},
};
use anyhow::{bail, Context, Result};
use std::{
    io::{self, Read, Write},
    os::{fd::OwnedFd, unix::net::UnixStream},
    process::Stdio,
    sync::mpsc::{self, TryRecvError},
    thread,
    time::{Duration, Instant},
};

const MAX_PENDING: usize = 4 * 1024 * 1024;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const STALL_TIMEOUT: Duration = Duration::from_secs(8);
const MAX_RECOVERIES: usize = 3;

pub(super) struct Producer {
    process: OwnedChild,
    output: UnixStream,
    pending: Vec<u8>,
    eof: bool,
    started: Instant,
    last_data: Instant,
    drop_pid: Option<u16>,
    epoch: Epoch,
}
impl Producer {
    fn read(&mut self) -> Result<()> {
        let mut buffer = [0u8; PACKET * 128];
        // Bounded buffering also lets a replacement producer prepare before handoff.
        for _ in 0..8 {
            if self.pending.len() + buffer.len() > MAX_PENDING {
                break;
            }
            match self.output.read(&mut buffer) {
                Ok(0) => {
                    self.eof = true;
                    break;
                }
                Ok(n) => {
                    self.pending.extend_from_slice(&buffer[..n]);
                    self.last_data = Instant::now();
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(e).context("Read private FFmpeg media socket"),
            }
        }
        Ok(())
    }
    fn packets(&mut self) -> Vec<u8> {
        // Never switch producers in the middle of a transport packet.
        let n = ((self.pending.len() / PACKET).min(128)) * PACKET;
        let mut data: Vec<u8> = self.pending.drain(..n).collect();
        if let Some(drop_pid) = self.drop_pid {
            let mut kept = 0;
            for at in (0..data.len()).step_by(PACKET) {
                let pid = (((data[at + 1] & 0x1f) as u16) << 8) | data[at + 2] as u16;
                if data[at] != 0x47 || pid != drop_pid {
                    data.copy_within(at..at + PACKET, kept);
                    kept += PACKET;
                }
            }
            data.truncate(kept);
        }
        data
    }
}
enum SourceEnd {
    Complete,
    Cancelled,
    Retry { reason: String, position: f64 },
}
impl Worker {
    pub(super) fn input_options(args: &mut Vec<String>, url: &str, seek: f64) {
        if url.starts_with("http://") || url.starts_with("https://") {
            // Repeat before BOTH -i arguments; input options are not global.
            // A normal finite-file EOF must not reconnect back to its beginning.
            args.extend(process::args(&[
                "-rw_timeout",
                "5000000",
                "-reconnect",
                "1",
                "-reconnect_on_network_error",
                "1",
                "-reconnect_on_http_error",
                "429,5xx",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-reconnect_max_retries",
                "3",
                "-reconnect_delay_total_max",
                "15",
            ]));
        }
        args.extend(process::args(&["-thread_queue_size", "256"]));
        if url.to_ascii_lowercase().contains(".m3u8") {
            args.extend(process::args(&["-http_persistent", "0"]));
        }
        if seek > 0.0 {
            args.extend(process::args(&["-ss", &format!("{seek:.3}")]));
        }
    }
    pub(super) fn stabilize_bridge_encoder(&self, args: &mut Vec<String>) {
        // Keep the output codec configuration owned by the bridge. Copying source
        // H.264 would expose source/filler SPS and encoder changes to the receiver.
        if let Some(index) = args.iter().position(|arg| arg == "-c:v") {
            args[index + 1] = self.cfg.encoder.clone();
        }
        let pixel = if self.cfg.encoder == "h264_vaapi" {
            args.splice(
                1..1,
                process::args(&["-vaapi_device", "/dev/dri/renderD128"]),
            );
            "format=nv12,hwupload"
        } else if self.cfg.encoder == "h264_qsv" {
            if std::path::Path::new("/dev/dri/renderD128").exists() {
                args.splice(
                    1..1,
                    process::args(&["-init_hw_device", "qsv=hw:/dev/dri/renderD128"]),
                );
            }
            "format=nv12"
        } else {
            "format=yuv420p"
        };
        let height = self.cfg.height;
        let width = ((height * 16 / 9) + 1) / 2 * 2;
        let filter = format!("scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bicubic,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,{pixel}");
        args.extend(process::args(&[
            "-vf",
            &filter,
            "-r",
            &self.cfg.fps.to_string(),
            "-g",
            &(self.cfg.fps * 2).to_string(),
            "-keyint_min",
            &(self.cfg.fps * 2).to_string(),
            "-b:v",
            &self.cfg.bitrate,
            "-maxrate",
            &self.cfg.bitrate,
            "-bufsize",
            &self.cfg.bufsize,
            "-profile:v",
            "high",
        ]));
        if self.cfg.encoder == "libx264" {
            args.extend(process::args(&[
                "-preset",
                "veryfast",
                "-sc_threshold",
                "0",
            ]));
        } else if self.cfg.encoder == "h264_nvenc" {
            args.extend(process::args(&["-rc", "cbr", "-forced-idr", "1"]));
        }
    }
    fn producer(&self, args: &[String], role: &str) -> Result<Producer> {
        let (output, writer) = UnixStream::pair()?;
        output.set_nonblocking(true)?;
        let process = self.launch(args, Stdio::from(OwnedFd::from(writer)), role)?;
        Ok(Producer {
            process,
            output,
            pending: Vec::new(),
            eof: false,
            started: Instant::now(),
            last_data: Instant::now(),
            drop_pid: None,
            epoch: Epoch::default(),
        })
    }
    fn progress(&self, producer: &Producer) -> Option<MediaProgress> {
        self.telemetry
            .lock()
            .unwrap()
            .get(&producer.process.0.id())
            .map(|v| v.progress())
    }
    fn write_packets(&mut self, data: &[u8]) -> Result<()> {
        let bridge = self
            .bridge
            .as_mut()
            .context("Persistent output output is missing")?;
        let mut offset = 0;
        let deadline = Instant::now() + Duration::from_secs(5);
        while offset < data.len() {
            if self.control.stopped() {
                bail!("Stopped");
            }
            if let Some(status) = bridge.process.0.try_wait()? {
                bail!("Persistent output output exited: {status}");
            }
            match bridge.input.write(&data[offset..]) {
                Ok(0) => bail!("Persistent output media input closed"),
                Ok(n) => offset += n,
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::WouldBlock
                            | io::ErrorKind::TimedOut
                            | io::ErrorKind::Interrupted
                    ) => {}
                Err(e) => return Err(e).context("Feed persistent output"),
            }
            if offset < data.len() && Instant::now() >= deadline {
                bail!("Persistent output output has not accepted a media block within 5 seconds");
            }
        }
        Ok(())
    }
    fn forward(&mut self, producer: &mut Producer) -> Result<()> {
        let bridge = self
            .bridge
            .as_mut()
            .context("Persistent output is missing")?;
        if !producer.epoch.started() {
            let Some(offset) = bridge.clock.begin(&mut producer.epoch, &producer.pending)? else {
                return Ok(());
            };
            self.app.debug(&format!("Playout clock handoff: producer_pid={}, shared A/V offset={:.6}s; output session retained.", producer.process.0.id(), offset as f64 / 90_000.0));
        }
        // Keep bursts bounded so cancellation and the watchdog remain responsive.
        for _ in 0..8 {
            let mut data = producer.packets();
            if data.is_empty() {
                break;
            }
            self.bridge
                .as_mut()
                .context("Persistent output is missing")?
                .clock
                .rewrite(&mut producer.epoch, &mut data)?;
            self.write_packets(&data)?;
        }
        Ok(())
    }
    fn start_filler(&mut self) -> Result<()> {
        // Fallback may preserve an existing output, but must never start one.
        if self.filler.is_some() || self.control.stopped() || self.bridge.is_none() {
            return Ok(());
        }
        self.start_bridge()?;
        let h = self.cfg.height;
        let w = ((h * 16 / 9) + 1) / 2 * 2;
        // CPU filler avoids consuming another hardware encoding session during
        // replacement-source startup. The EB bridge keeps its encoders unchanged.
        self.filler_image = crate::fallback_image::revision(&self.app);
        self.filler_image_checked = Instant::now();
        let use_image =
            self.filler_image.is_some() && self.filler_image_failed != self.filler_image;
        let mut args = process::args(&[
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            "-nostdin",
            "-re",
        ]);
        if use_image {
            args.extend(process::args(&[
                "-f",
                "image2",
                "-loop",
                "1",
                "-framerate",
                &self.cfg.fps.to_string(),
                "-i",
                &crate::fallback_image::path(&self.app).to_string_lossy(),
            ]));
        } else {
            args.extend(process::args(&[
                "-f",
                "lavfi",
                "-i",
                &format!("color=black:s={w}x{h}:rate={}", self.cfg.fps),
            ]));
        }
        args.extend(process::args(&[
            "-re",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=cl=stereo:r=44100",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-g",
            &(self.cfg.fps * 2).to_string(),
            "-c:a",
            "aac",
            "-b:a",
            &self.cfg.audio_bitrate,
            "-ar",
            "44100",
            "-ac",
            "2",
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
            "-flush_packets",
            "1",
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
        if use_image {
            let filter = format!("scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1");
            let index = args.len() - 1;
            args.splice(index..index, process::args(&["-vf", &filter]));
        }
        self.filler = Some(self.producer(&args, "keepalive")?);
        self.log(if use_image {
            "[INFO] Playout fallback active: recovery image and silent audio; persistent output retained."
        } else {
            "[INFO] Playout fallback active: black video and silent audio; persistent output retained."
        });
        Ok(())
    }
    pub(super) fn pump_filler(&mut self) -> Result<()> {
        if self.control.stopped() || !self.cfg.bridge() || self.bridge.is_none() {
            return Ok(());
        }
        if self.filler.is_some() && self.filler_image_checked.elapsed() >= Duration::from_secs(1) {
            self.filler_image_checked = Instant::now();
            if crate::fallback_image::revision(&self.app) != self.filler_image {
                self.stop_filler();
                self.filler_image_failed = None;
            }
        }
        self.start_filler()?;
        let mut filler = self.filler.take().context("Fallback producer missing")?;
        let result = (|| -> Result<()> {
            filler.read()?;
            self.forward(&mut filler)?;
            if let Some(status) = filler.process.0.try_wait()? {
                bail!("Fallback FFmpeg exited: {status}");
            }
            if filler.eof {
                bail!("Fallback FFmpeg closed its media output");
            }
            if filler.last_data.elapsed() > STARTUP_TIMEOUT {
                bail!("Fallback FFmpeg stopped producing media");
            }
            Ok(())
        })();
        let image_failed = result.is_err()
            && self.filler_image.is_some()
            && self.filler_image_failed != self.filler_image
            && (filler.process.0.try_wait().ok().flatten().is_some()
                || filler.eof
                || filler.last_data.elapsed() > STARTUP_TIMEOUT);
        self.filler = Some(filler);
        if image_failed {
            self.filler_image_failed = self.filler_image;
            self.log("[WARN] Recovery image could not be played; using black video and silent audio until the image changes.");
            self.stop_filler();
            return self.pump_filler();
        }
        result
    }
    fn stop_producer(&mut self, mut producer: Producer, drain: bool) {
        producer.process.request_ffmpeg_quit();
        let deadline = Instant::now()
            + if self.control.stopped() {
                Duration::from_secs(15)
            } else {
                Duration::from_secs(3)
            };
        loop {
            let _ = producer.read();
            if drain && !self.control.stopped() {
                if self.forward(&mut producer).is_err() {
                    producer.pending.clear();
                }
            } else {
                producer.pending.clear();
            }
            if !drain && self.filler.is_some() {
                let _ = self.pump_filler();
            }
            let exited = producer.process.0.try_wait().ok().flatten().is_some();
            if exited && (producer.eof || producer.pending.len() < PACKET) {
                break;
            }
            if Instant::now() >= deadline {
                self.log("[WARN] Source/fallback cleanup exceeded its deadline; stopping only that producer. persistent output is retained.");
                producer.process.stop();
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        self.report_processes(Some(producer.process.0.id()));
    }
    pub(super) fn stop_filler(&mut self) {
        if let Some(filler) = self.filler.take() {
            self.stop_producer(filler, true);
        }
    }
    /// Resolve off-thread; preserve an existing output with fallback, but leave
    /// the initial output closed while preparing the first real source.
    pub(super) fn resolve_with_filler<T: Send + 'static>(
        &mut self,
        task: impl FnOnce(Resolver) -> Result<T> + Send + 'static,
        skippable: bool,
    ) -> Result<T> {
        let (tx, rx) = mpsc::channel();
        let resolver = self.resolver.clone();
        thread::spawn(move || {
            let _ = tx.send(task(resolver));
        });
        loop {
            if self.control.stopped() {
                bail!("Stopped");
            }
            if skippable && self.control.skipped() {
                bail!("Skipped while resolving source");
            }
            match rx.try_recv() {
                Ok(result) => return result,
                Err(TryRecvError::Disconnected) => bail!("Source resolver exited without a result"),
                Err(TryRecvError::Empty) => {}
            }
            if self.cfg.bridge() {
                self.pump_filler()?;
            }
            self.report_processes(None);
            thread::sleep(Duration::from_millis(10));
        }
    }
    fn refresh_media(&mut self, media: Media, skippable: bool) -> Result<Media> {
        let source = self.cfg.source.clone();
        self.resolve_with_filler(
            move |resolver| {
                let mut refreshed = media;
                if let Some(id) = &refreshed.id {
                    let (video, audio) = resolver.urls(id)?;
                    refreshed.video = video;
                    refreshed.audio = audio;
                } else if input_type(&source) == "twitch" {
                    refreshed.video = resolver
                        .run(&["-f", "best", "-g", &source])?
                        .trim()
                        .to_owned();
                }
                Ok(refreshed)
            },
            skippable,
        )
    }
    fn apply_debug_fault(&mut self, source: &mut Producer) -> Result<()> {
        let request = {
            let mut state = self.control.fault.lock().unwrap();
            let request = state.pending.take();
            if request.is_some() {
                state.active = true;
            }
            request
        };
        let Some(request) = request else {
            return Ok(());
        };
        let pid = source.process.0.id();
        if request.pid != pid || source.process.0.try_wait()?.is_some() {
            self.control.fault.lock().unwrap().active = false;
            self.log("[INFO] Ignored a debug fault for a source that already ended.");
            return Ok(());
        }
        {
            let mut state = self.control.fault.lock().unwrap();
            state.active = true;
            state.last = Some(request.kind);
        }
        if let Some(stats) = self.telemetry.lock().unwrap().get(&pid) {
            stats.inject_debug_failure(request.kind);
        }
        match request.kind {
            crate::debug::FaultKind::AudioDrop => source.drop_pid = Some(257),
            crate::debug::FaultKind::VideoDrop => source.drop_pid = Some(256),
            crate::debug::FaultKind::ReadError => {}
            crate::debug::FaultKind::SourcePause | crate::debug::FaultKind::SourceExit => {
                let signal = if matches!(request.kind, crate::debug::FaultKind::SourcePause) {
                    libc::SIGSTOP
                } else {
                    libc::SIGKILL
                };
                let result = unsafe { libc::kill(-(pid as i32), signal) };
                if result < 0 {
                    self.control.fault.lock().unwrap().active = false;
                    self.log("[WARN] Debug signal could not be delivered; the source may already have ended.");
                    return Ok(());
                }
            }
        }
        self.log(&format!("[WARN] Debug source failure applied: kind={}, source_pid={pid}; output_pid={} is retained.", request.kind.name(), self.control.output_pid.load(std::sync::atomic::Ordering::Acquire)));
        Ok(())
    }
    fn source_once(&mut self, media: &Media, seek: f64, skippable: bool) -> Result<SourceEnd> {
        self.pump_filler()?;
        let args = self.ffmpeg_args(media, true, seek);
        let mut source = match self.producer(&args, "source") {
            Ok(source) => source,
            Err(e) => {
                return Ok(SourceEnd::Retry {
                    reason: format!("Source launch failed: {e:#}"),
                    position: seek,
                })
            }
        };
        let _debug_scope =
            crate::debug::SourceScope::new(self.control.clone(), source.process.0.id());
        let mut active = false;
        let mut checkpoint = seek;
        let outcome = loop {
            if self.control.stopped() || (skippable && self.control.skipped()) {
                break SourceEnd::Cancelled;
            }
            if active {
                self.apply_debug_fault(&mut source)?;
            }
            source.read()?;
            let progress = self.progress(&source);
            if let Some(p) = progress {
                if active {
                    if let Some(position) = p.common_position() {
                        checkpoint = checkpoint.max(seek + position);
                    }
                }
                if p.input_failed {
                    break SourceEnd::Retry {
                        reason: "FFmpeg reported a source demux/read failure".into(),
                        position: checkpoint,
                    };
                }
            }
            let ready = (active || super::transport::ready(&source.pending)?)
                && source.pending.len() >= PACKET
                && (progress.is_some_and(|p| p.video_packets > 0 && p.audio_packets > 0)
                    || (!self.stats_supported && source.pending.len() >= PACKET * 128));
            if !active && ready {
                self.stop_filler();
                if self.control.stopped() || (skippable && self.control.skipped()) {
                    break SourceEnd::Cancelled;
                }
                if self.bridge.is_none() {
                    self.log("[INFO] Initial selected-source audio/video buffered; opening the output with source media.");
                    self.start_bridge()?;
                } else {
                    self.log(&format!("[INFO] Source A/V ready at content position {seek:.3}s; handing off within the existing output session."));
                }
                self.app.status("Streaming");
                active = true;
                self.control
                    .source_ready
                    .store(true, std::sync::atomic::Ordering::Release);
            }
            if active {
                self.forward(&mut source)?;
            } else {
                self.pump_filler()?;
            }
            if let Some(status) = source.process.0.try_wait()? {
                if source.eof && source.pending.len() < PACKET {
                    let early = self.stats_supported
                        && media.duration.is_some_and(|d| checkpoint + 3.0 < d);
                    let live = media.id.is_none() && media.duration.is_none();
                    if status.success() && !early && !live {
                        break SourceEnd::Complete;
                    }
                    break SourceEnd::Retry {
                        reason: format!("Source exited with {status}; premature_end={early}, live_source={live}"), position: checkpoint,
                    };
                }
                // A failed startup may have only one stream; do not wait forever
                // for readiness when its remaining bytes cannot be used.
                if !active {
                    break SourceEnd::Retry {
                        reason: format!("Source exited before A/V readiness: {status}"),
                        position: checkpoint,
                    };
                }
            }
            if !active && source.started.elapsed() > STARTUP_TIMEOUT {
                break SourceEnd::Retry {
                    reason: "Source did not produce both audio and video within 30 seconds".into(),
                    position: checkpoint,
                };
            }
            if active {
                let stalled = progress.and_then(|p| {
                    if p.audio_idle.is_some_and(|age| age > STALL_TIMEOUT) {
                        Some("audio")
                    } else if p.video_idle.is_some_and(|age| age > STALL_TIMEOUT) {
                        Some("video")
                    } else {
                        None
                    }
                });
                if let Some(track) = stalled {
                    break SourceEnd::Retry {
                        reason: format!(
                            "Source {track} packets stopped advancing for over 8 seconds"
                        ),
                        position: checkpoint,
                    };
                }
                if source.last_data.elapsed() > STALL_TIMEOUT {
                    break SourceEnd::Retry {
                        reason: "Source media transport stalled for over 8 seconds".into(),
                        position: checkpoint,
                    };
                }
            }
            self.report_processes(None);
            thread::sleep(Duration::from_millis(10));
        };
        self.control
            .source_ready
            .store(false, std::sync::atomic::Ordering::Release);
        if !self.control.stopped() {
            // Preserve an already-open output during cleanup/recovery. Before
            // initial source readiness this deliberately does not create fallback.
            self.pump_filler()?;
        }
        self.stop_producer(source, false);
        Ok(outcome)
    }
    pub(super) fn play_resilient(&mut self, media: &Media, skippable: bool) -> Result<()> {
        if !self.stats_supported {
            self.log("[WARN] FFmpeg per-track stats unavailable: transport/exit recovery remains enabled, but audio-only stall detection requires a full FFmpeg 8+ build.");
        }
        let mut current = media.clone();
        let mut position = 0.0;
        let mut attempt = 0;
        loop {
            match self.source_once(&current, position, skippable)? {
                SourceEnd::Complete => {
                    self.log(
                        "[INFO] Source completed normally; persistent output session remains active.",
                    );
                    return Ok(());
                }
                SourceEnd::Cancelled => return Ok(()),
                SourceEnd::Retry {
                    reason,
                    position: checkpoint,
                } => {
                    if self.control.stopped() || (skippable && self.control.skipped()) {
                        return Ok(());
                    }
                    self.control
                        .recoveries
                        .fetch_add(1, std::sync::atomic::Ordering::AcqRel);
                    let output_state = if self.bridge.is_some() {
                        "Keeping output active."
                    } else {
                        "Output remains closed until selected-source A/V is ready."
                    };
                    self.log(&format!("[WARN] Source recovery required: {reason}; last common A/V content position={checkpoint:.3}s. {output_state}"));
                    if attempt == MAX_RECOVERIES {
                        if skippable {
                            bail!("Source recovery exhausted after {MAX_RECOVERIES} retries: {reason}");
                        }
                        if self.bridge.is_some() {
                            self.app.status("Source unavailable; fallback active");
                            self.log("[WARN] Live source retries exhausted; retaining fallback and output, retrying after 30 seconds.");
                        } else {
                            self.app.status("Waiting for source…");
                            self.log("[WARN] Initial live source retries exhausted; output remains closed, retrying after 30 seconds.");
                        }
                        self.slate(30.0)?;
                        if self.control.stopped() {
                            return Ok(());
                        }
                        attempt = 0;
                    } else {
                        attempt += 1;
                    }
                    if current.duration.is_none() && current.id.is_some() {
                        self.log("[WARN] Finite clip duration is unavailable; recovery must reopen from its beginning rather than seek.");
                    }
                    position = if current.duration.is_some() {
                        (checkpoint - 0.25).max(0.0)
                    } else {
                        0.0
                    };
                    if current
                        .duration
                        .is_some_and(|duration| position >= duration - 0.5)
                    {
                        return Ok(());
                    }
                    self.app.status(if self.bridge.is_some() {
                        "Recovering source"
                    } else {
                        "Preparing source…"
                    });
                    // Refresh signed YouTube URLs; a reconnect to an expired URL is insufficient.
                    match self.refresh_media(current.clone(), skippable) {
                        Ok(media) => current = media,
                        Err(e) => {
                            if self.control.stopped() || (skippable && self.control.skipped()) {
                                return Ok(());
                            }
                            self.log(&format!("[WARN] Refreshing source URLs failed: {e:#}; retrying within the recovery limit."));
                            self.slate(2.0)?;
                        }
                    }
                }
            }
        }
    }
}
