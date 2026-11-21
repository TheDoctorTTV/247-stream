//! Server-managed recovery and transition media. Client filenames never select paths.
use crate::{process, runtime::App};
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    os::unix::fs::OpenOptionsExt,
    path::PathBuf,
    time::{Duration, SystemTime},
};

pub const MAX_BYTES: usize = 10 * 1024 * 1024;
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Role {
    Recovery,
    Transition,
}
impl Role {
    fn name(self) -> &'static str {
        match self {
            Self::Recovery => "recovery",
            Self::Transition => "transition",
        }
    }
    fn endpoint(self) -> &'static str {
        match self {
            Self::Recovery => "/api/fallback-image",
            Self::Transition => "/api/transition-media",
        }
    }
    fn manifest(self, app: &App) -> PathBuf {
        app.dir.join(format!("{}-media.json", self.name()))
    }
}
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Stamp {
    modified: SystemTime,
    bytes: u64,
}

pub struct Asset {
    pub path: PathBuf,
    pub kind: String,
    pub duration: f64,
    pub audio: bool,
    temporary: bool,
}
impl Drop for Asset {
    fn drop(&mut self) {
        if self.temporary {
            let _ = fs::remove_file(&self.path);
        }
    }
}
impl Asset {
    fn extension(&self) -> &'static str {
        match self.kind.as_str() {
            "image/png" => "png",
            "image/gif" => "gif",
            "video/mp4" => "mp4",
            _ => "webm",
        }
    }
    pub fn video(&self) -> bool {
        self.kind.starts_with("video/")
    }
    pub fn input(&self, args: &mut Vec<String>, looping: bool, fps: i64) {
        args.push("-re".into());
        if self.kind == "image/gif" {
            args.extend(process::args(&["-stream_loop", "-1", "-ignore_loop", "1"]));
        } else if self.video() {
            if looping {
                args.extend(process::args(&["-stream_loop", "-1"]));
            }
        } else {
            args.extend(process::args(&[
                "-f",
                "image2",
                "-loop",
                "1",
                "-framerate",
                &fps.to_string(),
            ]));
        }
        args.extend(process::args(&[
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            &self.path.to_string_lossy(),
        ]));
    }
}
fn descriptor(app: &App, role: Role) -> Result<Option<Asset>> {
    let manifest = role.manifest(app);
    if !manifest.exists() {
        let legacy = app.dir.join("fallback-image.png");
        return Ok(
            (role == Role::Recovery && legacy.is_file()).then_some(Asset {
                path: legacy,
                kind: "image/png".into(),
                duration: 5.0,
                audio: false,
                temporary: false,
            }),
        );
    }
    let data: Value = serde_json::from_slice(&fs::read(manifest)?)?;
    let Some(name) = data["file"].as_str() else {
        return Ok(None);
    };
    let prefix = format!("{}-media-", role.name());
    anyhow::ensure!(
        name.starts_with(&prefix)
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'.'),
        "Invalid media filename"
    );
    let kind = data["kind"].as_str().context("Missing media type")?;
    anyhow::ensure!(
        ["image/png", "image/gif", "video/mp4", "video/webm"].contains(&kind),
        "Invalid media type"
    );
    let duration = data["duration"]
        .as_f64()
        .context("Missing media duration")?;
    anyhow::ensure!(
        duration.is_finite() && duration > 0.0,
        "Invalid media duration"
    );
    let path = app.dir.join(name);
    anyhow::ensure!(
        fs::symlink_metadata(&path)?.is_file(),
        "Media file unavailable"
    );
    Ok(Some(Asset {
        path,
        kind: kind.into(),
        duration,
        audio: data["audio"].as_bool().unwrap_or(false),
        temporary: false,
    }))
}
fn stamp(app: &App, role: Role) -> Option<Stamp> {
    let asset = descriptor(app, role).ok()??;
    let m = fs::symlink_metadata(&asset.path).ok()?;
    Some(Stamp {
        modified: m.modified().ok()?,
        bytes: m.len(),
    })
}
pub fn revision(app: &App) -> Option<Stamp> {
    stamp(app, Role::Recovery)
}
/// Hard-link under the upload lock so replacement/removal cannot invalidate playback.
pub fn snapshot(app: &App, role: Role) -> Result<Option<Asset>> {
    let _guard = app.fallback_image_lock.lock().unwrap();
    let Some(mut asset) = descriptor(app, role)? else {
        return Ok(None);
    };
    let path = app.dir.join(format!(
        "playout-media-{:016x}.{}",
        rand::random::<u64>(),
        asset.extension()
    ));
    fs::hard_link(&asset.path, &path)?;
    asset.path = path;
    asset.temporary = true;
    Ok(Some(asset))
}
pub fn info(app: &App, role: Role) -> Value {
    let _guard = app.fallback_image_lock.lock().unwrap();
    metadata(descriptor(app, role).ok().flatten().as_ref(), role)
}
fn metadata(asset: Option<&Asset>, role: Role) -> Value {
    json!({"configured":asset.is_some(), "preview_url":asset.map(|_| role.endpoint()),
        "kind":asset.map(|a| a.kind.as_str()), "duration":asset.map(|a| a.duration), "max_upload_bytes":MAX_BYTES})
}
fn media_type(bytes: &[u8]) -> Result<&'static str> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Ok("image/png");
    }
    if bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a") {
        return Ok("image/gif");
    }
    if bytes.len() >= 12 && &bytes[4..8] == b"ftyp" {
        return Ok("video/mp4");
    }
    if bytes.starts_with(b"\x1a\x45\xdf\xa3") {
        return Ok("video/webm");
    }
    anyhow::bail!("Choose a PNG image, GIF, MP4 or WebM video")
}
pub fn store(app: &App, role: Role, bytes: &[u8]) -> Result<Value> {
    anyhow::ensure!(
        !bytes.is_empty() && bytes.len() <= MAX_BYTES,
        "Media must be at most 10 MiB"
    );
    let kind = media_type(bytes)?;
    let extension = match kind {
        "image/png" => "png",
        "image/gif" => "gif",
        "video/mp4" => "mp4",
        _ => "webm",
    };
    let name = format!(
        "{}-media-{:016x}.{extension}",
        role.name(),
        rand::random::<u64>()
    );
    let path = app.dir.join(&name);
    let mut asset = Asset {
        path,
        kind: kind.into(),
        duration: 5.0,
        audio: false,
        temporary: false,
    };
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&asset.path)?;
    asset.temporary = true;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    // Runtime upload validation uses the installed FFmpeg, with local-only input.
    let ffmpeg = process::find(&app.dir, "ffmpeg")
        .context("Install FFmpeg from the Updates tab before uploading media")?;
    let args = process::args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "info",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        &asset.path.to_string_lossy(),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-f",
        "null",
        "-",
    ]);
    let (status, _, log) =
        process::capture(&ffmpeg, &args, &app.dir, Duration::from_secs(20), None)?;
    anyhow::ensure!(
        status.success(),
        "Media could not be decoded; choose a valid image, GIF, MP4 or WebM"
    );
    let dimensions = regex::Regex::new(r"Video:.*?[, ](\d{1,6})x(\d{1,6})[ ,\[]")?;
    let size = dimensions
        .captures(&log)
        .context("Could not determine media dimensions")?;
    let width: u32 = size[1].parse()?;
    let height: u32 = size[2].parse()?;
    anyhow::ensure!(
        (1..=4096).contains(&width) && (1..=4096).contains(&height),
        "Media dimensions must be between 1 and 4096 pixels"
    );
    if asset.video() {
        let duration = regex::Regex::new(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")?;
        let d = duration
            .captures(&log)
            .context("Video must have a finite duration")?;
        asset.duration =
            d[1].parse::<f64>()? * 3600.0 + d[2].parse::<f64>()? * 60.0 + d[3].parse::<f64>()?;
        anyhow::ensure!(
            asset.duration > 0.0 && asset.duration.is_finite(),
            "Video must have a finite duration"
        );
        let input_log = log.split("Stream mapping:").next().unwrap_or(&log);
        asset.audio = input_log.contains("Audio:");
    }
    let _guard = app.fallback_image_lock.lock().unwrap();
    let old = descriptor(app, role).ok().flatten();
    let manifest = role.manifest(app);
    let temporary = manifest.with_extension(format!("{:016x}.upload", rand::random::<u64>()));
    let result = (|| -> Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temporary)?;
        file.write_all(&serde_json::to_vec(
            &json!({"file":name,"kind":kind,"duration":asset.duration,"audio":asset.audio}),
        )?)?;
        file.sync_all()?;
        fs::rename(&temporary, &manifest)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result?;
    asset.temporary = false;
    if let Some(old) = old {
        let _ = fs::remove_file(&old.path);
    }
    app.log(&format!(
        "[INFO] {} media uploaded: {kind}, {width}x{height}.",
        role.name()
    ));
    Ok(metadata(Some(&asset), role))
}
pub fn remove(app: &App, role: Role) -> Result<Value> {
    let _guard = app.fallback_image_lock.lock().unwrap();
    let old = descriptor(app, role).ok().flatten();
    // A tombstone also prevents an old legacy image from reappearing.
    let manifest = role.manifest(app);
    process::write_atomic(&manifest, b"{}")?;
    if let Some(old) = old {
        let _ = fs::remove_file(&old.path);
    }
    app.log(&format!("[INFO] {} media removed.", role.name()));
    Ok(metadata(None, role))
}
pub fn preview(app: &App, role: Role) -> Result<Option<(String, Vec<u8>)>> {
    let _guard = app.fallback_image_lock.lock().unwrap();
    let Some(asset) = descriptor(app, role)? else {
        return Ok(None);
    };
    let mut bytes = Vec::new();
    fs::File::open(&asset.path)?
        .take((MAX_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    anyhow::ensure!(bytes.len() <= MAX_BYTES, "Media exceeds the size limit");
    Ok(Some((asset.kind.clone(), bytes)))
}
