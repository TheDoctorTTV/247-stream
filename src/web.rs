//! HTTP compatibility layer for the existing dashboard assets and JSON endpoints.
use crate::{config, runtime::App, updates, APP_NAME, REPO, VERSION};
use anyhow::{Context, Result};
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
        let result = match (method.as_str(), path.as_str()) {
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
