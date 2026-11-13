//! HTTP compatibility layer for the existing dashboard assets and JSON endpoints.
use crate::{config, debug, fallback_image, runtime::App, updates, APP_NAME, REPO, VERSION};
use anyhow::{Context, Result};
use fallback_image::Role;
use serde_json::{json, Value};
use std::{
    io::Read,
    sync::{atomic::Ordering, Arc},
    thread,
    time::Duration,
};
use tiny_http::{Header, Request, Response, Server, StatusCode};
fn respond(request: Request, status: u16, kind: &str, bytes: Vec<u8>) {
    let response = Response::from_data(bytes)
        .with_status_code(StatusCode(status))
        .with_header(Header::from_bytes("Content-Type", kind).unwrap())
        .with_header(Header::from_bytes("Cache-Control", "no-store, max-age=0").unwrap());
    let _ = request.respond(response);
}
fn handle(mut request: Request, app: Arc<App>) {
    let path = request.url().split('?').next().unwrap_or("/").to_owned();
    let method = request.method().as_str().to_owned();
    if method == "GET" && path == "/downloads/stream247-youtube-connect.tar.gz" {
        match crate::youtube_browser::extension_archive() {
            Ok(bytes) => respond(request, 200, "application/gzip", bytes),
            Err(_) => respond(
                request,
                500,
                "text/plain",
                b"Extension download unavailable".to_vec(),
            ),
        }
        return;
    }
    if method == "GET" && path.starts_with("/api/debug/preview/") {
        let name = path.trim_start_matches("/api/debug/preview/");
        match debug::preview_asset(&app, name) {
            Ok(Some((kind, bytes))) => respond(request, 200, kind, bytes),
            Ok(None) => respond(
                request,
                404,
                "text/plain",
                b"Local preview is not ready".to_vec(),
            ),
            Err(_) => respond(
                request,
                400,
                "text/plain",
                b"Local preview asset unavailable".to_vec(),
            ),
        }
        return;
    }
    let media_role = match path.strip_suffix("/info").unwrap_or(&path) {
        "/api/fallback-image" => Some(Role::Recovery),
        "/api/transition-media" => Some(Role::Transition),
        _ => None,
    };
    if method == "GET" && media_role.is_some() && !path.ends_with("/info") {
        match fallback_image::preview(&app, media_role.unwrap()) {
            Ok(Some((kind, bytes))) => respond(request, 200, &kind, bytes),
            Ok(None) => respond(request, 404, "text/plain", b"No media configured".to_vec()),
            Err(_) => respond(request, 400, "text/plain", b"Media unavailable".to_vec()),
        }
        return;
    }
    if method == "GET" {
        let asset = match path.as_str() {
            "/" | "/index.html" => Some((
                "text/html; charset=utf-8",
                include_str!("../web/index.html")
                    .replace("{APP_NAME}", APP_NAME)
                    .replace("{APP_VERSION}", VERSION)
                    .replace("{GITHUB_REPO}", REPO)
                    .into_bytes(),
            )),
            "/assets/style.css" => Some((
                "text/css; charset=utf-8",
                include_bytes!("../web/style.css").to_vec(),
            )),
            "/assets/app.js" => Some((
                "application/javascript; charset=utf-8",
                include_bytes!("../web/app.js").to_vec(),
            )),
            "/assets/vendor/hls.light.min.js" => Some((
                "application/javascript; charset=utf-8",
                include_bytes!("../web/vendor/hls.light.min.js").to_vec(),
            )),
            "/assets/vendor/hls-LICENSE.txt" => Some((
                "text/plain; charset=utf-8",
                [
                    include_str!("../web/vendor/hls-LICENSE.txt"),
                    include_str!("../web/vendor/Apache-2.0.txt"),
                ]
                .join("\n")
                .into_bytes(),
            )),
            "/favicon.ico" | "/icon.ico" => {
                Some(("image/x-icon", include_bytes!("../icon.ico").to_vec()))
            }
            _ => None,
        };
        if let Some((kind, data)) = asset {
            respond(request, 200, kind, data);
            return;
        }
    }
    let result = (|| -> Result<(u16, Value)> {
        let browser_route = path.starts_with("/api/youtube-browser/");
        if browser_route && method != "GET" {
            anyhow::ensure!(
                request
                    .headers()
                    .iter()
                    .any(|h| h.field.equiv("Content-Type")
                        && h.value.as_str().split(';').next().unwrap_or("").trim()
                            == "application/json"),
                "Browser pairing requests require JSON"
            );
        }
        if browser_route && path != "/api/youtube-browser/info" {
            // The browser requires HTTPS for other devices. A local HTTPS proxy
            // reaches this HTTP backend over loopback; direct LAN pairing is refused.
            anyhow::ensure!(
                request
                    .remote_addr()
                    .is_some_and(|addr| addr.ip().is_loopback()),
                "Use HTTPS through a local reverse proxy to connect a browser on another device"
            );
        }
        if path == "/api/youtube-browser/pairing" || path == "/api/youtube-browser/disconnect" {
            if let Some(origin) = request.headers().iter().find(|h| h.field.equiv("Origin")) {
                let origin = reqwest::Url::parse(origin.value.as_str())
                    .context("Invalid dashboard origin")?;
                let host = request
                    .headers()
                    .iter()
                    .find(|h| h.field.equiv("Host"))
                    .map(|h| h.value.as_str())
                    .unwrap_or("");
                let expected = reqwest::Url::parse(&format!("{}://{host}", origin.scheme()))
                    .context("Invalid dashboard host")?;
                anyhow::ensure!(
                    matches!(origin.scheme(), "http" | "https")
                        && origin.origin() == expected.origin(),
                    "Open pairing from the Stream247 dashboard"
                );
            }
        }
        if let Some(role) = media_role {
            if method == "GET" && path.ends_with("/info") {
                return Ok((
                    200,
                    json!({"ok":true,"image":fallback_image::info(&app, role)}),
                ));
            }
            if method == "DELETE" && !path.ends_with("/info") {
                return Ok((
                    200,
                    json!({"ok":true,"image":fallback_image::remove(&app, role)?}),
                ));
            }
        }
        if method == "POST" && media_role.is_some() && !path.ends_with("/info") {
            let mut bytes = Vec::new();
            request
                .as_reader()
                .take((fallback_image::MAX_BYTES + 1) as u64)
                .read_to_end(&mut bytes)?;
            let image = fallback_image::store(&app, media_role.unwrap(), &bytes)?;
            return Ok((200, json!({"ok":true,"image":image})));
        }
        let body = if method == "POST" {
            let mut bytes = Vec::new();
            request
                .as_reader()
                .take(2 * 1024 * 1024 + 1)
                .read_to_end(&mut bytes)?;
            anyhow::ensure!(bytes.len() <= 2 * 1024 * 1024, "Request body too large");
            if bytes.is_empty() {
                json!({})
            } else {
                serde_json::from_slice::<Value>(&bytes).context("Invalid JSON body")?
            }
        } else {
            json!({})
        };
        let browser_token = request
            .headers()
            .iter()
            .find(|h| h.field.equiv("Authorization"))
            .and_then(|h| h.value.as_str().strip_prefix("Bearer "))
            .unwrap_or("");
        let result = match (method.as_str(), path.as_str()) {
            ("GET", "/api/youtube-browser/info") => {
                json!({"ok":true,"browser":crate::youtube_browser::info(&app)})
            }
            ("POST", "/api/youtube-browser/pairing") => {
                json!({"ok":true,"pairing":crate::youtube_browser::begin(&app)?})
            }
            ("POST", "/api/youtube-browser/pair") => {
                let paired = crate::youtube_browser::pair(&app, &body)?;
                json!({"ok":true,"token":paired["token"],"browser":paired["browser"]})
            }
            ("GET", "/api/youtube-browser/client") => {
                json!({"ok":true,"browser":crate::youtube_browser::client_status(&app,browser_token)?})
            }
            ("POST", "/api/youtube-browser/config") => {
                json!({"ok":true,"browser":crate::youtube_browser::configure(&app,browser_token,&body)?})
            }
            ("POST", "/api/youtube-browser/sync") => {
                let synced = crate::youtube_browser::sync(&app, browser_token, &body)?;
                json!({"ok":true,"browser":synced["browser"],"cookie":synced["cookie"]})
            }
            ("POST", "/api/youtube-browser/disconnect") => {
                let disconnected = crate::youtube_browser::disconnect(&app, None)?;
                json!({"ok":true,"browser":disconnected["browser"],"cookie":disconnected["cookie"]})
            }
            ("POST", "/api/youtube-browser/client-disconnect") => {
                let disconnected = crate::youtube_browser::disconnect(&app, Some(browser_token))?;
                json!({"ok":true,"browser":disconnected["browser"],"cookie":disconnected["cookie"]})
            }
            ("GET", "/api/debug/state") => json!({"ok":true,"debug":app.debug_status()}),
            ("POST", "/api/debug/failure") => {
                json!({"ok":true,"failure":app.inject_failure(&body)?})
            }
            ("POST", "/api/debug/test-stream/start") => {
                let source = match body.get("source") {
                    Some(value) => Some(
                        value
                            .as_str()
                            .context("source must be a URL string")?
                            .to_owned(),
                    ),
                    None => None,
                };
                app.start_preview(source)?;
                json!({"ok":true,"preview_url":"/api/debug/preview/index.m3u8"})
            }
            ("POST", "/api/debug/test-stream/skip") => {
                app.skip_preview(&body)?;
                json!({"ok":true,"transition_skip":true})
            }
            ("POST", "/api/debug/test-stream/stop") => {
                app.stop_preview()?;
                json!({"ok":true})
            }
            ("GET", "/api/state") => app.state.lock().unwrap().snapshot(),
            ("GET", "/api/settings") => json!({"ok":true,"settings":app.settings()}),
            ("POST", "/api/settings") => json!({"ok":true,"settings":app.save_settings(&body)?}),
            ("POST", "/api/start") => {
                app.start()?;
                json!({"ok":true})
            }
            ("POST", "/api/stop") => {
                app.stop();
                json!({"ok":true})
            }
            ("POST", "/api/skip") => {
                app.skip();
                json!({"ok":true})
            }
            ("GET", "/api/binaries") => {
                json!({"ok":true,"binaries":updates::initial_status(&app,false)})
            }
            ("POST", "/api/binaries/update") => {
                json!({"ok":true,"binaries":updates::trigger(&app,false,true,&body)})
            }
            ("GET", "/api/app-update") => {
                json!({"ok":true,"app_update":updates::initial_status(&app,true)})
            }
            ("POST", "/api/app-update/check") => {
                json!({"ok":true,"app_update":updates::trigger(&app,true,false,&body)})
            }
            ("POST", "/api/app-update/download") => {
                json!({"ok":true,"app_update":updates::trigger(&app,true,true,&body)})
            }
            _ => return Ok((404, json!({"ok":false,"error":"Not found"}))),
        };
        Ok((200, result))
    })();
    let (status, payload) = match result {
        Ok(v) => v,
        Err(e) => (
            400,
            json!({"ok":false,"error":app.sanitize(&format!("{e:#}"))}),
        ),
    };
    respond(
        request,
        status,
        "application/json; charset=utf-8",
        serde_json::to_vec(&payload).unwrap_or_default(),
    );
}
pub fn serve(app: Arc<App>) -> Result<()> {
    let cfg = app.raw_settings();
    let host = config::text(&cfg, "web_server_host", "0.0.0.0");
    let host = if host.is_empty() {
        "0.0.0.0".into()
    } else {
        host
    };
    let port = config::integer(&cfg, "web_server_port", 7788);
    let port = if (1..=65535).contains(&port) {
        port
    } else {
        7788
    };
    let bind = if host.contains(':') {
        format!("[{host}]:{port}")
    } else {
        format!("{host}:{port}")
    };
    let server = Server::http(&bind).map_err(|e| anyhow::anyhow!("Bind dashboard {bind}: {e}"))?;
    app.log(&format!("[INFO] Web dashboard listening on http://{bind}"));
    app.log(&format!(
        "[INFO] {APP_NAME} version {VERSION}; headless runtime active."
    ));
    crate::diagnostics::start(Arc::clone(&app));
    let startup = Arc::clone(&app);
    thread::spawn(move || updates::startup(startup));
    while !app.shutdown.load(Ordering::Acquire) {
        if let Some(request) = server.recv_timeout(Duration::from_millis(250))? {
            let app = Arc::clone(&app);
            thread::spawn(move || handle(request, app));
        }
    }
    drop(server);
    let reason = *app.shutdown_reason.lock().unwrap();
    app.log(&format!("[INFO] Application shutting down: {reason}."));
    app.stop();
    app.join_worker();
    Ok(())
}
