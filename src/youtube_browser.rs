//! One explicitly paired browser; credentials never appear in dashboard status.
use crate::{runtime::App, state::now, youtube_cookie};
use anyhow::{Context, Result};
use rand::RngCore;
use serde_json::{json, Value};
use std::fs;

const STATE_FILE: &str = "browser.json";
#[derive(Default)]
pub struct BrowserState {
    code: String,
    expires: f64,
    token: String,
    device: String,
    auto_sync: bool,
    last_sync: Option<f64>,
}
fn random_hex(bytes: usize) -> Result<String> {
    let mut data = vec![0u8; bytes];
    rand::rngs::OsRng
        .try_fill_bytes(&mut data)
        .map_err(|_| anyhow::anyhow!("Could not generate browser pairing credentials"))?;
    Ok(data.iter().map(|b| format!("{b:02x}")).collect())
}
fn equal(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.bytes()
        .zip(b.bytes())
        .fold(0u8, |diff, (a, b)| diff | (a ^ b))
        == 0
}
fn status(state: &BrowserState) -> Value {
    json!({"connected":!state.token.is_empty(), "device":state.device,
        "auto_sync":state.auto_sync, "last_sync":state.last_sync,
        "pairing_expires_at":if state.expires > now() { Some(state.expires) } else { None }})
}
fn persist(app: &App, state: &BrowserState) -> Result<()> {
    youtube_cookie::write_private(
        app,
        STATE_FILE,
        &json!({"token":state.token,
        "device":state.device,"auto_sync":state.auto_sync,"last_sync":state.last_sync})
        .to_string(),
    )
}
fn authorize(state: &BrowserState, token: &str) -> Result<()> {
    anyhow::ensure!(
        !state.token.is_empty() && equal(&state.token, token),
        "Browser pairing is no longer valid"
    );
    Ok(())
}
pub fn initialize(app: &App) -> Result<()> {
    if !app.dir.join("youtube-auth").exists() {
        return Ok(());
    }
    if let Some(bytes) = youtube_cookie::read_private(app, STATE_FILE)? {
        let saved: Value = serde_json::from_slice(&bytes)
            .map_err(|_| anyhow::anyhow!("Invalid saved browser pairing"))?;
        let token = saved["token"].as_str().unwrap_or("");
        anyhow::ensure!(
            token.len() == 64 && token.bytes().all(|b| b.is_ascii_hexdigit()),
            "Invalid saved browser pairing"
        );
        app.register_secret(token);
        let mut state = app.youtube_browser.lock().unwrap();
        state.token = token.into();
        state.device = saved["device"].as_str().unwrap_or("Browser").into();
        state.auto_sync = saved["auto_sync"].as_bool().unwrap_or(false);
        state.last_sync = saved["last_sync"].as_f64();
    }
    Ok(())
}
pub fn info(app: &App) -> Value {
    status(&app.youtube_browser.lock().unwrap())
}
pub fn begin(app: &App) -> Result<Value> {
    let code = random_hex(12)?;
    app.register_secret(&code);
    let display = code
        .as_bytes()
        .chunks(4)
        .map(|part| std::str::from_utf8(part).unwrap())
        .collect::<Vec<_>>()
        .join("-");
    app.register_secret(&display);
    let mut state = app.youtube_browser.lock().unwrap();
    state.code = code;
    state.expires = now() + 600.0;
    Ok(json!({"code":display,"expires_at":state.expires}))
}
pub fn pair(app: &App, body: &Value) -> Result<Value> {
    let code = body["code"]
        .as_str()
        .unwrap_or("")
        .replace('-', "")
        .to_ascii_lowercase();
    let mut state = app.youtube_browser.lock().unwrap();
    anyhow::ensure!(
        !state.code.is_empty() && state.expires > now() && equal(&state.code, &code),
        "Pairing code is invalid or expired"
    );
    let token = random_hex(32)?;
    app.register_secret(&token);
    let device: String = body["device"]
        .as_str()
        .unwrap_or("Browser")
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(*c, ' ' | '-' | '_' | '.'))
        .take(64)
        .collect();
    let paired = BrowserState {
        token: token.clone(),
        device: if device.is_empty() {
            "Browser".into()
        } else {
            device
        },
        auto_sync: body["auto_sync"].as_bool().unwrap_or(false),
        ..BrowserState::default()
    };
    persist(app, &paired)?;
    *state = paired;
    let result = json!({"token":token,"browser":status(&state)});
    drop(state);
    app.log("[INFO] YouTube browser paired.");
    Ok(result)
}
pub fn client_status(app: &App, token: &str) -> Result<Value> {
    let state = app.youtube_browser.lock().unwrap();
    authorize(&state, token)?;
    Ok(status(&state))
}
pub fn configure(app: &App, token: &str, body: &Value) -> Result<Value> {
    let mut state = app.youtube_browser.lock().unwrap();
    authorize(&state, token)?;
    let enabled = body["auto_sync"]
        .as_bool()
        .context("Choose whether to enable automatic sync")?;
    let old = state.auto_sync;
    state.auto_sync = enabled;
    if let Err(e) = persist(app, &state) {
        state.auto_sync = old;
        return Err(e);
    }
    Ok(status(&state))
}
pub fn sync(app: &App, token: &str, body: &Value) -> Result<Value> {
    let mut state = app.youtube_browser.lock().unwrap();
    authorize(&state, token)?;
    match body["mode"].as_str() {
        Some("manual") => {}
        Some("automatic") => anyhow::ensure!(state.auto_sync, "Automatic sync is disabled"),
        _ => anyhow::bail!("Invalid cookie sync mode"),
    }
    let cookies = body["cookies"]
        .as_str()
        .context("Missing YouTube cookies")?;
    // Browser sync is deliberately limited to YouTube, excluding Google cookies.
    for line in cookies.lines().skip(1) {
        let row = line.strip_prefix("#HttpOnly_").unwrap_or(line);
        if row.is_empty() || row.starts_with('#') {
            continue;
        }
        let domain = row
            .split('\t')
            .next()
            .unwrap_or("")
            .trim_start_matches('.')
            .to_ascii_lowercase();
        anyhow::ensure!(
            domain == "youtube.com" || domain.ends_with(".youtube.com"),
            "Browser sync accepts only YouTube cookies"
        );
    }
    let cookie = if cookies.is_empty() {
        youtube_cookie::remove(app)?
    } else {
        youtube_cookie::store(app, cookies.as_bytes())?
    };
    state.last_sync = Some(now());
    persist(app, &state)?;
    Ok(json!({"browser":status(&state),"cookie":cookie}))
}
pub fn disconnect(app: &App, token: Option<&str>) -> Result<Value> {
    let mut state = app.youtube_browser.lock().unwrap();
    if let Some(token) = token {
        authorize(&state, token)?;
    }
    match fs::remove_file(youtube_cookie::directory(app)?.join(STATE_FILE)) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => anyhow::bail!("Could not revoke browser pairing"),
    }
    *state = BrowserState::default();
    let cookie = youtube_cookie::remove(app)?;
    Ok(json!({"browser":status(&state),"cookie":cookie}))
}

pub fn extension_archive() -> Result<Vec<u8>> {
    let encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
    let mut archive = tar::Builder::new(encoder);
    let files: &[(&str, &[u8])] = &[
        (
            "manifest.json",
            include_bytes!("../browser-extension/manifest.json"),
        ),
        (
            "background.js",
            include_bytes!("../browser-extension/background.js"),
        ),
        (
            "connect.html",
            include_bytes!("../browser-extension/connect.html"),
        ),
        (
            "connect.js",
            include_bytes!("../browser-extension/connect.js"),
        ),
        (
            "connect.css",
            include_bytes!("../browser-extension/connect.css"),
        ),
        (
            "README.md",
            include_bytes!("../browser-extension/README.md"),
        ),
    ];
    for (name, bytes) in files {
        let mut header = tar::Header::new_gnu();
        header.set_size(bytes.len() as u64);
        header.set_mode(0o644);
        header.set_cksum();
        archive.append_data(
            &mut header,
            format!("stream247-youtube-connect/{name}"),
            *bytes,
        )?;
    }
    Ok(archive.into_inner()?.finish()?)
}
