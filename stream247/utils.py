"""Misc runtime utilities and process/network helpers."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO, Tuple

from .config import _app_dir
from .constants import APP_NAME, APP_VERSION


def resource_path(name: str) -> str:
    """Resolve a resource path for frozen executables or source runs."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
    p = Path(base) / name
    if p.exists():
        return str(p)
    return str(Path.cwd() / name)


def find_drawtext_fontfile() -> Optional[str]:
    """Return a font file path suitable for ffmpeg drawtext on Linux."""
    candidates: List[Path] = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]
    for p in candidates:
        try:
            if p.exists():
                return p.as_posix()
        except Exception:
            continue
    return None


def find_binary(candidates: List[str]) -> Optional[str]:
    """Search PATH and local resources for the first existing executable."""
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    for c in candidates:
        rp = resource_path(c)
        if Path(rp).exists():
            return rp
    return None


def find_ffmpeg() -> Optional[str]:
    return find_binary(["ffmpeg"])


def find_ytdlp() -> Optional[str]:
    candidates = ["yt-dlp"]
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    for c in candidates:
        rp = resource_path(c)
        if Path(rp).exists():
            return rp
    return None


def _download_url(
    url: str,
    dest_path: Path,
    user_agent: Optional[str] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    max_mbps: Optional[int] = None,
    parallel_chunks: int = 8,
) -> None:
    """Download a URL to dest_path atomically."""

    class _RateLimiter:
        def __init__(self, bytes_per_sec: Optional[float]):
            self.rate = bytes_per_sec or 0.0
            self.tokens = self.rate
            self.last = time.monotonic()
            self.lock = threading.Lock()

        def acquire(self, amount: int) -> None:
            if self.rate <= 0:
                return
            need = float(amount)
            while True:
                with self.lock:
                    now = time.monotonic()
                    elapsed = now - self.last
                    if elapsed > 0:
                        self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
                        self.last = now
                    if self.tokens >= need:
                        self.tokens -= need
                        return
                    wait_s = (need - self.tokens) / self.rate
                time.sleep(max(0.001, wait_s))

    def _emit_progress(done: int, total: int) -> None:
        if progress_cb:
            try:
                progress_cb(done, total)
            except Exception:
                pass

    def _download_single(headers: dict) -> None:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = resp.length or 0
            downloaded = 0
            with tempfile.NamedTemporaryFile(delete=False, dir=str(dest_path.parent)) as tf:
                tmp_name = tf.name
                while True:
                    limiter.acquire(chunk_size)
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    tf.write(chunk)
                    downloaded += len(chunk)
                    _emit_progress(downloaded, total)
        Path(tmp_name).replace(dest_path)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    chunk_size = 1024 * 256
    capped_mbps = None
    if max_mbps is not None:
        try:
            capped_mbps = max(1, min(50, int(max_mbps)))
        except Exception:
            capped_mbps = None
    max_bytes_per_sec = (capped_mbps * 1_000_000 / 8.0) if capped_mbps else None
    limiter = _RateLimiter(max_bytes_per_sec)

    total_size = 0
    accepts_ranges = False
    try:
        head_req = urllib.request.Request(url, headers=headers, method="HEAD")
        with urllib.request.urlopen(head_req, timeout=30) as head_resp:
            content_len = head_resp.headers.get("Content-Length")
            total_size = int(content_len) if content_len and content_len.isdigit() else 0
            accepts_ranges = ("bytes" in head_resp.headers.get("Accept-Ranges", "").lower())
    except Exception:
        total_size = 0
        accepts_ranges = False

    if not accepts_ranges or total_size <= chunk_size * 4 or parallel_chunks <= 1:
        _download_single(headers)
        return

    with tempfile.NamedTemporaryFile(delete=False, dir=str(dest_path.parent)) as tf:
        tmp_name = tf.name
    tmp_path = Path(tmp_name)
    downloaded_total = 0
    dl_lock = threading.Lock()
    part_size = max(1, total_size // parallel_chunks)
    ranges: List[Tuple[int, int]] = []
    start = 0
    while start < total_size:
        end = min(total_size - 1, start + part_size - 1)
        ranges.append((start, end))
        start = end + 1

    try:
        with open(tmp_path, "wb") as f:
            f.truncate(total_size)

        def _download_range(r_start: int, r_end: int) -> None:
            nonlocal downloaded_total
            range_headers = dict(headers)
            range_headers["Range"] = f"bytes={r_start}-{r_end}"
            req = urllib.request.Request(url, headers=range_headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = int(getattr(resp, "status", 200))
                if status not in (206,):
                    raise RuntimeError("Server did not honor range request")
                pos = r_start
                with open(tmp_path, "r+b", buffering=0) as out:
                    while pos <= r_end:
                        to_read = min(chunk_size, r_end - pos + 1)
                        limiter.acquire(to_read)
                        chunk = resp.read(to_read)
                        if not chunk:
                            break
                        out.seek(pos)
                        out.write(chunk)
                        read_len = len(chunk)
                        pos += read_len
                        with dl_lock:
                            downloaded_total += read_len
                            _emit_progress(downloaded_total, total_size)
                if pos <= r_end:
                    raise RuntimeError("Incomplete range download")

        with ThreadPoolExecutor(max_workers=max(2, min(12, parallel_chunks))) as ex:
            futures = [ex.submit(_download_range, s, e) for s, e in ranges]
            for fut in as_completed(futures):
                fut.result()

        tmp_path.replace(dest_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        _download_single(headers)


def github_latest_asset_url(repo: str, prefer_substrings: List[str], must_match_regex: str = ".*", user_agent: Optional[str] = None) -> Optional[str]:
    """Return browser_download_url of an asset from latest GitHub release."""
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json"}
        if user_agent:
            headers["User-Agent"] = user_agent
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assets = data.get("assets", [])
        if not assets:
            return None
        regex = re.compile(must_match_regex)
        filtered = [a for a in assets if regex.search(a.get("name", ""))]
        if not filtered:
            return None

        def score(name: str) -> Tuple[int, int]:
            pri = len(prefer_substrings)
            for i, sub in enumerate(prefer_substrings):
                if sub.lower() in name.lower():
                    pri = i
                    break
            return (pri, len(name))

        best = min(filtered, key=lambda a: score(a.get("name", "")))
        return best.get("browser_download_url")
    except Exception:
        return None


def run_hidden(cmd: List[str], check=False, capture=True, text=True, timeout=None) -> subprocess.CompletedProcess:
    kwargs = {}
    if capture:
        kwargs.update(dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text))
    return subprocess.run(cmd, check=check, timeout=timeout, **kwargs)


def safe_write_text(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8", errors="ignore")
    except Exception:
        pass


def open_rotating_latest_log() -> Tuple[Optional[TextIO], Optional[Path]]:
    base = _app_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    log_path = base / "latest.log"
    try:
        if log_path.exists():
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            log_path.rename(log_path.with_name(f"{log_path.stem}-{ts}{log_path.suffix}"))
        return log_path.open("w", encoding="utf-8"), log_path
    except Exception:
        try:
            cwd_path = Path.cwd() / "latest.log"
            if cwd_path.exists():
                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                cwd_path.rename(cwd_path.with_name(f"{cwd_path.stem}-{ts}{cwd_path.suffix}"))
            return cwd_path.open("w", encoding="utf-8"), cwd_path
        except Exception:
            return None, None


def restore_terminal_state() -> None:
    try:
        if not sys.stdin.isatty():
            return
    except Exception:
        return
    try:
        subprocess.run(["stty", "sane"], check=False)
    except Exception:
        pass


def detect_input_type(url: str) -> str:
    url_lower = url.lower().strip()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        if "list=" in url_lower:
            return "youtube_playlist"
        if "watch?v=" in url_lower or "youtu.be/" in url_lower:
            return "youtube_video"
    if url_lower.endswith(".m3u8"):
        return "direct_hls"
    if "twitch.tv" in url_lower:
        return "twitch_stream"
    return "unknown"


def ffmpeg_lists_encoder(ffmpeg_path: Optional[str], codec: str) -> bool:
    if not ffmpeg_path:
        return False
    try:
        cp = run_hidden([ffmpeg_path, "-hide_banner", "-loglevel", "error", "-encoders"], timeout=8)
        text = f"{cp.stdout or ''}\n{cp.stderr or ''}"
        return bool(re.search(rf"^\s*[A-Z\.]+\s+{re.escape(codec)}\b", text, re.MULTILINE))
    except Exception:
        return False


def ffprobe_encoder(ffmpeg_path: Optional[str], codec: str) -> bool:
    if not ffmpeg_path or not ffmpeg_lists_encoder(ffmpeg_path, codec):
        return False
    try:
        null = "/dev/null"
        base = [
            ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=black:s=320x180:rate=30",
            "-t", "0.2",
        ]
        probes: List[List[str]] = []
        if codec == "h264_vaapi":
            device = "/dev/dri/renderD128"
            if not Path(device).exists():
                return False
            probes.append(base + ["-vaapi_device", device, "-vf", "format=nv12,hwupload", "-c:v", codec, "-f", "null", null])
        elif codec == "h264_qsv":
            probes.append(base + ["-vf", "format=nv12", "-c:v", codec, "-f", "null", null])
            if Path("/dev/dri/renderD128").exists():
                probes.append(base + ["-init_hw_device", "qsv=hw:/dev/dri/renderD128", "-vf", "format=nv12", "-c:v", codec, "-f", "null", null])
        else:
            probes.append(base + ["-vf", "format=yuv420p", "-c:v", codec, "-f", "null", null])

        for cmd in probes:
            if run_hidden(cmd, timeout=10).returncode == 0:
                return True
        return False
    except Exception:
        return False


def fmt_yt_date(upload_date: Optional[str], timestamp: Optional[int], release_ts: Optional[int]) -> Optional[str]:
    def _fmt_day(d: datetime.datetime) -> str:
        return d.strftime("%b %d, %Y").replace(" 0", " ")

    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        try:
            year = int(upload_date[0:4])
            month = int(upload_date[4:6])
            day = int(upload_date[6:8])
            date_obj = datetime.date(year, month, day)
            date_obj = date_obj - datetime.timedelta(days=1)
            dt_for_format = datetime.datetime.combine(date_obj, datetime.time.min)
            return _fmt_day(dt_for_format)
        except Exception:
            pass
    ts = release_ts or timestamp
    if ts:
        try:
            dt = datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc)
            dt = dt - datetime.timedelta(days=1)
            dt = dt.replace(tzinfo=None)
            return _fmt_day(dt)
        except Exception:
            pass
    return None
