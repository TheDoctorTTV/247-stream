//! Private cookie storage for paired-browser sync. Cookie contents are never served.
use crate::runtime::App;
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::{
    fs::{self, DirBuilder, OpenOptions, Permissions},
    io::{Read, Write},
    os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt},
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};

pub const MAX_BYTES: usize = 256 * 1024;
static SERIAL: AtomicU64 = AtomicU64::new(0);

pub(crate) fn directory(app: &App) -> Result<PathBuf> {
    let dir = app.dir.join("youtube-auth");
    match DirBuilder::new().mode(0o700).create(&dir) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => anyhow::bail!("Could not create private cookie storage"),
    }
    anyhow::ensure!(
        fs::symlink_metadata(&dir)?.is_dir(),
        "Invalid cookie storage directory"
    );
    fs::set_permissions(&dir, Permissions::from_mode(0o700))?;
    Ok(dir)
}

fn validate(bytes: &[u8]) -> Result<(String, Vec<String>)> {
    anyhow::ensure!(
        !bytes.is_empty() && bytes.len() <= MAX_BYTES,
        "Cookie file must be between 1 byte and 256 KiB"
    );
    let text = std::str::from_utf8(bytes)
        .map_err(|_| anyhow::anyhow!("Cookie file must be UTF-8 text"))?;
    let mut lines = text.trim_start_matches('\u{feff}').lines();
    anyhow::ensure!(
        matches!(
            lines.next(),
            Some("# Netscape HTTP Cookie File" | "# HTTP Cookie File")
        ),
        "Export cookies in Netscape cookies.txt format"
    );
    let mut output = String::from("# Netscape HTTP Cookie File\n");
    let mut secrets = Vec::new();
    let mut youtube = false;
    for line in lines {
        let row = line.strip_prefix("#HttpOnly_").unwrap_or(line);
        if row.is_empty() || row.starts_with('#') {
            continue;
        }
        let fields: Vec<&str> = row.split('\t').collect();
        anyhow::ensure!(
            fields.len() == 7 && !row.chars().any(|c| c.is_control() && c != '\t'),
            "Invalid Netscape cookie entry"
        );
        let domain = fields[0].trim_start_matches('.').to_ascii_lowercase();
        let is_youtube = domain == "youtube.com" || domain.ends_with(".youtube.com");
        anyhow::ensure!(is_youtube, "Cookie sync accepts only YouTube cookies");
        anyhow::ensure!(
            matches!(fields[1], "TRUE" | "FALSE")
                && matches!(fields[3], "TRUE" | "FALSE")
                && fields[2].starts_with('/')
                && fields[4].parse::<u64>().is_ok()
                && !fields[5].is_empty(),
            "Invalid Netscape cookie entry"
        );
        youtube |= is_youtube;
        if !fields[6].is_empty() {
            // Redact complete assignments for all cookies. Standalone short
            // preference values (such as 1) must not erase unrelated log text.
            secrets.push(format!("{}={}", fields[5], fields[6]));
            let name = fields[5].to_ascii_uppercase();
            if fields[6].len() >= 8 || name.contains("SID") || name == "LOGIN_INFO" {
                secrets.push(fields[6].to_owned());
            }
        }
        output.push_str(line);
        output.push('\n');
    }
    anyhow::ensure!(
        youtube && !secrets.is_empty(),
        "No YouTube cookies found in this file"
    );
    Ok((output, secrets))
}

fn register(app: &App, secrets: Vec<String>) {
    let mut state = app.state.lock().unwrap();
    for secret in secrets {
        state.register_secret(&secret);
    }
}

fn read_saved(app: &App) -> Result<Option<String>> {
    let path = directory(app)?.join("cookies.txt");
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
    {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => anyhow::bail!("Could not open saved YouTube cookies"),
    };
    anyhow::ensure!(file.metadata()?.is_file(), "Invalid cookie storage file");
    file.set_permissions(Permissions::from_mode(0o600))?;
    let mut bytes = Vec::new();
    file.take((MAX_BYTES + 1) as u64).read_to_end(&mut bytes)?;
    let (text, secrets) = validate(&bytes)?;
    register(app, secrets);
    Ok(Some(text))
}

pub fn initialize(app: &App) -> Result<()> {
    let _guard = app.youtube_cookie_lock.lock().unwrap();
    if app.dir.join("youtube-auth").exists() {
        read_saved(app)?;
    }
    Ok(())
}

pub fn info(app: &App) -> Value {
    let _guard = app.youtube_cookie_lock.lock().unwrap();
    let dir = app.dir.join("youtube-auth");
    let configured = fs::symlink_metadata(&dir).is_ok_and(|m| m.is_dir())
        && fs::symlink_metadata(dir.join("cookies.txt")).is_ok_and(|m| m.is_file());
    json!({"configured": configured})
}

pub struct CookieFile(pub PathBuf);
impl Drop for CookieFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}
fn temporary(app: &App, text: &str) -> Result<CookieFile> {
    let dir = directory(app)?;
    // Exclusive creation and a fresh name avoid overwriting a live resolver's jar.
    for _ in 0..100 {
        let path = dir.join(format!(
            "cookie-{}-{}.txt",
            std::process::id(),
            SERIAL.fetch_add(1, Ordering::Relaxed)
        ));
        let mut file = match OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
        {
            Ok(file) => file,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => anyhow::bail!("Could not save private YouTube cookies"),
        };
        let temporary = CookieFile(path);
        file.write_all(text.as_bytes())
            .context("Could not save private YouTube cookies")?;
        file.sync_all()
            .context("Could not save private YouTube cookies")?;
        return Ok(temporary);
    }
    anyhow::bail!("Could not allocate private cookie storage")
}

pub fn store(app: &App, bytes: &[u8]) -> Result<Value> {
    let (text, secrets) = validate(bytes)?;
    let _guard = app.youtube_cookie_lock.lock().unwrap();
    register(app, secrets);
    let temporary = temporary(app, &text)?;
    fs::rename(&temporary.0, directory(app)?.join("cookies.txt"))
        .context("Could not replace saved YouTube cookies")?;
    drop(_guard);
    app.log("[INFO] YouTube cookies saved.");
    Ok(info(app))
}

pub fn remove(app: &App) -> Result<Value> {
    let _guard = app.youtube_cookie_lock.lock().unwrap();
    let path = directory(app)?.join("cookies.txt");
    match fs::remove_file(path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => anyhow::bail!("Could not remove saved YouTube cookies"),
    }
    drop(_guard);
    // Keep old secrets registered while in-flight resolvers finish.
    app.log("[INFO] YouTube cookies removed.");
    Ok(info(app))
}

pub fn snapshot(app: &App) -> Result<Option<CookieFile>> {
    let _guard = app.youtube_cookie_lock.lock().unwrap();
    if !app.dir.join("youtube-auth").exists() {
        return Ok(None);
    }
    read_saved(app)?
        .map(|text| temporary(app, &text))
        .transpose()
}

// Used only for fixed application-owned names, never client-supplied paths.
pub(crate) fn write_private(app: &App, name: &str, text: &str) -> Result<()> {
    let temporary = temporary(app, text)?;
    fs::rename(&temporary.0, directory(app)?.join(name))
        .context("Could not save private authentication state")?;
    Ok(())
}

pub(crate) fn read_private(app: &App, name: &str) -> Result<Option<Vec<u8>>> {
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(directory(app)?.join(name))
    {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => anyhow::bail!("Could not read private authentication state"),
    };
    anyhow::ensure!(
        file.metadata()?.is_file(),
        "Invalid authentication storage file"
    );
    file.set_permissions(Permissions::from_mode(0o600))?;
    let mut bytes = Vec::new();
    file.take(16385).read_to_end(&mut bytes)?;
    anyhow::ensure!(bytes.len() <= 16384, "Invalid authentication storage size");
    Ok(Some(bytes))
}
