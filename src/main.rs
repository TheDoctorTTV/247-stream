//! Stream247's native Rust entry point. FFmpeg and yt-dlp remain external tools.
mod config;
mod process;
mod runtime;
mod state;
mod updates;
mod web;
mod worker;

use anyhow::{Context, Result};
use std::{
    path::PathBuf,
    sync::{atomic::Ordering, Arc},
};
pub const APP_NAME: &str = "Stream247";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const REPO: &str = "TheDoctorTTV/247-stream";
fn main() -> Result<()> {
    let executable = std::env::current_exe()?;
    let development = executable.components().any(|c| c.as_os_str() == "target");
    let mut dir = if development {
        std::env::current_dir()?
    } else {
        executable
            .parent()
            .context("Executable directory missing")?
            .to_path_buf()
    };
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--version" | "-V" => {
                println!("{APP_NAME} {VERSION}");
                return Ok(());
            }
            "--help" | "-h" => {
                println!("Stream247 headless runtime\n\nUsage: stream247-server [--data-dir PATH] [--version]\n\nReads config.json and writes logs in the data directory.\nPackaged binaries default to their own directory; cargo run uses the current directory.");
                return Ok(());
            }
            "--data-dir" => {
                dir = PathBuf::from(args.next().context("--data-dir requires a path")?);
            }
            _ => anyhow::bail!("Unknown argument: {arg}"),
        }
    }
    if dir.is_relative() {
        dir = std::env::current_dir()?.join(dir);
    }
    let app = runtime::App::new(dir, development)?;
    let shutdown = Arc::clone(&app);
    ctrlc::set_handler(move || {
        shutdown.shutdown.store(true, Ordering::Release);
        shutdown.stop();
    })?;
    let result = web::serve(Arc::clone(&app));
    if result.is_err() {
        app.stop();
        app.join_worker();
    }
    if app.restart.load(Ordering::Acquire) {
        use std::os::unix::process::CommandExt;
        let mut cmd = std::process::Command::new(&executable);
        cmd.arg("--data-dir").arg(&app.dir);
        return Err(cmd.exec().into());
    }
    result
}
