//! Subprocess ownership, cancellation, and pipe readers shared by the worker and updater.
use anyhow::{Context, Result};
#[cfg(unix)]
use std::os::unix::{fs::PermissionsExt, process::CommandExt};
use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

pub fn find(dir: &Path, name: &str) -> Option<PathBuf> {
    let local = dir.join(name);
    if executable(&local) {
        return Some(local);
    }
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .map(|p| p.join(name))
        .find(|p| executable(p))
}
fn executable(path: &Path) -> bool {
    path.is_file() && {
        #[cfg(unix)]
        {
            path.metadata()
                .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
        }
        #[cfg(not(unix))]
        {
            true
        }
    }
}
pub fn chmod(path: &Path) -> Result<()> {
    #[cfg(unix)]
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755))?;
    Ok(())
}
pub fn command(binary: &Path, args: &[String], dir: &Path) -> Command {
    let mut cmd = Command::new(binary);
    cmd.args(args)
        .current_dir(dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    cmd.process_group(0);
    cmd
}
pub struct OwnedChild(pub Child);
impl OwnedChild {
    pub fn stop(&mut self) {
        if self.0.try_wait().ok().flatten().is_some() {
            return;
        }
        #[cfg(unix)]
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGTERM);
        }
        #[cfg(not(unix))]
        let _ = self.0.kill();
        let until = Instant::now() + Duration::from_secs(2);
        while Instant::now() < until {
            if self.0.try_wait().ok().flatten().is_some() {
                return;
            }
            thread::sleep(Duration::from_millis(25));
        }
        #[cfg(unix)]
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGKILL);
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
impl Drop for OwnedChild {
    fn drop(&mut self) {
        self.stop();
    }
}
#[derive(Default)]
pub struct Control {
    pub stop: AtomicBool,
    pub skip: AtomicBool,
}
impl Control {
    pub fn stopped(&self) -> bool {
        self.stop.load(Ordering::Acquire)
    }
    pub fn skipped(&self) -> bool {
        self.skip.load(Ordering::Acquire)
    }
    pub fn pause(&self, duration: Duration) {
        let end = Instant::now() + duration;
        while Instant::now() < end && !self.stopped() {
            thread::sleep(Duration::from_millis(50));
        }
    }
}
pub fn capture(
    binary: &Path,
    args: &[String],
    dir: &Path,
    timeout: Duration,
    control: Option<&Control>,
) -> Result<(ExitStatus, String, String)> {
    let mut cmd = command(binary, args, dir);
    cmd.stdout(Stdio::piped());
    let mut child = OwnedChild(
        cmd.spawn()
            .with_context(|| format!("Launch {}", binary.display()))?,
    );
    let stdout = child.0.stdout.take().context("Missing stdout")?;
    let stderr = child.0.stderr.take().context("Missing stderr")?;
    let reader = |mut stream: Box<dyn Read + Send>| {
        thread::spawn(move || {
            let mut out = String::new();
            let _ = stream.read_to_string(&mut out);
            out
        })
    };
    let out = reader(Box::new(stdout));
    let err = reader(Box::new(stderr));
    let started = Instant::now();
    let mut failure = None;
    let status = loop {
        if let Some(status) = child.0.try_wait()? {
            break status;
        }
        if control.is_some_and(|c| c.stopped()) || started.elapsed() > timeout {
            failure = Some(if started.elapsed() > timeout {
                "Subprocess timed out"
            } else {
                "Stopped"
            });
            child.stop();
            break child.0.wait()?;
        }
        thread::sleep(Duration::from_millis(50));
    };
    let stdout = out.join().unwrap_or_default();
    let stderr = err.join().unwrap_or_default();
    if let Some(reason) = failure {
        anyhow::bail!("{reason}");
    }
    Ok((status, stdout, stderr))
}
pub fn args(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| s.to_string()).collect()
}

/// FFmpeg writes progress using carriage returns as well as newlines.
pub fn lines<R: Read + Send + 'static>(
    mut input: R,
    callback: Arc<dyn Fn(String) + Send + Sync>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let mut buf = [0; 8192];
        let mut line = Vec::new();
        loop {
            let n = match input.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => n,
            };
            for b in &buf[..n] {
                if *b == b'\n' || *b == b'\r' {
                    if !line.is_empty() {
                        callback(String::from_utf8_lossy(&line).into_owned());
                        line.clear();
                    }
                } else if line.len() < 65536 {
                    line.push(*b);
                }
            }
        }
        if !line.is_empty() {
            callback(String::from_utf8_lossy(&line).into_owned());
        }
    })
}
pub fn write_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let tmp = path.with_extension("tmp");
    let mut f = std::fs::File::create(&tmp)?;
    f.write_all(bytes)?;
    f.sync_all()?;
    std::fs::rename(tmp, path)?;
    Ok(())
}
