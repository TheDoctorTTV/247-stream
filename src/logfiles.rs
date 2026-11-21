//! Bounded plaintext active logs and asynchronous, low-compression gzip archives.
use anyhow::{Context, Result};
use chrono::Local;
use flate2::{write::GzEncoder, Compression};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{
        mpsc::{self, SyncSender},
        OnceLock,
    },
    thread,
};
const KEEP_ARCHIVES: usize = 32;
fn archive_name(name: &str, kind: &str) -> bool {
    name.starts_with(&format!("{kind}-")) && (name.ends_with(".log") || name.ends_with(".log.gz"))
}
fn prune(dir: &Path, kind: &str) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    let mut files: Vec<_> = entries
        .flatten()
        .filter(|e| {
            e.file_type().is_ok_and(|t| t.is_file())
                && archive_name(&e.file_name().to_string_lossy(), kind)
        })
        .map(|e| e.path())
        .collect();
    files.sort();
    let remove = files.len().saturating_sub(KEEP_ARCHIVES);
    for path in files.into_iter().take(remove) {
        let _ = fs::remove_file(path);
    }
}
fn compress(path: &Path) -> Result<()> {
    if !path.is_file() {
        return Ok(());
    }
    let target = PathBuf::from(format!("{}.gz", path.display()));
    let temp = PathBuf::from(format!("{}.part", target.display()));
    let result = (|| -> Result<()> {
        let mut source = File::open(path)?;
        let _ = fs::remove_file(&temp);
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temp)?;
        let mut gzip = GzEncoder::new(file, Compression::fast());
        io::copy(&mut source, &mut gzip)?;
        gzip.finish()?.sync_all()?;
        fs::rename(&temp, &target)?;
        fs::remove_file(path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}
fn compressor() -> &'static SyncSender<PathBuf> {
    static SENDER: OnceLock<SyncSender<PathBuf>> = OnceLock::new();
    SENDER.get_or_init(|| {
        let (tx, rx) = mpsc::sync_channel::<PathBuf>(64);
        thread::spawn(move || {
            while let Ok(path) = rx.recv() {
                if compress(&path).is_err() {
                    // Fixed text only: error objects may contain private paths.
                    let _ = writeln!(
                        io::stderr().lock(),
                        "[WARN] Log compression failed; the uncompressed archive was retained."
                    );
                }
                if let Some(dir) = path.parent() {
                    for kind in ["app", "ffmpeg"] {
                        prune(dir, kind);
                    }
                }
            }
        });
        tx
    })
}
fn queue(path: PathBuf) {
    let _ = compressor().try_send(path);
}

pub struct LogFile {
    dir: PathBuf,
    kind: &'static str,
    file: Option<File>,
    bytes: u64,
    limit: u64,
}
impl LogFile {
    pub fn open(base: &Path, kind: &'static str, limit: u64, rotate: bool) -> Result<Self> {
        let dir = base.join("logs");
        if !dir.exists() {
            fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(&dir)?;
        }
        let mut log = Self {
            dir,
            kind,
            file: None,
            bytes: 0,
            limit,
        };
        // Requeue archives left plain if shutdown interrupted compression or the queue filled.
        for entry in fs::read_dir(&log.dir)?.flatten() {
            if entry.file_type().is_ok_and(|t| t.is_file()) {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if archive_name(&name, kind) && name.ends_with(".log") {
                    queue(entry.path());
                }
            }
        }
        if rotate || log.path().metadata().is_ok_and(|m| m.len() >= limit) {
            log.rotate()?;
        } else {
            log.reopen()?;
        }
        Ok(log)
    }
    fn path(&self) -> PathBuf {
        self.dir.join(format!("{}.log", self.kind))
    }
    fn reopen(&mut self) -> Result<()> {
        let file = OpenOptions::new()
            .append(true)
            .create(true)
            .mode(0o600)
            .open(self.path())
            .context("Open active log")?;
        self.bytes = file.metadata()?.len();
        self.file = Some(file);
        Ok(())
    }
    pub fn rotate(&mut self) -> Result<()> {
        if let Some(mut f) = self.file.take() {
            f.flush()?;
        }
        let path = self.path();
        if path.metadata().is_ok_and(|m| m.len() > 0) {
            let name = format!(
                "{}-{}-{}.log",
                self.kind,
                Local::now().format("%Y%m%d-%H%M%S-%9f"),
                std::process::id()
            );
            let archived = self.dir.join(name);
            fs::rename(&path, &archived)?;
            queue(archived);
            prune(&self.dir, self.kind);
        }
        self.reopen()
    }
    pub fn line(&mut self, line: &str) -> Result<()> {
        let length = line.len() as u64 + 1;
        if self.bytes > 0 && self.bytes + length > self.limit {
            self.rotate()?;
        }
        let file = self.file.as_mut().context("Log file is closed")?;
        writeln!(file, "{line}")?;
        self.bytes += length;
        Ok(())
    }
}
