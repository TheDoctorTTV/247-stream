//! Tool provisioning and GitHub release-channel updates, using the dashboard's existing schema.
use crate::{
    config::{boolean, text},
    process,
    runtime::App,
    state::now,
    REPO, VERSION,
};
use anyhow::{bail, Context, Result};
use reqwest::blocking::Client;
use serde_json::{json, Value};
use std::{
    cmp::Ordering,
    fs::{self, File},
    io::{Read, Write},
    path::Path,
    sync::{Arc, OnceLock},
    time::{Duration, Instant},
};

pub fn client(timeout: Duration) -> Result<Client> {
    Ok(Client::builder()
        .user_agent(format!("Stream247/{VERSION}"))
        .timeout(timeout)
        .build()?)
}
fn get_json(url: &str) -> Result<Value> {
    Ok(client(Duration::from_secs(20))?
        .get(url)
        .send()?
        .error_for_status()?
        .json()?)
}
fn version_parts(v: &str) -> (Vec<u64>, Vec<String>) {
    let v = v.trim().trim_start_matches(['v', 'V']).to_lowercase();
    let n = v
        .find(|c: char| !c.is_ascii_digit() && c != '.')
        .unwrap_or(v.len());
    let core = v[..n]
        .trim_end_matches('.')
        .split('.')
        .filter_map(|s| s.parse().ok())
        .collect();
    let suffix = v[n..]
        .replace("pre-release", "prerelease")
        .replace("pre release", "prerelease");
    let re = regex::Regex::new(r"[a-z]+|\d+").unwrap();
    (
        core,
        re.find_iter(&suffix)
            .map(|m| m.as_str().to_owned())
            .collect(),
    )
}
pub fn compare(a: &str, b: &str) -> Ordering {
    let (ac, at) = version_parts(a);
    let (bc, bt) = version_parts(b);
    for i in 0..ac.len().max(bc.len()) {
        let c = ac.get(i).unwrap_or(&0).cmp(bc.get(i).unwrap_or(&0));
        if c != Ordering::Equal {
            return c;
        }
    }
    match (at.is_empty(), bt.is_empty()) {
        (true, false) => return Ordering::Greater,
        (false, true) => return Ordering::Less,
        _ => {}
    }
    for (a, b) in at.iter().zip(bt.iter()) {
        let c = match (a.parse::<u64>(), b.parse::<u64>()) {
            (Ok(a), Ok(b)) => a.cmp(&b),
            (Ok(_), Err(_)) => Ordering::Less,
            (Err(_), Ok(_)) => Ordering::Greater,
            _ => a.cmp(b),
        };
        if c != Ordering::Equal {
            return c;
        }
    }
    at.len().cmp(&bt.len())
}
fn installable(name: &str) -> bool {
    !name.is_empty()
        && ![
            ".sh",
            ".ps1",
            ".bat",
            ".cmd",
            ".msi",
            ".exe",
            ".appimage",
            ".zip",
            ".tar.gz",
            ".tgz",
        ]
        .iter()
        .any(|ext| name.to_lowercase().ends_with(ext))
}
pub fn release(channel: &str, selected: &str) -> Result<Value> {
    if !selected.is_empty() && compare(selected, "2.0-pre-release-2") == Ordering::Less {
        bail!("Selected version is below the minimum supported in-app target");
    }
    let data = get_json(&format!(
        "https://api.github.com/repos/{REPO}/releases?per_page=100"
    ))?;
    let mut releases: Vec<&Value> = data
        .as_array()
        .context("Invalid GitHub releases response")?
        .iter()
        .filter(|r| {
            !boolean(r, "draft", false)
                && boolean(r, "prerelease", false) == (channel == "prerelease")
                && compare(&text(r, "tag_name", ""), "2.0-pre-release-2") != Ordering::Less
        })
        .collect();
    releases.sort_by(|a, b| compare(&text(b, "tag_name", ""), &text(a, "tag_name", "")));
    let latest = *releases
        .first()
        .context("No supported release found for this channel")?;
    let chosen = if selected.is_empty() {
        latest
    } else {
        releases
            .iter()
            .copied()
            .find(|r| compare(&text(r, "tag_name", ""), selected) == Ordering::Equal)
            .context("Selected version was not found in this channel")?
    };
    let mut assets = chosen["assets"].as_array().cloned().unwrap_or_default();
    assets.sort_by_key(|a| {
        let n = text(a, "name", "").to_lowercase();
        (
            (!installable(&n)) as u8,
            (!n.ends_with(".tar.gz") && !n.ends_with(".zip") && !n.ends_with(".tgz")) as u8,
            (!n.contains("linux")) as u8,
            (!n.contains("server")) as u8,
            (!n.contains("stream247")) as u8,
        )
    });
    let asset = assets.first().cloned().unwrap_or(Value::Null);
    let version = text(chosen, "tag_name", "")
        .trim_start_matches(['v', 'V'])
        .to_owned();
    let name = text(&asset, "name", "");
    Ok(
        json!({"current_version":VERSION,"latest_version":version,"selected_version":version,"requested_version":selected,"latest_channel_version":text(latest,"tag_name","").trim_start_matches(['v','V']),"is_newer":compare(&version,VERSION)==Ordering::Greater,"should_install":compare(&version,VERSION)==Ordering::Greater,"release_url":text(chosen,"html_url",""),"channel":channel,"download_url":text(&asset,"browser_download_url",""),"asset_name":name,"asset_supported":installable(&name),"available_versions":releases.iter().map(|r|text(r,"tag_name","").trim_start_matches(['v','V']).to_owned()).collect::<Vec<_>>()}),
    )
}
pub fn download(url: &str, path: &Path, progress: impl Fn(u64, u64)) -> Result<()> {
    let temp = path.with_extension("download-part");
    let result = (|| -> Result<()> {
        let mut response = client(Duration::from_secs(1800))?
            .get(url)
            .send()?
            .error_for_status()?;
        let total = response.content_length().unwrap_or(0);
        let mut done = 0;
        let mut file = File::create(&temp)?;
        let mut buffer = [0u8; 65536];
        let started = Instant::now();
        let mut last = Instant::now() - Duration::from_secs(1);
        loop {
            let n = response.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            file.write_all(&buffer[..n])?;
            done += n as u64;
            // Keep the existing 50 MB/s download cap without busy-spinning.
            let expected = Duration::from_secs_f64(done as f64 / (50.0 * 1024.0 * 1024.0));
            if expected > started.elapsed() {
                std::thread::sleep(expected.saturating_sub(started.elapsed()));
            }
            if last.elapsed() >= Duration::from_millis(200) {
                progress(done, total);
                last = Instant::now();
            }
        }
        if total > 0 && done != total {
            bail!("Incomplete download: {done}/{total} bytes");
        }
        file.sync_all()?;
        drop(file);
        progress(done, total);
        fs::rename(&temp, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}
pub fn tool_version(path: Option<&Path>, tool: &str, dir: &Path) -> Option<String> {
    let path = path?;
    let flag = if tool == "ffmpeg" {
        "-version"
    } else {
        "--version"
    };
    let (status, out, _) = process::capture(
        path,
        &process::args(&[flag]),
        dir,
        Duration::from_secs(8),
        None,
    )
    .ok()?;
    if !status.success() {
        return None;
    }
    if tool == "ffmpeg" {
        let re = regex::Regex::new(r"ffmpeg version\s+([^\s]+)").ok()?;
        let raw = re.captures(&out)?.get(1)?.as_str();
        let re = regex::Regex::new(r"\d+\.\d+(?:\.\d+)?").ok()?;
        Some(re.find(raw).map(|m| m.as_str()).unwrap_or(raw).to_owned())
    } else {
        out.lines().next().map(|s| s.trim().to_owned())
    }
}
pub fn binaries_status(dir: &Path) -> Value {
    let mut result = json!({});
    let mut all = true;
    let mut any = false;
    for tool in ["yt-dlp", "ffmpeg"] {
        let path = process::find(dir, tool);
        let current = tool_version(path.as_deref(), tool, dir);
        let latest = if tool == "yt-dlp" {
            get_json("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest")
                .ok()
                .map(|v| text(&v, "tag_name", "").trim_start_matches('v').to_owned())
        } else {
            (|| -> Result<Option<String>> {
                let html = client(Duration::from_secs(15))?
                    .get("https://johnvansickle.com/ffmpeg/")
                    .send()?
                    .error_for_status()?
                    .text()?;
                let re = regex::Regex::new(r"ffmpeg-(\d+\.\d+(?:\.\d+)?)-amd64-static\.tar\.xz")?;
                let mut versions: Vec<String> =
                    re.captures_iter(&html).map(|c| c[1].to_owned()).collect();
                versions.sort_by(|a, b| compare(a, b));
                if let Some(v) = versions.pop() {
                    return Ok(Some(v));
                }
                Ok(regex::Regex::new(r"(?i)release[:\s]+(\d+\.\d+(?:\.\d+)?)")?
                    .captures(&html)
                    .map(|c| c[1].to_owned()))
            })()
            .ok()
            .flatten()
        };
        let status = match (&current, &latest) {
            (Some(a), Some(b)) => {
                if compare(a, b) == Ordering::Less {
                    "update_available"
                } else {
                    "up_to_date"
                }
            }
            _ => "unknown",
        };
        all &= status == "up_to_date";
        any |= status == "update_available";
        result[tool] =
            json!({"path":path,"current_version":current,"latest_version":latest,"status":status});
    }
    result["all_up_to_date"] = json!(all);
    result["any_update_available"] = json!(any);
    result
}
fn healthy(path: &Path, dir: &Path) -> bool {
    process::capture(
        path,
        &process::args(&[
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "0.25",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-f",
            "null",
            "-",
        ]),
        dir,
        Duration::from_secs(8),
        None,
    )
    .is_ok_and(|(s, _, _)| s.success())
}
pub fn ensure_binaries(app: &App, force: bool) -> Result<()> {
    if !cfg!(target_os = "linux") {
        bail!("Automatic tool provisioning currently supports Linux");
    }
    for (tool, base, span) in [("yt-dlp", 10, 35), ("ffmpeg", 60, 30)] {
        let path = app.dir.join(tool);
        if !force && path.exists() && (tool != "ffmpeg" || healthy(&path, &app.dir)) {
            continue;
        }
        app.progress(false, &format!("Downloading {tool}..."), base);
        let staged = app.dir.join(format!("{tool}.new"));
        let result = (|| -> Result<()> {
            if tool == "yt-dlp" {
                let asset = match std::env::consts::ARCH {
                    "x86_64" => "yt-dlp_linux",
                    "aarch64" => "yt-dlp_linux_aarch64",
                    _ => bail!("Unsupported yt-dlp architecture"),
                };
                download(
                    &format!("https://github.com/yt-dlp/yt-dlp/releases/latest/download/{asset}"),
                    &staged,
                    |done, total| {
                        if total > 0 {
                            app.progress(
                                false,
                                &format!("Downloading {tool}... {}%", done * 100 / total),
                                base + (done * span / total) as i64,
                            );
                        }
                    },
                )?;
                process::chmod(&staged)?;
                anyhow::ensure!(
                    tool_version(Some(&staged), tool, &app.dir).is_some(),
                    "Downloaded yt-dlp is not executable"
                );
            } else {
                anyhow::ensure!(
                    std::env::consts::ARCH == "x86_64",
                    "Automatic FFmpeg download requires x86_64"
                );
                let archive = app.dir.join("ffmpeg-latest.tar.xz");
                download(
                    "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
                    &archive,
                    |done, total| {
                        if total > 0 {
                            app.progress(
                                false,
                                &format!("Downloading ffmpeg... {}%", done * 100 / total),
                                base + (done * span / total) as i64,
                            );
                        }
                    },
                )?;
                let mut tar = tar::Archive::new(xz2::read::XzDecoder::new(File::open(&archive)?));
                let mut found = false;
                for entry in tar.entries()? {
                    let mut entry = entry?;
                    if entry.header().entry_type().is_file()
                        && entry.path()?.file_name().is_some_and(|s| s == "ffmpeg")
                    {
                        let mut file = File::create(&staged)?;
                        std::io::copy(&mut entry, &mut file)?;
                        found = true;
                        break;
                    }
                }
                anyhow::ensure!(found, "ffmpeg not found in downloaded archive");
                process::chmod(&staged)?;
                anyhow::ensure!(
                    healthy(&staged, &app.dir),
                    "Downloaded FFmpeg failed its startup probe"
                );
                let _ = fs::remove_file(archive);
            }
            fs::rename(&staged, &path)?;
            Ok(())
        })();
        if let Err(e) = result {
            let _ = fs::remove_file(&staged);
            app.log(&format!("[WARN] Could not refresh {tool}: {e}"));
            // A failed update leaves the previous local binary in place.
            if tool == "ffmpeg" && !healthy(&path, &app.dir) {
                let system = std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
                    .map(|p| p.join(tool))
                    .find(|p| p != &path && p.is_file() && healthy(p, &app.dir));
                if let Some(system) = system {
                    fs::copy(system, &staged)?;
                    process::chmod(&staged)?;
                    fs::rename(&staged, &path)?;
                } else {
                    bail!("No usable FFmpeg available: {e}");
                }
            } else if process::find(&app.dir, tool).is_none() {
                return Err(e);
            }
        }
    }
    Ok(())
}
fn instance_id() -> &'static str {
    static INSTANCE: OnceLock<String> = OnceLock::new();
    INSTANCE
        .get_or_init(|| format!("{}-{}", std::process::id(), now()))
        .as_str()
}

pub fn empty_job() -> Value {
    json!({
        "running":false,"operation":"idle","phase":"idle","outcome":"",
        "instance_id":instance_id(),"restart_expected":false,
        "last_result":null,"last_error":"","started_at":0.0,"finished_at":0.0,
        "progress_percent":0,"progress_message":"","downloaded_path":"",
        "selected_version":"","selected_channel":"","force_reinstall":false,"mode":"manual"
    })
}

pub fn trigger(app: &Arc<App>, app_update: bool, install: bool, payload: &Value) -> Value {
    let settings = app.settings();
    let raw = app.raw_settings();
    let channel = text(
        payload,
        "channel",
        &text(&settings, "app_update_channel", "release"),
    );
    let channel = if channel == "prerelease" {
        "prerelease"
    } else {
        "release"
    }
    .to_owned();
    // Missing means retain the user's selection; an explicit empty string means latest.
    let fallback = if channel == text(&settings, "app_update_channel", "release") {
        text(&raw, "app_update_selected_version", "")
    } else {
        String::new()
    };
    let selected = text(payload, "selected_version", &fallback);
    let force = install && boolean(payload, "force_reinstall", false);
    {
        let lock = if app_update {
            &app.app_job
        } else {
            &app.binary_job
        };
        let mut job = lock.lock().unwrap();
        if boolean(&job, "running", false) {
            return job.clone();
        }
        *job = empty_job();
        job["running"] = json!(true);
        job["operation"] = json!(if app_update {
            if install {
                "install"
            } else {
                "check"
            }
        } else if install {
            "binaries_update"
        } else {
            "binaries_status"
        });
        job["phase"] = json!("checking");
        job["started_at"] = json!(now());
        job["selected_channel"] = json!(channel);
        job["selected_version"] = json!(selected);
        job["force_reinstall"] = json!(force);
        job["progress_message"] = json!("Checking versions...");
    }
    if app_update && (payload.get("selected_version").is_some() || payload.get("channel").is_some())
    {
        if let Err(e) = app.remember_update_selection(&channel, &selected) {
            let mut job = app.app_job.lock().unwrap();
            job["running"] = json!(false);
            job["phase"] = json!("failed");
            job["last_error"] = json!(format!("Save update selection: {e:#}"));
            return job.clone();
        }
    }
    let cloned = Arc::clone(app);
    std::thread::spawn(move || {
        let result = (|| -> Result<Value> {
            if app_update {
                cloned.log(&format!(
                    "[INFO] App update check: current {VERSION}, channel {channel}, selection {}.",
                    if selected.is_empty() {
                        "latest"
                    } else {
                        &selected
                    }
                ));
                cloned.progress(true, "Checking GitHub releases...", 5);
                let info = release(&channel, &selected)?;
                let target = text(&info, "selected_version", "");
                let ordering = compare(&target, VERSION);
                let newer = ordering == Ordering::Greater;
                let reinstall = force && ordering == Ordering::Equal;
                let automatic = !install
                    && boolean(&cloned.settings(), "auto_app_updates", false)
                    && !cloned.development;
                let do_install = (install && (newer || reinstall)) || (automatic && newer);
                let supported = boolean(&info, "asset_supported", false);
                {
                    let mut job = cloned.app_job.lock().unwrap();
                    job["last_result"] = info.clone();
                    job["mode"] = json!(if automatic { "automatic" } else { "manual" });
                    job["outcome"] = json!(if newer {
                        "update_available"
                    } else if ordering == Ordering::Less {
                        "local_newer"
                    } else {
                        "up_to_date"
                    });
                }
                cloned.log(&format!("[INFO] App update check complete: current {VERSION}, selected {target}, newer={newer}, asset_supported={supported}."));
                if do_install && supported {
                    let name = text(&info, "asset_name", "");
                    anyhow::ensure!(
                        Path::new(&name).file_name().and_then(|s| s.to_str())
                            == Some(name.as_str()),
                        "Invalid release asset name"
                    );
                    let dir = cloned.dir.join("updates");
                    fs::create_dir_all(&dir)?;
                    let staged = dir.join(name);
                    {
                        let mut job = cloned.app_job.lock().unwrap();
                        job["operation"] = json!("install");
                        job["phase"] = json!("downloading");
                        job["restart_expected"] = json!(!cloned.development);
                    }
                    cloned.log(&format!(
                        "[INFO] {} app {}: {VERSION} -> {target}.",
                        if automatic { "Automatic" } else { "Manual" },
                        if reinstall { "reinstall" } else { "update" }
                    ));
                    download(&text(&info, "download_url", ""), &staged, |done, total| {
                        if total > 0 {
                            cloned.progress(
                                true,
                                "Downloading app update...",
                                12 + (done * 80 / total) as i64,
                            );
                        }
                    })?;
                    process::chmod(&staged)?;
                    cloned.app_job.lock().unwrap()["downloaded_path"] = json!(staged);
                    if cloned.development {
                        let mut job = cloned.app_job.lock().unwrap();
                        job["phase"] = json!("downloaded");
                        job["outcome"] = json!("downloaded");
                        job["progress_message"] =
                            json!("Downloaded update; install manually in development mode.");
                    } else {
                        cloned.progress(true, "Installing update and restarting...", 95);
                        cloned.app_job.lock().unwrap()["phase"] = json!("installing");
                        cloned.install_update(&staged)?;
                        let mut job = cloned.app_job.lock().unwrap();
                        job["phase"] = json!("installed");
                        job["outcome"] = json!("installed");
                        job["progress_message"] = json!("Update installed. Restarting...");
                    }
                } else {
                    let message = if ordering == Ordering::Less {
                        "Installed version is newer than the selected release; no update needed."
                    } else if !newer {
                        "Already on the selected version."
                    } else if !supported {
                        "A newer release exists, but it has no self-installable asset for this platform."
                    } else if cloned.development
                        && boolean(&cloned.settings(), "auto_app_updates", false)
                    {
                        "Update available; automatic installation is disabled in development mode."
                    } else {
                        "Update available. Use Install Update to install the selected version."
                    };
                    cloned.progress(true, message, 100);
                    cloned.log(&format!("[INFO] {message}"));
                }
                Ok(info)
            } else {
                if install {
                    let _guard = cloned.provision_lock.lock().unwrap();
                    anyhow::ensure!(
                        !cloned.is_streaming(),
                        "Stop the stream before updating tool binaries"
                    );
                    ensure_binaries(&cloned, true)?;
                }
                Ok(binaries_status(&cloned.dir))
            }
        })();
        let error = result.as_ref().err().map(|e| format!("{e:#}"));
        {
            let lock = if app_update {
                &cloned.app_job
            } else {
                &cloned.binary_job
            };
            let mut job = lock.lock().unwrap();
            job["running"] = json!(false);
            job["finished_at"] = json!(now());
            match result {
                Ok(info) => {
                    job["last_result"] = info;
                    job["progress_percent"] = json!(100);
                    if !["installed", "downloaded"].contains(&text(&job, "phase", "").as_str()) {
                        job["phase"] = json!("complete");
                    }
                    if !app_update {
                        job["progress_message"] = json!(if install {
                            "Binary update complete."
                        } else {
                            "Binary check complete."
                        });
                    }
                }
                Err(e) => {
                    job["phase"] = json!("failed");
                    job["last_error"] = json!(format!("{e:#}"));
                    job["progress_message"] = json!("Update operation failed.");
                    job["restart_expected"] = json!(false);
                }
            }
        }
        if let Some(error) = error {
            cloned.log(&format!("[ERROR] Update operation failed: {error}"));
        }
    });
    let lock = if app_update {
        &app.app_job
    } else {
        &app.binary_job
    };
    let snapshot = lock.lock().unwrap().clone();
    snapshot
}

pub fn initial_status(app: &Arc<App>, is_app: bool) -> Value {
    let lock = if is_app {
        &app.app_job
    } else {
        &app.binary_job
    };
    let job = lock.lock().unwrap().clone();
    // Reading app status never starts an installation or a new check.
    if !is_app
        && !boolean(&job, "running", false)
        && job["last_result"].is_null()
        && text(&job, "last_error", "").is_empty()
    {
        return trigger(app, false, false, &json!({}));
    }
    job
}

pub fn startup(app: Arc<App>) {
    let settings = app.settings();
    if !boolean(&settings, "check_updates_startup", true) {
        app.log("[INFO] Startup update check is disabled.");
        return;
    }
    // Checking is independent of automatic installation; trigger applies that policy.
    trigger(&app, true, false, &json!({}));
}
