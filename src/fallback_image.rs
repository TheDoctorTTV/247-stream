//! A server-managed still image for source-recovery output. Uploads never select a path.
use crate::runtime::App;
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    os::unix::fs::OpenOptionsExt,
    path::PathBuf,
    time::SystemTime,
};

pub const MAX_BYTES: usize = 8 * 1024 * 1024;
const NAME: &str = "fallback-image.png";
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Stamp {
    modified: SystemTime,
    bytes: u64,
}
pub fn path(app: &App) -> PathBuf {
    app.dir.join(NAME)
}
pub fn revision(app: &App) -> Option<Stamp> {
    let metadata = fs::symlink_metadata(path(app)).ok()?;
    if !metadata.is_file() {
        return None;
    }
    Some(Stamp {
        modified: metadata.modified().ok()?,
        bytes: metadata.len(),
    })
}
fn dimensions(bytes: &[u8]) -> Result<(u32, u32)> {
    anyhow::ensure!(
        bytes.len() <= MAX_BYTES && bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "Upload must be a PNG image of at most 8 MiB"
    );
    let mut at = 8usize;
    let mut size = None;
    let mut data_seen = false;
    while at + 12 <= bytes.len() {
        let length = u32::from_be_bytes(bytes[at..at + 4].try_into().unwrap()) as usize;
        let end = at
            .checked_add(12)
            .and_then(|v| v.checked_add(length))
            .context("Invalid PNG chunk size")?;
        anyhow::ensure!(end <= bytes.len(), "Incomplete PNG image");
        let kind = &bytes[at + 4..at + 8];
        let mut crc = !0u32;
        for byte in &bytes[at + 4..end - 4] {
            crc ^= *byte as u32;
            for _ in 0..8 {
                crc = (crc >> 1) ^ (0xedb88320 & 0u32.wrapping_sub(crc & 1));
            }
        }
        anyhow::ensure!(
            !crc == u32::from_be_bytes(bytes[end - 4..end].try_into().unwrap()),
            "PNG checksum mismatch"
        );
        anyhow::ensure!(size.is_some() || kind == b"IHDR", "PNG header missing");
        if kind == b"IHDR" {
            anyhow::ensure!(
                size.is_none() && at == 8 && length == 13,
                "Invalid PNG header"
            );
            let header = &bytes[at + 8..end - 4];
            let w = u32::from_be_bytes(header[..4].try_into().unwrap());
            let h = u32::from_be_bytes(header[4..8].try_into().unwrap());
            anyhow::ensure!(
                (1..=4096).contains(&w) && (1..=4096).contains(&h),
                "Image dimensions must be between 1 and 4096 pixels"
            );
            anyhow::ensure!(
                header[8] == 8 && [2, 6].contains(&header[9]) && header[10..13] == [0, 0, 0],
                "Upload an 8-bit RGB/RGBA still PNG using the dashboard"
            );
            size = Some((w, h));
        } else if kind == b"IDAT" {
            data_seen = true;
        } else if kind == b"acTL" {
            anyhow::bail!("Animated images are not supported");
        } else if kind == b"IEND" {
            anyhow::ensure!(
                length == 0 && end == bytes.len() && data_seen,
                "Incomplete PNG image"
            );
            return size.context("PNG dimensions missing");
        }
        at = end;
    }
    anyhow::bail!("PNG end marker missing")
}
pub fn info(app: &App) -> Value {
    let _guard = app.fallback_image_lock.lock().unwrap();
    let configured = revision(app).is_some();
    json!({"configured":configured,"preview_url":if configured{Some("/api/fallback-image")}else{None},"max_upload_bytes":MAX_BYTES})
}
pub fn store(app: &App, bytes: &[u8]) -> Result<Value> {
    let (width, height) = dimensions(bytes)?;
    anyhow::ensure!(
        width * 9 == height * 16,
        "Recovery image must have a 16:9 aspect ratio; 1920x1080 pixels is recommended"
    );
    let _guard = app.fallback_image_lock.lock().unwrap();
    let temporary = app.dir.join(format!(
        "fallback-image-{}-{}.upload",
        std::process::id(),
        rand::random::<u64>()
    ));
    let result = (|| -> Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, path(app))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result?;
    app.log(&format!(
        "[INFO] Recovery image uploaded: {width}x{height}; it will replace black fallback video."
    ));
    Ok(json!({"configured":true,"preview_url":"/api/fallback-image","width":width,"height":height}))
}
pub fn remove(app: &App) -> Result<Value> {
    let _guard = app.fallback_image_lock.lock().unwrap();
    match fs::remove_file(path(app)) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e.into()),
    }
    app.log("[INFO] Recovery image removed; fallback video will be black.");
    Ok(json!({"configured":false,"preview_url":null}))
}
pub fn preview(app: &App) -> Result<Option<Vec<u8>>> {
    let _guard = app.fallback_image_lock.lock().unwrap();
    let Some(stamp) = revision(app) else {
        return Ok(None);
    };
    anyhow::ensure!(
        stamp.bytes <= MAX_BYTES as u64,
        "Recovery image exceeds the size limit"
    );
    let bytes = fs::read(path(app))?;
    dimensions(&bytes)?;
    Ok(Some(bytes))
}
