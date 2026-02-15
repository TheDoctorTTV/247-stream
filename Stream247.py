import os, sys, time, json, random, shlex, shutil, subprocess, threading, datetime
import tempfile, platform, tarfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple
from collections import deque
from pathlib import Path
try:
    from PySide6 import QtCore  # type: ignore
except Exception:
    class _SignalInstance:
        def __init__(self):
            self._callbacks: List[Callable[..., None]] = []
            self._lock = threading.Lock()

        def connect(self, cb, *args, **kwargs):
            with self._lock:
                self._callbacks.append(cb)

        def emit(self, *args, **kwargs):
            with self._lock:
                callbacks = list(self._callbacks)
            for cb in callbacks:
                try:
                    cb(*args, **kwargs)
                except Exception:
                    pass

    class _SignalDescriptor:
        def __set_name__(self, owner, name):
            self._name = f"__signal_{name}"

        def __get__(self, instance, owner):
            if instance is None:
                return self
            sig = instance.__dict__.get(self._name)
            if sig is None:
                sig = _SignalInstance()
                instance.__dict__[self._name] = sig
            return sig

    class _QObjectShim:
        def __init__(self, *args, **kwargs):
            pass

    class _QtCoreShim:
        QObject = _QObjectShim

        @staticmethod
        def Signal(*args, **kwargs):
            return _SignalDescriptor()

        @staticmethod
        def Slot(*args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator

        class Qt:
            class ConnectionType:
                DirectConnection = 0

    QtCore = _QtCoreShim()  # type: ignore
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import re

# General application metadata and platform helpers
APP_NAME = "Stream247"  # Name shown in logs and dashboard
APP_VERSION = "2.0-pre-release-6"  # Current version
GITHUB_REPO = "TheDoctorTTV/247-stream"  # GitHub repository for updates
APP_UPDATE_MIN_VERSION = "2.0-pre-release-2"  # Oldest version eligible for in-app updater

# ---------- config.json helpers ----------
def _app_dir() -> Path:
    """Return the directory where the app is running from."""
    # If running from AppImage, use a writable config path.
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / APP_NAME
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).parent  # packaged exe folder
    return Path.cwd()                       # running from source


def _running_under_systemd() -> bool:
    """Best-effort detection for Linux systemd-managed runtime."""
    if platform.system().lower() != "linux":
        return False
    forced = str(os.environ.get("STREAM247_SYSTEMD", "")).strip().lower()
    if forced in ("1", "true", "yes", "on"):
        return True
    for key in ("INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "SYSTEMD_EXEC_PID"):
        if os.environ.get(key):
            return True
    return False


CONFIG_PATH = _app_dir() / "config.json"

def load_config_json() -> dict:
    """Load configuration settings from CONFIG_PATH if it exists."""
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def save_config_json(data: dict) -> None:
    """Persist the configuration dictionary to CONFIG_PATH."""
    try:
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def read_web_server_settings() -> Tuple[bool, str, int, bool]:
    """Return web dashboard settings from config.json."""
    cfg = load_config_json()
    enabled = bool(cfg.get("web_server_enabled", False))
    host = str(cfg.get("web_server_host", "0.0.0.0")).strip() or "0.0.0.0"
    try:
        port = int(cfg.get("web_server_port", 7788))
    except Exception:
        port = 7788
    if port <= 0 or port > 65535:
        port = 7788
    autostart = bool(cfg.get("web_server_autostart", True))
    return enabled, host, port, autostart


WEB_ALLOWED_RESOLUTIONS = ("480p", "720p", "1080p", "1440p", "2160p")
WEB_ALLOWED_FRAMERATES = (30, 60)
WEB_ALLOWED_BUFFER_MODES = ("Low", "Medium", "High", "Ultra")
WEB_ALLOWED_ENCODERS = (
    "auto", "libx264", "h264_nvenc", "h264_qsv", "h264_amf", "h264_vaapi"
)
WEB_ALLOWED_BROWSERS = (
    "auto", "firefox", "chrome", "edge", "chromium", "brave", "vivaldi", "opera"
)
WEB_ALLOWED_UPDATE_CHANNELS = ("release", "prerelease")
WEB_UPDATE_RELEASES_LIMIT = 100
BITRATE_MIN_KBPS = 1500
BITRATE_MAX_KBPS = 25000
BITRATE_STEP_KBPS = 500
BITRATE_DEFAULT_KBPS = 2500
BUFFER_MODE_BUFSIZE_MULTIPLIER = {
    "Low": 2.0,
    "Medium": 3.0,
    "High": 4.0,
    "Ultra": 5.0,
}


def _to_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
    return bool(default)


def _parse_bitrate_kbps(value: object, default: int = BITRATE_DEFAULT_KBPS) -> int:
    """Parse bitrate text (e.g. '6000k', '6000 kbps', '6000') into clamped kbps."""
    try:
        text = str(value or "").strip().lower()
        m = re.search(r"(\d+)", text)
        if not m:
            return int(default)
        raw = int(m.group(1))
    except Exception:
        return int(default)
    clamped = max(BITRATE_MIN_KBPS, min(BITRATE_MAX_KBPS, raw))
    offset = clamped - BITRATE_MIN_KBPS
    rounded = BITRATE_MIN_KBPS + int(round(offset / BITRATE_STEP_KBPS) * BITRATE_STEP_KBPS)
    return max(BITRATE_MIN_KBPS, min(BITRATE_MAX_KBPS, rounded))


def _kbps_to_text(kbps: int) -> str:
    return f"{int(kbps)}k"


def _normalize_sources(value: object) -> List[str]:
    """Normalize sources into an ordered, de-duplicated URL list."""
    raw_items: List[object]
    if isinstance(value, str):
        raw_items = value.splitlines()
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = []
    out: List[str] = []
    seen = set()
    for item in raw_items:
        src = ""
        if isinstance(item, dict):
            for key in ("url", "source", "playlist_url"):
                raw = str(item.get(key, "")).strip()
                if raw:
                    src = raw
                    break
        else:
            src = str(item or "").strip()
        if not src or src in seen:
            continue
        seen.add(src)
        out.append(src)
    return out


def _normalize_source_names(value: object, valid_sources: List[str]) -> Dict[str, str]:
    """Normalize source display-name mapping keyed by URL."""
    raw_map: Dict[str, str] = {}
    if isinstance(value, dict):
        for raw_url, raw_name in value.items():
            url = str(raw_url or "").strip()
            name = str(raw_name or "").strip()
            if url and name:
                raw_map[url] = name
    elif isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "") or item.get("source", "") or item.get("playlist_url", "")).strip()
            name = str(item.get("name", "")).strip()
            if url and name:
                raw_map[url] = name

    out: Dict[str, str] = {}
    for src in valid_sources:
        name = raw_map.get(src, "").strip()
        if name:
            out[src] = name
    return out


def _resolved_source_names(cfg: Dict[str, object], sources: List[str]) -> Dict[str, str]:
    """Resolve normalized source display names for known sources."""
    names = _normalize_source_names(cfg.get("source_names", {}), sources)
    raw_sources = cfg.get("sources", [])
    if isinstance(raw_sources, (list, tuple)):
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            src = str(item.get("url", "") or item.get("source", "") or item.get("playlist_url", "")).strip()
            if (not src) or (src not in sources) or (src in names):
                continue
            name = str(item.get("name", "")).strip()
            if name:
                names[src] = name
    return names


def _resolved_sources_and_playlist(cfg: Dict[str, object]) -> Tuple[List[str], str]:
    """Resolve a normalized source list and selected playlist URL."""
    selected = str(cfg.get("playlist_url", "")).strip()
    sources = _normalize_sources(cfg.get("sources", []))
    # Backward compatibility for older single-source configs.
    if selected and not sources:
        sources = [selected]
    if len(sources) == 1:
        selected = sources[0]
    elif selected and selected in sources:
        pass
    elif sources:
        selected = sources[0]
    return sources, selected


def web_settings_payload_from_config(data: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Return normalized stream settings for the web UI/API."""
    cfg = data or {}
    sources, playlist_url = _resolved_sources_and_playlist(cfg)
    source_names = _resolved_source_names(cfg, sources)
    resolution = str(cfg.get("resolution", "720p"))
    if resolution not in WEB_ALLOWED_RESOLUTIONS:
        resolution = "720p"
    try:
        framerate = int(cfg.get("framerate", 30))
    except Exception:
        framerate = 30
    if framerate not in WEB_ALLOWED_FRAMERATES:
        framerate = 30
    buffer_mode = str(cfg.get("buffer_mode", "Medium"))
    if buffer_mode not in WEB_ALLOWED_BUFFER_MODES:
        buffer_mode = "Medium"
    encoder = str(cfg.get("encoder_preference", "auto")).strip().lower()
    if encoder not in WEB_ALLOWED_ENCODERS:
        encoder = "auto"
    browser = str(cfg.get("yt_auth_browser", "auto")).strip().lower()
    if browser not in WEB_ALLOWED_BROWSERS:
        browser = "auto"
    try:
        cap = int(cfg.get("update_download_cap_mbps", 25))
    except Exception:
        cap = 25
    cap = max(1, min(25, cap))
    update_channel = str(cfg.get("app_update_channel", "release")).strip().lower()
    if update_channel not in WEB_ALLOWED_UPDATE_CHANNELS:
        update_channel = "release"
    bitrate_kbps = _parse_bitrate_kbps(cfg.get("video_bitrate", f"{BITRATE_DEFAULT_KBPS}k"))
    return {
        "playlist_url": playlist_url,
        "sources": sources,
        "source_names": source_names,
        "rtmp_base": str(cfg.get("rtmp_base", "rtmp://a.rtmp.youtube.com/live2")).strip(),
        "stream_key": str(cfg.get("stream_key", "")).strip(),
        "resolution": resolution,
        "framerate": framerate,
        "video_bitrate": _kbps_to_text(bitrate_kbps),
        "buffer_mode": buffer_mode,
        "encoder_preference": encoder,
        "overlay_titles": _to_bool(cfg.get("overlay_titles", True), True),
        "shuffle": _to_bool(cfg.get("shuffle", False), False),
        "log_to_file": _to_bool(cfg.get("log_to_file", False), False),
        "ffmpeg_log_to_file": _to_bool(cfg.get("ffmpeg_log_to_file", False), False),
        "remember": _to_bool(cfg.get("remember", True), True),
        "check_updates_startup": _to_bool(cfg.get("check_updates_startup", True), True),
        "auto_app_updates": _to_bool(cfg.get("auto_app_updates", False), False),
        "app_update_channel": update_channel,
        "yt_auth_enabled": _to_bool(cfg.get("yt_auth_enabled", False), False),
        "yt_auth_browser": browser,
        "yt_auth_profile": str(cfg.get("yt_auth_profile", "")).strip(),
        "update_download_cap_mbps": cap,
    }


def apply_web_settings_payload(base: Dict[str, object], payload: Dict[str, object]) -> Dict[str, object]:
    """Merge a web settings payload into config data with validation."""
    out = dict(base)
    normalized = web_settings_payload_from_config(payload if isinstance(payload, dict) else {})
    if not isinstance(payload, dict):
        return out
    for key, value in normalized.items():
        if key in payload:
            out[key] = value
    if ("sources" in payload) and ("playlist_url" not in payload):
        out["playlist_url"] = normalized.get("playlist_url", "")
    if ("buffer_mode" in payload) or ("video_bitrate" in payload):
        # bufsize is now derived from stream-buffer mode and bitrate.
        out.pop("bufsize", None)
    return out

# ---------- misc utilities ----------
# (Removed: default browser detection helpers)
def resource_path(name: str) -> str:
    """Resolve a resource path for frozen executables or source runs."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
    p = Path(base) / name
    if p.exists():
        return str(p)
    return str(Path.cwd() / name)

def find_drawtext_fontfile() -> Optional[str]:
    """Return a font file path suitable for ffmpeg drawtext on Linux."""
    candidates: List[Path] = []
    # Linux common fonts
    candidates += [
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
    """Locate an ffmpeg binary in PATH or alongside the executable."""
    return find_binary(["ffmpeg"])

def find_ytdlp() -> Optional[str]:
    """Locate a yt-dlp binary in PATH or alongside the executable."""
    # First try the Python-installed version (usually more up-to-date)
    candidates = ["yt-dlp"]
    
    # Check PATH first
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    
    # Then check local resources
    for c in ["yt-dlp"]:
        rp = resource_path(c)
        if Path(rp).exists():
            return rp
    
    return None


class RuntimeStateStore:
    """Thread-safe runtime state used by the runtime and web dashboard."""

    CONSOLE_LOG_WINDOW_SECONDS = 30 * 60

    def __init__(self, log_limit: int = 500):
        self._lock = threading.Lock()
        self._logs: deque[Tuple[float, str]] = deque()
        self._logs_other: deque[Tuple[float, str]] = deque()
        self._logs_ffmpeg: deque[Tuple[float, str]] = deque()
        self._status = "Idle"
        self._streaming = False
        self._updated_at = time.time()
        self._meta: Dict[str, object] = {}

    @staticmethod
    def _purge_before(bucket: deque[Tuple[float, str]], cutoff_ts: float) -> None:
        while bucket and bucket[0][0] < cutoff_ts:
            bucket.popleft()

    def _purge_expired_locked(self, now_ts: Optional[float] = None) -> None:
        now = now_ts if now_ts is not None else time.time()
        cutoff = now - self.CONSOLE_LOG_WINDOW_SECONDS
        self._purge_before(self._logs, cutoff)
        self._purge_before(self._logs_other, cutoff)
        self._purge_before(self._logs_ffmpeg, cutoff)

    @staticmethod
    def _is_ffmpeg_log(line: str) -> bool:
        s = (line or "").strip()
        if not s:
            return False
        lower = s.lower()
        if "[cmd] ffmpeg" in lower or "ffmpeg exited with code" in lower:
            return True
        if s.startswith("frame=") or s.startswith("size="):
            return True
        prefixes = (
            "[INFO]", "[WARN]", "[ERROR]", "[STATUS]", "[PREFETCH]", "[CMD]", "[DETAIL]", "[DEBUG]"
        )
        if any(s.startswith(prefix) for prefix in prefixes):
            return False
        return True

    def append_log(self, line: str) -> None:
        text = (line or "").rstrip()
        if not text:
            return
        now = time.time()
        with self._lock:
            self._logs.append((now, text))
            if self._is_ffmpeg_log(text):
                self._logs_ffmpeg.append((now, text))
            else:
                self._logs_other.append((now, text))
            self._purge_expired_locked(now)
            self._updated_at = now

    def set_status(self, status: str) -> None:
        with self._lock:
            self._status = (status or "Idle").strip() or "Idle"
            self._updated_at = time.time()

    def set_streaming(self, streaming: bool) -> None:
        with self._lock:
            self._streaming = bool(streaming)
            self._updated_at = time.time()

    def set_meta(self, **kwargs: object) -> None:
        with self._lock:
            self._meta.update(kwargs)
            self._updated_at = time.time()

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            self._purge_expired_locked()
            return {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "streaming": self._streaming,
                "status": self._status,
                "updated_at": self._updated_at,
                "meta": dict(self._meta),
                "logs": [line for _ts, line in self._logs],
                "logs_other": [line for _ts, line in self._logs_other],
                "logs_ffmpeg": [line for _ts, line in self._logs_ffmpeg],
            }


class LocalWebDashboard:
    """Small local HTTP server to monitor and control stream runtime."""

    def __init__(
        self,
        host: str,
        port: int,
        state_provider: Callable[[], Dict[str, object]],
        settings_provider: Callable[[], Dict[str, object]],
        settings_updater: Callable[[Dict[str, object]], Dict[str, object]],
        binaries_status_provider: Callable[[], Dict[str, object]],
        binaries_update_trigger: Callable[[], Dict[str, object]],
        app_update_status_provider: Callable[[], Dict[str, object]],
        app_update_check_trigger: Callable[[Optional[Dict[str, object]]], Dict[str, object]],
        app_update_download_trigger: Callable[[Optional[Dict[str, object]]], Dict[str, object]],
        start_cb: Callable[[], None],
        stop_cb: Callable[[], None],
        skip_cb: Callable[[], None],
        log_cb: Optional[Callable[[str], None]] = None,
    ):
        self.host = host
        self.port = int(port)
        self._state_provider = state_provider
        self._settings_provider = settings_provider
        self._settings_updater = settings_updater
        self._binaries_status_provider = binaries_status_provider
        self._binaries_update_trigger = binaries_update_trigger
        self._app_update_status_provider = app_update_status_provider
        self._app_update_check_trigger = app_update_check_trigger
        self._app_update_download_trigger = app_update_download_trigger
        self._start_cb = start_cb
        self._stop_cb = stop_cb
        self._skip_cb = skip_cb
        self._log_cb = log_cb
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _log(self, line: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(line)
            except Exception:
                pass

    def _handler_factory(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A003
                return

            def _send_json(self, payload: Dict[str, object], status: int = 200) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def _send_html(self, html: str, status: int = 200) -> None:
                data = html.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def _read_body(self) -> Dict[str, object]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except Exception:
                    length = 0
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                if not raw:
                    return {}
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
                return {}

            def do_GET(self):  # noqa: N802
                path = urlsplit(self.path).path
                if path in ("/favicon.ico", "/icon.ico"):
                    ico_path = Path(resource_path("icon.ico"))
                    if not ico_path.exists():
                        self.send_error(HTTPStatus.NOT_FOUND, "icon.ico not found")
                        return
                    try:
                        self._send_bytes(ico_path.read_bytes(), "image/x-icon")
                    except Exception:
                        self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Failed to read icon.ico")
                    return
                if path == "/assets/style.css":
                    css_path = Path(resource_path("web/style.css"))
                    if not css_path.exists():
                        self.send_error(HTTPStatus.NOT_FOUND, "style.css not found")
                        return
                    try:
                        self._send_bytes(css_path.read_bytes(), "text/css; charset=utf-8")
                    except Exception:
                        self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Failed to read style.css")
                    return
                if path == "/assets/app.js":
                    js_path = Path(resource_path("web/app.js"))
                    if not js_path.exists():
                        self.send_error(HTTPStatus.NOT_FOUND, "app.js not found")
                        return
                    try:
                        self._send_bytes(js_path.read_bytes(), "application/javascript; charset=utf-8")
                    except Exception:
                        self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Failed to read app.js")
                    return
                if path == "/api/state":
                    self._send_json(dashboard._state_provider())
                    return
                if path == "/api/settings":
                    self._send_json({"ok": True, "settings": dashboard._settings_provider()})
                    return
                if path == "/api/binaries":
                    self._send_json({"ok": True, "binaries": dashboard._binaries_status_provider()})
                    return
                if path == "/api/app-update":
                    self._send_json({"ok": True, "app_update": dashboard._app_update_status_provider()})
                    return
                if path in ("/", "/index.html"):
                    self._send_html(dashboard._build_index_html())
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")

            def do_POST(self):  # noqa: N802
                path = urlsplit(self.path).path
                body = self._read_body()
                if path == "/api/start":
                    dashboard._start_cb()
                    self._send_json({"ok": True})
                    return
                if path == "/api/stop":
                    dashboard._stop_cb()
                    self._send_json({"ok": True})
                    return
                if path == "/api/skip":
                    dashboard._skip_cb()
                    self._send_json({"ok": True})
                    return
                if path == "/api/settings":
                    try:
                        updated = dashboard._settings_updater(body)
                        self._send_json({"ok": True, "settings": updated})
                    except Exception as e:
                        self._send_json({"ok": False, "error": str(e)}, status=400)
                    return
                if path == "/api/binaries/update":
                    try:
                        info = dashboard._binaries_update_trigger()
                        self._send_json({"ok": True, "binaries": info})
                    except Exception as e:
                        self._send_json({"ok": False, "error": str(e)}, status=400)
                    return
                if path == "/api/app-update/download":
                    try:
                        info = dashboard._app_update_download_trigger(body if isinstance(body, dict) else None)
                        self._send_json({"ok": True, "app_update": info})
                    except Exception as e:
                        self._send_json({"ok": False, "error": str(e)}, status=400)
                    return
                if path == "/api/app-update/check":
                    try:
                        info = dashboard._app_update_check_trigger(body if isinstance(body, dict) else None)
                        self._send_json({"ok": True, "app_update": info})
                    except Exception as e:
                        self._send_json({"ok": False, "error": str(e)}, status=400)
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        return Handler

    def _build_index_html(self) -> str:
        template_path = Path(resource_path("web/index.html"))
        try:
            html = template_path.read_text(encoding="utf-8")
        except Exception:
            return (
                "<!doctype html><html><head><meta charset='utf-8'><title>"
                + APP_NAME
                + "</title></head><body><h1>"
                + APP_NAME
                + "</h1><p>Dashboard template missing: web/index.html</p></body></html>"
            )

        html = html.replace("{APP_NAME}", APP_NAME)
        html = html.replace("{APP_VERSION}", APP_VERSION)
        html = html.replace("{GITHUB_REPO}", GITHUB_REPO)
        return html

    def start(self) -> bool:
        """Bind and start HTTP server in a daemon thread."""
        if self._server is not None:
            return True
        try:
            self._server = ThreadingHTTPServer((self.host, self.port), self._handler_factory())
        except Exception as e:
            self._log(f"[WARN] Web dashboard failed to start on {self.host}:{self.port}: {e}")
            self._server = None
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._log(f"[INFO] Web dashboard listening on http://{self.host}:{self.port}")
        return True

    def stop(self) -> None:
        if not self._server:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        self._server = None
        self._thread = None

def _download_url(
    url: str,
    dest_path: Path,
    user_agent: Optional[str] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    max_mbps: Optional[int] = None,
    parallel_chunks: int = 8,
) -> None:
    """Download a URL to dest_path atomically.

    Uses a temp file then renames into place to avoid partial files on failure.
    """
    class _RateLimiter:
        """Simple token-bucket limiter shared across download threads."""
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

    # Ensure parent exists before creating a temp file in that directory.
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    chunk_size = 1024 * 256
    capped_mbps = None
    if max_mbps is not None:
        try:
            capped_mbps = max(1, min(25, int(max_mbps)))
        except Exception:
            capped_mbps = None
    max_bytes_per_sec = (capped_mbps * 1_000_000 / 8.0) if capped_mbps else None
    limiter = _RateLimiter(max_bytes_per_sec)

    # Probe range support and content length.
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

    # Multi-threaded ranged download with shared speed cap.
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
        # Fallback to a single-stream download if ranged mode fails.
        _download_single(headers)

def github_latest_asset_url(repo: str, prefer_substrings: List[str], must_match_regex: str = ".*", user_agent: Optional[str] = None) -> Optional[str]:
    """Return browser_download_url of an asset from latest GitHub release.

    Args:
      repo: "owner/name" form
      prefer_substrings: list of substrings to prioritize in asset name order
      must_match_regex: regex that asset name must match
    """
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
        # Filter by regex
        filtered = [a for a in assets if regex.search(a.get("name", ""))]
        if not filtered:
            return None
        # Prefer entries containing preferred substrings in order
        def score(name: str) -> Tuple[int, int]:
            pri = len(prefer_substrings)
            for i, sub in enumerate(prefer_substrings):
                if sub.lower() in name.lower():
                    pri = i
                    break
            # Prefer smaller files might have shorter names; secondary metric by length
            return (pri, len(name))

        best = min(filtered, key=lambda a: score(a.get("name", "")))
        return best.get("browser_download_url")
    except Exception:
        return None

def run_hidden(cmd: List[str], check=False, capture=True, text=True, timeout=None) -> subprocess.CompletedProcess:
    """Run a subprocess without showing a console window."""
    kwargs = {}
    if capture:
        kwargs.update(dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text))
    return subprocess.run(cmd, check=check, timeout=timeout, **kwargs)

def safe_write_text(path: Path, text: str) -> None:
    """Write text to a file, ignoring any errors that occur."""
    try:
        path.write_text(text, encoding="utf-8", errors="ignore")
    except Exception:
        pass

def open_rotating_latest_log() -> Tuple[Optional[TextIO], Optional[Path]]:
    """Open ``latest.log`` for writing, rotating any existing file first."""
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
        # Fallback to CWD if app dir is not writable in service/headless contexts.
        try:
            cwd_path = Path.cwd() / "latest.log"
            if cwd_path.exists():
                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                cwd_path.rename(cwd_path.with_name(f"{cwd_path.stem}-{ts}{cwd_path.suffix}"))
            return cwd_path.open("w", encoding="utf-8"), cwd_path
        except Exception:
            return None, None

def restore_terminal_state() -> None:
    """Best-effort reset of terminal mode after abrupt subprocess/shutdown paths."""
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
    """Detect the type of input URL.
    
    Returns:
        'youtube_playlist' - YouTube playlist URL
        'youtube_video' - Single YouTube video URL
        'twitch_stream' - Twitch channel/stream URL
        'direct_hls' - Direct HLS manifest URL (.m3u8)
        'unknown' - Unrecognized format
    """
    url_lower = url.lower().strip()
    
    # YouTube playlist detection
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        if 'list=' in url_lower:
            return 'youtube_playlist'
        elif 'watch?v=' in url_lower or 'youtu.be/' in url_lower:
            return 'youtube_video'
    
    # Direct HLS manifest (m3u8 extension)
    if url_lower.endswith('.m3u8'):
        return 'direct_hls'
    
    # Twitch channel/stream URL
    if 'twitch.tv' in url_lower:
        return 'twitch_stream'
    
    return 'unknown'

def ffmpeg_lists_encoder(ffmpeg_path: Optional[str], codec: str) -> bool:
    """Return True when ``ffmpeg -encoders`` reports the requested encoder."""
    if not ffmpeg_path:
        return False
    try:
        cp = run_hidden(
            [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-encoders"],
            timeout=8,
        )
        text = f"{cp.stdout or ''}\n{cp.stderr or ''}"
        return bool(re.search(rf"^\s*[A-Z\.]+\s+{re.escape(codec)}\b", text, re.MULTILINE))
    except Exception:
        return False

def ffprobe_encoder(ffmpeg_path: Optional[str], codec: str) -> bool:
    """Check whether ``ffmpeg`` can initialize and use a specific encoder."""
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
    """Return a human‑friendly YouTube upload date in UTC (matching YouTube's display)."""
    def _fmt_day(d: datetime.datetime) -> str:
        return d.strftime("%b %d, %Y").replace(" 0", " ")

    dt = None
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        try:
            # Parse the date string directly - upload_date is YYYYMMDD in UTC
            year = int(upload_date[0:4])
            month = int(upload_date[4:6])
            day = int(upload_date[6:8])
            date_obj = datetime.date(year, month, day)
            # Subtract 1 day to account for timezone offset
            date_obj = date_obj - datetime.timedelta(days=1)
            # Format the corrected date
            dt_for_format = datetime.datetime.combine(date_obj, datetime.time.min)
            return _fmt_day(dt_for_format)
        except Exception:
            pass
    # Fallback to timestamp if upload_date not available
    ts = release_ts or timestamp
    if ts:
        try:
            # Convert timestamp to UTC datetime
            dt = datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc)
            # Subtract 1 day to account for timezone offset
            dt = dt - datetime.timedelta(days=1)
            # Strip timezone info for formatting
            dt = dt.replace(tzinfo=None)
            return _fmt_day(dt)
        except Exception:
            pass
    return None


def _binary_names_for_platform() -> Tuple[str, str]:
    """Return (yt-dlp-name, ffmpeg-name) for supported server platforms."""
    return ("yt-dlp", "ffmpeg")


def _preferred_binary_paths() -> Dict[str, Optional[str]]:
    """Return local-preferred paths used by the app for yt-dlp and ffmpeg."""
    app_dir = _app_dir()
    ytdlp_name, ffmpeg_name = _binary_names_for_platform()
    local_ytdlp = app_dir / ytdlp_name
    local_ffmpeg = app_dir / ffmpeg_name

    ytdlp_path = str(local_ytdlp) if local_ytdlp.exists() else shutil.which("yt-dlp")
    ffmpeg_path = str(local_ffmpeg) if local_ffmpeg.exists() else shutil.which("ffmpeg")
    return {"yt-dlp": ytdlp_path, "ffmpeg": ffmpeg_path}


def _read_tool_version(binary_path: Optional[str], tool: str) -> Optional[str]:
    """Return parsed version string for yt-dlp or ffmpeg."""
    if not binary_path:
        return None
    try:
        if tool == "yt-dlp":
            cp = run_hidden([binary_path, "--version"])
            if cp.returncode == 0 and cp.stdout:
                return cp.stdout.strip().splitlines()[0].strip()
            return None
        if tool == "ffmpeg":
            cp = run_hidden([binary_path, "-version"])
            if cp.returncode == 0:
                line = (cp.stdout or "").strip().splitlines()
                if line:
                    m = re.search(r"ffmpeg version\s+([^\s]+)", line[0], re.IGNORECASE)
                    if m:
                        raw = m.group(1).strip()
                        m2 = re.search(r"(\d+\.\d+(?:\.\d+)?)", raw)
                        return m2.group(1) if m2 else raw
            return None
    except Exception:
        return None
    return None


def _latest_ytdlp_version(user_agent: Optional[str] = None) -> Optional[str]:
    """Return latest yt-dlp version from GitHub releases."""
    try:
        req = urllib.request.Request("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest")
        req.add_header("Accept", "application/vnd.github+json")
        if user_agent:
            req.add_header("User-Agent", user_agent)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = str(data.get("tag_name", "")).strip().lstrip("v")
        return tag or None
    except Exception:
        return None


def _latest_ffmpeg_version(user_agent: Optional[str] = None) -> Optional[str]:
    """Return latest FFmpeg version from the configured update source."""
    try:
        headers = {}
        if user_agent:
            headers["User-Agent"] = user_agent
        if platform.system().lower() == "linux":
            # Match the source used by the in-app Linux updater.
            req = urllib.request.Request("https://johnvansickle.com/ffmpeg/", headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            versions = re.findall(r"ffmpeg-(\d+\.\d+(?:\.\d+)?)-amd64-static\.tar\.xz", html)
            if versions:
                return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))
            rel = re.search(r"release[:\s]+(\d+\.\d+(?:\.\d+)?)", html, re.IGNORECASE)
            if rel:
                return rel.group(1)
            return None

        req = urllib.request.Request("https://ffmpeg.org/download.html", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        versions = re.findall(r"ffmpeg-(\d+\.\d+(?:\.\d+)?)\.tar\.(?:xz|gz|bz2)", html)
        if not versions:
            return None
        return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))
    except Exception:
        return None


def _version_tuple(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Parse a dotted numeric version into a comparable tuple."""
    if not version:
        return None
    m = re.search(r"(\d+(?:\.\d+)+)", version)
    if not m:
        return None
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except Exception:
        return None


def _compare_versions(current: Optional[str], latest: Optional[str]) -> Optional[int]:
    """Compare versions, returning -1 (behind), 0 (equal), 1 (ahead), None (unknown)."""
    c = _version_tuple(current)
    l = _version_tuple(latest)
    if c is None or l is None:
        return None
    max_len = max(len(c), len(l))
    c2 = c + (0,) * (max_len - len(c))
    l2 = l + (0,) * (max_len - len(l))
    if c2 == l2:
        return 0
    return 1 if c2 > l2 else -1


def gather_binary_update_status() -> Dict[str, object]:
    """Collect current and latest version info for yt-dlp and ffmpeg."""
    paths = _preferred_binary_paths()
    ytdlp_current = _read_tool_version(paths.get("yt-dlp"), "yt-dlp")
    ffmpeg_current = _read_tool_version(paths.get("ffmpeg"), "ffmpeg")
    ytdlp_latest = _latest_ytdlp_version(user_agent=f"{APP_NAME}/{APP_VERSION}")
    ffmpeg_latest = _latest_ffmpeg_version(user_agent=f"{APP_NAME}/{APP_VERSION}")

    ytdlp_cmp = _compare_versions(ytdlp_current, ytdlp_latest)
    ffmpeg_cmp = _compare_versions(ffmpeg_current, ffmpeg_latest)

    def status_from_cmp(cmp_value: Optional[int]) -> str:
        if cmp_value is None:
            return "unknown"
        return "up_to_date" if cmp_value >= 0 else "update_available"

    result = {
        "yt-dlp": {
            "path": paths.get("yt-dlp"),
            "current_version": ytdlp_current,
            "latest_version": ytdlp_latest,
            "status": status_from_cmp(ytdlp_cmp),
        },
        "ffmpeg": {
            "path": paths.get("ffmpeg"),
            "current_version": ffmpeg_current,
            "latest_version": ffmpeg_latest,
            "status": status_from_cmp(ffmpeg_cmp),
        },
    }
    statuses = [result["yt-dlp"]["status"], result["ffmpeg"]["status"]]
    result["all_up_to_date"] = all(s == "up_to_date" for s in statuses)
    result["any_update_available"] = any(s == "update_available" for s in statuses)
    return result


def _parse_app_version(version: str) -> Optional[Tuple[List[int], str]]:
    """Parse app version into numeric core and optional pre-release suffix."""
    text = str(version or "").strip().lstrip("vV")
    if not text:
        return None
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", text)
    if not m:
        return None
    try:
        core = [int(part) for part in m.group(1).split(".")]
    except Exception:
        return None
    suffix = str(m.group(2) or "").strip()
    if suffix.startswith("-"):
        suffix = suffix[1:].strip()
    return core, suffix


def _compare_app_versions(target: str, current: str) -> Optional[int]:
    """Compare target vs current app versions: -1 older, 0 equal, 1 newer."""
    p_target = _parse_app_version(target)
    p_current = _parse_app_version(current)
    if not p_target or not p_current:
        return None
    target_core, target_suffix = p_target
    current_core, current_suffix = p_current
    n = max(len(target_core), len(current_core))
    for i in range(n):
        tv = target_core[i] if i < len(target_core) else 0
        cv = current_core[i] if i < len(current_core) else 0
        if tv > cv:
            return 1
        if tv < cv:
            return -1
    # Same numeric core: stable release outranks pre-release.
    if not target_suffix and current_suffix:
        return 1
    if target_suffix and not current_suffix:
        return -1
    if target_suffix == current_suffix:
        return 0
    # Both pre-release; lexical compare is sufficient for this updater.
    return 1 if target_suffix > current_suffix else -1


def _is_version_newer(latest: str, current: str) -> bool:
    """Return True when latest is strictly newer than current."""
    cmp = _compare_app_versions(latest, current)
    if cmp is not None:
        return cmp > 0
    return str(latest) != str(current) and str(latest) > str(current)


def _should_install_selected_release(latest: str, current: str) -> bool:
    """Allow install when selected channel points to a different version (upgrade or downgrade)."""
    cmp = _compare_app_versions(latest, current)
    if cmp is not None:
        return cmp != 0
    return str(latest).strip() != str(current).strip()


def _is_supported_update_version(version: str) -> bool:
    """Return True when version is allowed as an in-app updater target."""
    cmp = _compare_app_versions(version, APP_UPDATE_MIN_VERSION)
    if cmp is not None:
        return cmp >= 0
    return str(version or "").strip().lstrip("vV") >= APP_UPDATE_MIN_VERSION


def _pick_release_asset(assets: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    """Pick the best app update asset for current OS (prefers runnable binaries)."""
    if not assets:
        return None
    sys_name = platform.system().lower()

    def score(asset: Dict[str, object]) -> Tuple[int, int, int, int, int]:
        name = str(asset.get("name", "")).lower()
        pri_binary_ext = 1
        if name.endswith((".appimage", ".bin")):
            pri_binary_ext = 0
        if "." not in name.split("/")[-1]:
            pri_binary_ext = 0
        pri_archive = 0 if name.endswith((".zip", ".tar.gz", ".tgz")) else 1
        pri_platform = 0 if (sys_name == "linux" and "linux" in name) else 1
        pri_server = 0 if "server" in name else 1
        pri_app = 0 if "stream247" in name else 1
        return (pri_binary_ext, pri_archive, pri_platform, pri_server, pri_app)

    try:
        return sorted(assets, key=score)[0]
    except Exception:
        return assets[0]


def _is_release_asset_self_installable(asset_name: str) -> bool:
    """Return True when a release asset can be self-installed by this runtime."""
    name = str(asset_name or "").strip().lower()
    if not name:
        return False
    if name.endswith((".sh", ".ps1", ".bat", ".cmd", ".msi", ".zip", ".tar.gz", ".tgz")):
        return False
    return True


def _pick_release_by_channel(releases: List[Dict[str, object]], update_channel: str) -> Optional[Dict[str, object]]:
    """Pick the newest release matching the requested channel."""
    channel = str(update_channel or "release").strip().lower()
    if channel == "prerelease":
        for rel in releases:
            if bool(rel.get("draft", False)):
                continue
            if bool(rel.get("prerelease", False)):
                return rel
        return None
    for rel in releases:
        if bool(rel.get("draft", False)):
            continue
        if not bool(rel.get("prerelease", False)):
            return rel
    return None


def _release_version_string(release: Dict[str, object]) -> str:
    """Return normalized version text from a GitHub release record."""
    return str(release.get("tag_name", "")).strip().lstrip("vV")


def _filter_releases_for_channel(releases: List[Dict[str, object]], update_channel: str) -> List[Dict[str, object]]:
    """Return releases (newest-first) matching the configured app update channel."""
    channel = str(update_channel or "release").strip().lower()
    out: List[Dict[str, object]] = []
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        if bool(rel.get("draft", False)):
            continue
        is_pre = bool(rel.get("prerelease", False))
        if channel == "prerelease":
            if is_pre:
                out.append(rel)
            continue
        if not is_pre:
            out.append(rel)
    return out


def _pick_release_by_version(
    releases: List[Dict[str, object]],
    selected_version: Optional[str] = None,
) -> Optional[Dict[str, object]]:
    """Pick selected release version when present; otherwise return newest for channel."""
    if not releases:
        return None
    wanted = str(selected_version or "").strip().lstrip("vV")
    if wanted:
        for rel in releases:
            if _release_version_string(rel) == wanted:
                return rel
    return releases[0]


def fetch_latest_app_release_info(update_channel: str = "release", selected_version: Optional[str] = None) -> Dict[str, object]:
    """Return app release metadata for web updater, including selectable channel versions."""
    channel = str(update_channel or "release").strip().lower()
    if channel not in WEB_ALLOWED_UPDATE_CHANNELS:
        channel = "release"
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page={WEB_UPDATE_RELEASES_LIMIT}")
    req.add_header("User-Agent", f"{APP_NAME}/{APP_VERSION}")
    with urllib.request.urlopen(req, timeout=15) as response:
        all_releases = json.loads(response.read().decode("utf-8"))
    if not isinstance(all_releases, list):
        raise RuntimeError("Invalid releases response from GitHub.")

    channel_releases = _filter_releases_for_channel(all_releases, channel)
    channel_releases = [
        rel for rel in channel_releases
        if _is_supported_update_version(_release_version_string(rel))
    ]
    wanted = str(selected_version or "").strip().lstrip("vV")
    if wanted and not _is_supported_update_version(wanted):
        raise RuntimeError(
            f"Selected version {wanted} is below the minimum supported in-app update target ({APP_UPDATE_MIN_VERSION})."
        )
    if not channel_releases:
        if channel == "prerelease":
            raise RuntimeError(
                f"No supported pre-release found (minimum in-app target is {APP_UPDATE_MIN_VERSION})."
            )
        raise RuntimeError(
            f"No supported release found (minimum in-app target is {APP_UPDATE_MIN_VERSION})."
        )
    latest_rel = channel_releases[0]
    selected_rel = _pick_release_by_version(channel_releases, selected_version)
    if not isinstance(selected_rel, dict):
        raise RuntimeError("Unable to select app release version.")

    latest_for_channel = _release_version_string(latest_rel)
    selected_release_version = _release_version_string(selected_rel)
    data = selected_rel
    release_url = str(data.get("html_url", ""))
    assets = data.get("assets", []) or []
    selected = _pick_release_asset(assets)
    download_url = ""
    asset_name = ""
    if isinstance(selected, dict):
        download_url = str(selected.get("browser_download_url", ""))
        asset_name = str(selected.get("name", ""))
    available_versions = [_release_version_string(rel) for rel in channel_releases if _release_version_string(rel)]
    return {
        "current_version": APP_VERSION,
        "latest_version": selected_release_version,
        "selected_version": selected_release_version,
        "latest_channel_version": latest_for_channel,
        "is_newer": _is_version_newer(selected_release_version, APP_VERSION),
        "should_install": _should_install_selected_release(selected_release_version, APP_VERSION),
        "release_url": release_url,
        "channel": channel,
        "download_url": download_url,
        "asset_name": asset_name,
        "asset_supported": _is_release_asset_self_installable(asset_name),
        "available_versions": available_versions,
    }


# ---------- buffer presets ----------
BUFFER_PRESETS = {
    "Low": {
        "probesize": "15M",
        "analyzeduration": "5000000",  # 5 seconds
        "buffer_size": "2048k",
        "max_delay": "3000000",  # 3 seconds in microseconds
    },
    "Medium": {
        "probesize": "25M",
        "analyzeduration": "10000000",  # 10 seconds
        "buffer_size": "4096k",
        "max_delay": "7000000",  # 7 seconds in microseconds
    },
    "High": {
        "probesize": "40M",
        "analyzeduration": "15000000",  # 15 seconds
        "buffer_size": "6144k",
        "max_delay": "12000000",  # 12 seconds in microseconds
    },
    "Ultra": {
        "probesize": "50M",
        "analyzeduration": "30000000",  # 30 seconds
        "buffer_size": "8192k",
        "max_delay": "25000000",  # 25 seconds in microseconds
    }
}

# Shared presets for runtime config parsing.
RESOLUTION_PRESETS: Dict[str, Tuple[int, str]] = {
    "480p": (480, "1500k"),
    "720p": (720, "2500k"),
    "1080p": (1080, "6000k"),
    "1440p": (1440, "9000k"),
    "2160p": (2160, "25000k"),
}

# ---------- streaming core ----------
@dataclass
class StreamConfig:
    """Configuration options for the livestream."""

    playlist_url: str
    stream_key: str
    rtmp_base: str = "rtmp://a.rtmp.youtube.com/live2"
    fps: int = 30
    height: int = 720
    video_bitrate: str = "2500k"
    bufsize: str = "7500k"
    audio_bitrate: str = "128k"
    overlay_titles: bool = True
    shuffle: bool = False
    title_file: str = "current_title.txt"
    buffer_mode: str = "Medium"  # Low, Medium, or High
    yt_auth_enabled: bool = False
    yt_auth_browser: str = "auto"  # auto, chrome, edge, firefox, ...
    yt_auth_profile: str = ""  # Optional custom profile root path
    yt_auth_allow_unauth_fallback: bool = True
    update_download_cap_mbps: int = 25
    encoder_preference: str = "auto"  # auto or explicit encoder id

    # runtime-selected
    encoder: str = "libx264"
    encoder_name: str = "CPU x264"
    pix_fmt: str = "yuv420p"
    extra_venc_flags: List[str] = None  # type: ignore
    _overlay_fontsize: int = 24  # Optional runtime field for overlay fontsize

    def rtmp_url(self) -> str:
        """Construct the full RTMP URL using the base and stream key."""
        return f"{self.rtmp_base}/{self.stream_key}"


def stream_config_from_settings(data: Dict[str, object]) -> StreamConfig:
    """Build StreamConfig from a persisted settings dictionary."""
    _, playlist_url = _resolved_sources_and_playlist(data)
    resolution = str(data.get("resolution", "720p"))
    height, preset_bitrate = RESOLUTION_PRESETS.get(
        resolution, RESOLUTION_PRESETS["720p"]
    )
    fps_raw = data.get("framerate", 30)
    try:
        fps = int(fps_raw)
    except Exception:
        fps = 30
    if fps not in (30, 60):
        fps = 30
    try:
        cap_mbps = int(data.get("update_download_cap_mbps", 25) or 25)
    except Exception:
        cap_mbps = 25
    cap_mbps = max(1, min(25, cap_mbps))
    buffer_mode = str(data.get("buffer_mode", "Medium")).strip() or "Medium"
    if buffer_mode not in WEB_ALLOWED_BUFFER_MODES:
        buffer_mode = "Medium"
    preset_kbps = _parse_bitrate_kbps(preset_bitrate, BITRATE_DEFAULT_KBPS)
    video_kbps = _parse_bitrate_kbps(data.get("video_bitrate", preset_bitrate), preset_kbps)
    # Derive ffmpeg bufsize from selected stream-buffer mode.
    buf_mult = float(BUFFER_MODE_BUFSIZE_MULTIPLIER.get(buffer_mode, BUFFER_MODE_BUFSIZE_MULTIPLIER["Medium"]))
    bufsize_kbps = int(max(BITRATE_MIN_KBPS, round(video_kbps * buf_mult)))

    return StreamConfig(
        playlist_url=playlist_url,
        stream_key=str(data.get("stream_key", "")).strip(),
        rtmp_base=str(data.get("rtmp_base", "rtmp://a.rtmp.youtube.com/live2")).strip(),
        fps=fps,
        height=height,
        video_bitrate=_kbps_to_text(video_kbps),
        bufsize=_kbps_to_text(bufsize_kbps),
        audio_bitrate=str(data.get("audio_bitrate", "128k")).strip() or "128k",
        overlay_titles=bool(data.get("overlay_titles", True)),
        shuffle=bool(data.get("shuffle", False)),
        title_file=str(data.get("title_file", "current_title.txt")).strip() or "current_title.txt",
        buffer_mode=buffer_mode,
        yt_auth_enabled=bool(data.get("yt_auth_enabled", False)),
        yt_auth_browser=str(data.get("yt_auth_browser", "auto")).strip() or "auto",
        yt_auth_profile=str(data.get("yt_auth_profile", "")).strip(),
        yt_auth_allow_unauth_fallback=bool(data.get("yt_auth_allow_unauth_fallback", True)),
        update_download_cap_mbps=cap_mbps,
        encoder_preference=str(data.get("encoder_preference", "auto")).strip() or "auto",
    )


class StreamWorker(QtCore.QObject):
    """Background worker that handles playlist streaming with ffmpeg."""

    log = QtCore.Signal(str)
    status = QtCore.Signal(str)
    finished = QtCore.Signal()
    FFMPEG_STATS_EMIT_INTERVAL = 0.25
    RTMP_HANDOFF_DELAY_SEC = 1.0
    RTMP_SKIP_HANDOFF_DELAY_SEC = 0.35
    RTMP_FAST_HANDOFF_DELAY_SEC = 0.0
    RTMP_FAST_SKIP_HANDOFF_DELAY_SEC = 0.0
    PREFETCH_WAIT_TIMEOUT_SEC = 1.2
    PREFETCH_WAIT_TIMEOUT_FAST_SEC = 0.25

    ff_proc: Optional[subprocess.Popen]

    def __init__(self, cfg: StreamConfig, parent=None):
        """Store configuration and initialise worker state."""
        super().__init__(parent)
        self.cfg = cfg
        self._stop = threading.Event()
        self._skip = threading.Event()
        self.ffmpeg_path = find_ffmpeg()
        self.ytdlp_path = find_ytdlp()
        self.ff_proc = None
        self._rtmp_bridge_proc: Optional[subprocess.Popen] = None
        self._rtmp_bridge_write_fd: Optional[int] = None
        self._rtmp_live_protocol_opts_enabled = True
        # Prefetch cache for next video
        self._prefetch_video_id: Optional[str] = None
        self._prefetch_title: Optional[str] = None
        self._prefetch_date: Optional[str] = None
        self._prefetch_vurl: Optional[str] = None
        self._prefetch_aurl: Optional[str] = None
        self._prefetch_thread: Optional[threading.Thread] = None
        self._prefetch_target_video_id: Optional[str] = None
        self._prefetch_lock = threading.Lock()
        self._prefetch_ready = threading.Event()
        self._cookie_args_cache: Optional[List[List[str]]] = None
        self._last_working_cookie_args: Optional[List[str]] = None
        self._cookie_fail_logged: set = set()
        self._cookie_fallback_logged = False
        self._cookie_profile_warned = False
        self._ffmpeg_stats_lock = threading.Lock()
        self._last_ffmpeg_stats_emit = 0.0

    def _emit_ffmpeg_line(self, line: str) -> None:
        """Emit ffmpeg output with light throttling for frequent stats lines."""
        text = (line or "").rstrip()
        if not text:
            return
        if text.startswith("frame="):
            now = time.monotonic()
            with self._ffmpeg_stats_lock:
                if now - self._last_ffmpeg_stats_emit < self.FFMPEG_STATS_EMIT_INTERVAL:
                    return
                self._last_ffmpeg_stats_emit = now
        self.log.emit(text)

    def _maybe_switch_to_system_ffmpeg(self, reason: str) -> bool:
        """Switch to PATH ffmpeg when the bundled binary misbehaves."""
        system_ffmpeg = shutil.which("ffmpeg")
        if not system_ffmpeg:
            return False
        try:
            if self.ffmpeg_path and Path(self.ffmpeg_path).resolve() == Path(system_ffmpeg).resolve():
                return False
        except Exception:
            pass
        self.log.emit(f"[WARN] {reason}. Switching to system ffmpeg: {system_ffmpeg}")
        self.ffmpeg_path = system_ffmpeg
        try:
            # Re-evaluate best encoder for the newly selected binary.
            self.select_encoder()
            self.log.emit(f"[INFO] Re-selected encoder: {self.cfg.encoder_name} ({self.cfg.encoder})")
        except Exception as e:
            self.log.emit(f"[WARN] Could not re-select encoder after ffmpeg switch: {e}")
        return True

    def _rtmp_host(self) -> str:
        """Return lowercased RTMP host for destination-aware tuning."""
        try:
            return (urlsplit(self.cfg.rtmp_base).hostname or "").strip().lower()
        except Exception:
            return ""

    def _is_youtube_rtmp(self) -> bool:
        """Return True for YouTube ingest destinations."""
        host = self._rtmp_host()
        return host.endswith("youtube.com") or ("youtube" in host)

    def _prefetch_wait_timeout(self) -> float:
        """Use shorter prefetch waits for low-latency ingest servers (e.g. Owncast)."""
        if self._is_youtube_rtmp():
            return self.PREFETCH_WAIT_TIMEOUT_SEC
        return self.PREFETCH_WAIT_TIMEOUT_FAST_SEC

    def _io_join_timeout(self) -> float:
        """Use shorter reader-thread joins on low-latency RTMP targets."""
        if self._is_youtube_rtmp():
            return 0.2
        return 0.05

    def _transition_retry_delay(self) -> float:
        """Delay before retrying next item after an error."""
        if self._is_youtube_rtmp():
            return 2.0
        return 0.25

    def _default_auth_browsers(self) -> List[str]:
        """Return a browser probe order based on OS for --cookies-from-browser."""
        # Linux and others
        return ["firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera"]

    def _normalize_auth_browser(self) -> str:
        """Return the configured browser in normalized yt-dlp naming."""
        b = (self.cfg.yt_auth_browser or "auto").strip().lower()
        allowed = {"auto", "chrome", "chromium", "edge", "firefox", "brave", "vivaldi", "opera"}
        return b if b in allowed else "auto"

    def _candidate_browsers(self) -> List[str]:
        """Return browser candidates in attempt order."""
        chosen = self._normalize_auth_browser()
        if chosen != "auto":
            return [chosen]
        return self._default_auth_browsers()

    def _linux_browser_profile_roots(self, browser: str) -> List[str]:
        """Return existing Linux profile roots for sandboxed browser installs."""
        if platform.system().lower() != "linux":
            return []
        home = Path.home()
        roots = {
            "firefox": [
                home / ".var/app/org.mozilla.firefox/.mozilla/firefox",
                home / "snap/firefox/common/.mozilla/firefox",
            ],
            "chrome": [
                home / ".var/app/com.google.Chrome/config/google-chrome",
            ],
            "chromium": [
                home / ".var/app/org.chromium.Chromium/config/chromium",
                home / "snap/chromium/common/chromium",
            ],
            "brave": [
                home / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
            ],
            "edge": [
                home / ".var/app/com.microsoft.Edge/config/microsoft-edge",
            ],
            "vivaldi": [
                home / ".var/app/com.vivaldi.Vivaldi/config/vivaldi",
            ],
            "opera": [
                home / ".var/app/com.opera.Opera/config/opera",
            ],
        }.get(browser, [])
        return [p.as_posix() for p in roots if p.exists()]

    def _browser_keyring_suffixes(self, browser: str) -> List[str]:
        """Return keyring suffixes to improve Linux Chromium-family compatibility."""
        if platform.system().lower() != "linux":
            return [""]
        if browser in {"chrome", "chromium", "brave", "edge", "vivaldi", "opera"}:
            return ["", "+basictext", "+gnomekeyring"]
        return [""]

    def _build_cookie_arg_sets(self) -> List[List[str]]:
        """Build ordered yt-dlp cookie argument sets with browser/profile fallbacks."""
        if not self.cfg.yt_auth_enabled:
            return [[]]

        custom_profile = (self.cfg.yt_auth_profile or "").strip()
        expanded_custom_profile = ""
        if custom_profile:
            expanded_custom_profile = Path(custom_profile).expanduser().as_posix()
            if (not self._cookie_profile_warned) and (not Path(expanded_custom_profile).exists()):
                self.log.emit(f"[WARN] Cookie profile path not found: {expanded_custom_profile}")
                self._cookie_profile_warned = True
        browsers = self._candidate_browsers()
        specs: List[str] = []

        for browser in browsers:
            keyrings = self._browser_keyring_suffixes(browser)
            profile_roots: List[str] = []
            if expanded_custom_profile:
                profile_roots.append(expanded_custom_profile)
            profile_roots.extend(self._linux_browser_profile_roots(browser))

            for kr in keyrings:
                specs.append(f"{browser}{kr}")
                for root in profile_roots:
                    specs.append(f"{browser}{kr}:{root}")

        # de-dup while preserving order
        seen = set()
        unique_specs: List[str] = []
        for spec in specs:
            if spec in seen:
                continue
            seen.add(spec)
            unique_specs.append(spec)

        arg_sets = [["--cookies-from-browser", spec] for spec in unique_specs]
        if self.cfg.yt_auth_allow_unauth_fallback:
            arg_sets.append([])
        return arg_sets or [[]]

    def _cookie_args(self) -> List[List[str]]:
        if self._cookie_args_cache is None:
            self._cookie_args_cache = self._build_cookie_arg_sets()
        return self._cookie_args_cache

    def _cookie_error(self, stderr: str) -> bool:
        s = (stderr or "").lower()
        markers = (
            "cookie",
            "cookies-from-browser",
            "could not copy",
            "database is locked",
            "failed to decrypt",
            "keyring",
            "permission denied",
            "browser",
            "profile",
        )
        return any(m in s for m in markers)

    def _cookie_desc(self, cookie_args: List[str]) -> str:
        if not cookie_args:
            return "none"
        if len(cookie_args) >= 2 and cookie_args[0] == "--cookies-from-browser":
            return cookie_args[1]
        return "custom"

    def _run_ytdlp(self, args: List[str], timeout=None) -> subprocess.CompletedProcess:
        """Run yt-dlp with cookie-auth fallbacks and optional unauth fallback."""
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp not found.")

        ordered: List[List[str]] = []
        if self._last_working_cookie_args is not None:
            ordered.append(self._last_working_cookie_args)
        ordered.extend(self._cookie_args())

        # de-dup list-of-lists
        deduped: List[List[str]] = []
        seen = set()
        for cargs in ordered:
            key = tuple(cargs)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(cargs)

        last_cp = None
        had_cookie_fail = False
        for cargs in deduped:
            cp = run_hidden([self.ytdlp_path, *cargs, *args], timeout=timeout)
            last_cp = cp
            if cp.returncode == 0:
                if cargs:
                    self._last_working_cookie_args = cargs
                elif had_cookie_fail and not self._cookie_fallback_logged:
                    self.log.emit("[WARN] Browser cookie auth failed; continuing without auth cookies.")
                    self._cookie_fallback_logged = True
                return cp

            if not cargs:
                # unauth fallback failed too, return final error
                return cp

            err = (cp.stderr or "").strip()
            if self._cookie_error(err):
                had_cookie_fail = True
                desc = self._cookie_desc(cargs)
                if desc not in self._cookie_fail_logged:
                    self._cookie_fail_logged.add(desc)
                    self.log.emit(f"[WARN] Cookie auth attempt failed: {desc}")
                continue

            # Not cookie related; return immediately.
            return cp

        # Should not happen, but keep a sane fallback.
        if last_cp is not None:
            return last_cp
        return run_hidden([self.ytdlp_path, *args], timeout=timeout)

    # ---------- dependency ensure / auto-download ----------
    def ensure_binaries(
        self,
        force: bool = False,
        progress_cb: Optional[Callable[[str, int], None]] = None,
        force_ytdlp: Optional[bool] = None,
        force_ffmpeg: Optional[bool] = None,
    ):
        """Ensure yt-dlp and ffmpeg are available; auto-download per OS when missing."""
        app_dir = _app_dir()
        sys_name = platform.system().lower()
        machine = platform.machine().lower()
        if force_ytdlp is None:
            force_ytdlp = force
        if force_ffmpeg is None:
            force_ffmpeg = force

        def _emit_progress(message: str, percent: int) -> None:
            if not progress_cb:
                return
            try:
                progress_cb(message, max(0, min(100, int(percent))))
            except Exception:
                pass

        def _mk_dl_progress(base: int, span: int, label: str) -> Callable[[int, int], None]:
            last_pct = -1
            def _cb(downloaded: int, total: int) -> None:
                nonlocal last_pct
                if total > 0:
                    pct = int((downloaded * 100) / total)
                    if pct == last_pct:
                        return
                    # Throttle UI updates to every 2%
                    if last_pct >= 0 and pct < last_pct + 2 and pct < 100:
                        return
                    last_pct = pct
                    overall = base + int((span * pct) / 100)
                    _emit_progress(f"Downloading {label}... {pct}%", overall)
            return _cb

        if sys_name == "linux":
            ytdlp_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux"
            ytdlp_regex = r"yt-dlp_linux$"
            ytdlp_local_name = "yt-dlp"
            ffmpeg_local_name = "ffmpeg"
        else:
            self.log.emit(f"[WARN] Unsupported OS for auto-download: {platform.system()}")
            return

        # Discover system binaries for fallback only.
        sys_ytdlp = shutil.which("yt-dlp")
        sys_ffmpeg = shutil.which("ffmpeg")

        # Ensure a local yt-dlp next to the app and prefer using it
        local_ytdlp = app_dir / ytdlp_local_name
        need_ytdlp_download = bool(force_ytdlp) or (not local_ytdlp.exists())
        _emit_progress("Checking yt-dlp...", 5)
        if need_ytdlp_download:
            try:
                if force_ytdlp:
                    self.log.emit(f"[INFO] Updating {ytdlp_local_name} to latest release…")
                else:
                    self.log.emit(f"[INFO] {ytdlp_local_name} not found next to the app — downloading latest release…")
                _emit_progress("Starting yt-dlp download...", 10)
                _download_url(
                    ytdlp_url,
                    local_ytdlp,
                    user_agent=f"{APP_NAME}/{APP_VERSION}",
                    progress_cb=_mk_dl_progress(10, 35, ytdlp_local_name),
                    max_mbps=self.cfg.update_download_cap_mbps,
                )
                try:
                    os.chmod(local_ytdlp, 0o755)
                except Exception:
                    pass
                self.log.emit(f"[INFO] Downloaded {ytdlp_local_name}")
                _emit_progress("Installed yt-dlp.", 50)
            except Exception:
                # Fallback via API
                alt = github_latest_asset_url(
                    "yt-dlp/yt-dlp",
                    prefer_substrings=[ytdlp_local_name],
                    must_match_regex=ytdlp_regex,
                    user_agent=f"{APP_NAME}/{APP_VERSION}"
                )
                if alt:
                    try:
                        _emit_progress("Retrying yt-dlp download via API fallback...", 20)
                        _download_url(
                            alt,
                            local_ytdlp,
                            user_agent=f"{APP_NAME}/{APP_VERSION}",
                            progress_cb=_mk_dl_progress(20, 25, ytdlp_local_name),
                            max_mbps=self.cfg.update_download_cap_mbps,
                        )
                        try:
                            os.chmod(local_ytdlp, 0o755)
                        except Exception:
                            pass
                        self.log.emit(f"[INFO] Downloaded {ytdlp_local_name} via API fallback")
                        _emit_progress("Installed yt-dlp (fallback).", 50)
                    except Exception as e2:
                        self.log.emit(f"[WARN] Failed to download {ytdlp_local_name} automatically: {e2}")
                else:
                    self.log.emit(f"[WARN] Could not determine latest {ytdlp_local_name} download URL")
        else:
            _emit_progress("yt-dlp already present.", 50)
        # Prefer local copy if available
        if local_ytdlp.exists():
            self.ytdlp_path = str(local_ytdlp)
        elif sys_ytdlp:
            self.ytdlp_path = sys_ytdlp
            self.log.emit(f"[WARN] Falling back to system yt-dlp: {sys_ytdlp}")
            _emit_progress("Using system yt-dlp fallback.", 50)

        # Ensure a local ffmpeg next to the app and prefer using it
        local_ffmpeg = app_dir / ffmpeg_local_name
        need_ffmpeg_download = bool(force_ffmpeg) or (not local_ffmpeg.exists())
        _emit_progress("Checking FFmpeg...", 55)
        if need_ffmpeg_download:
            try:
                if machine not in ("x86_64", "amd64"):
                    raise RuntimeError(f"Unsupported Linux architecture for auto-download: {machine}")
                if force_ffmpeg:
                    self.log.emit("[INFO] Updating ffmpeg to latest Linux build…")
                else:
                    self.log.emit("[INFO] ffmpeg not found next to the app — downloading latest Linux build…")
                _emit_progress("Starting FFmpeg download...", 60)
                ffmpeg_url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
                dest_tar = app_dir / "ffmpeg-latest.tar.xz"
                _download_url(
                    ffmpeg_url,
                    dest_tar,
                    user_agent=f"{APP_NAME}/{APP_VERSION}",
                    progress_cb=_mk_dl_progress(60, 30, "ffmpeg"),
                    max_mbps=self.cfg.update_download_cap_mbps,
                )
                ffmpeg_bin_path = None
                try:
                    with tarfile.open(dest_tar, "r:xz") as tf:
                        member = next((m for m in tf.getmembers() if m.name.endswith("/ffmpeg")), None)
                        if not member:
                            raise RuntimeError("ffmpeg not found inside archive")
                        with tf.extractfile(member) as src, open(local_ffmpeg, "wb") as out:
                            if src is None:
                                raise RuntimeError("Failed to extract ffmpeg from archive")
                            shutil.copyfileobj(src, out)
                        ffmpeg_bin_path = local_ffmpeg
                finally:
                    try:
                        dest_tar.unlink(missing_ok=True)
                    except Exception:
                        pass

                if ffmpeg_bin_path and ffmpeg_bin_path.exists():
                    try:
                        os.chmod(ffmpeg_bin_path, 0o755)
                    except Exception:
                        pass
                    self.ffmpeg_path = str(ffmpeg_bin_path)
                    self.log.emit("[INFO] FFmpeg downloaded and ready")
                    _emit_progress("Installed FFmpeg.", 95)
            except Exception as e:
                self.log.emit(
                    f"[ERROR] Could not auto-download FFmpeg. Please place {ffmpeg_local_name} next to the app or install FFmpeg in PATH."
                )
                self.log.emit(f"[DETAIL] {e}")
        else:
            _emit_progress("FFmpeg already present.", 95)
        # Prefer local copy if available
        if local_ffmpeg.exists():
            self.ffmpeg_path = str(local_ffmpeg)
        elif sys_ffmpeg:
            self.ffmpeg_path = sys_ffmpeg
            self.log.emit(f"[WARN] Falling back to system ffmpeg: {sys_ffmpeg}")
            _emit_progress("Using system FFmpeg fallback.", 95)

        _emit_progress("Binary update complete.", 100)

    def preflight_rtmp(self) -> bool:
        """Quickly validate RTMP endpoint by pushing a 1-second test stream.

        Returns True on success; logs errors and returns False on failure.
        """
        try:
            if not self._is_youtube_rtmp():
                self.log.emit("[INFO] Preflight: non-YouTube RTMP target detected; skipping probe.")
                return True

            # Build minimal test command using color source (video) and anullsrc (audio)
            gop = max(2, self.cfg.fps * 2)
            vf_chain = [
                "scale=-2:360:flags=bicubic",
                "format=yuv420p",
            ]
            # Use a safe, software-only encoder for preflight to avoid HW quirks.
            preflight_encoder = "libx264"
            def try_push(url: str) -> Tuple[bool, str, int]:
                cmd = [
                    self.ffmpeg_path or "ffmpeg",
                    "-hide_banner", "-loglevel", "warning", "-stats",
                    "-re", "-f", "lavfi", "-i", f"color=black:s=640x360:rate={self.cfg.fps}",
                    "-f", "lavfi", "-i", "anullsrc=cl=stereo:r=44100",
                    "-t", "1",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", preflight_encoder,
                    "-fflags", "+genpts",
                    "-r", str(self.cfg.fps), "-g", str(gop), "-keyint_min", str(gop),
                    "-b:v", "1000k", "-maxrate", "1000k", "-bufsize", "2000k",
                    "-vf", ",".join(vf_chain),
                    "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
                    "-f", "flv", url,
                ]
                try:
                    cp = run_hidden(cmd, capture=True, timeout=15)
                    stderr = (cp.stderr or "").strip()
                    stdout = (cp.stdout or "").strip()
                    if cp.returncode == 0:
                        return (True, "", 0)
                    err = stderr or stdout or f"ffmpeg exited with code {cp.returncode}"
                    return (False, err, cp.returncode)
                except subprocess.TimeoutExpired:
                    return (False, "RTMP preflight timed out", -1)
                except Exception as e:
                    return (False, f"RTMP preflight exception: {e}", -1)

            self.log.emit("[INFO] Preflight: testing RTMP connectivity…")
            url = self.cfg.rtmp_url()
            ok, err, rc = try_push(url)
            if ok:
                self.log.emit("[INFO] Preflight: RTMP OK")
                return True

            # Attempt RTMPS fallback if original was RTMP
            try:
                from urllib.parse import urlparse, urlunparse
                u = urlparse(url)
                if u.scheme == "rtmp":
                    # Switch to rtmps and default to port 443 if none set or was 1935
                    netloc = u.netloc
                    host, sep, port = netloc.partition(":")
                    new_port = "443"
                    new_netloc = f"{host}:{new_port}" if host else netloc
                    rtmps_url = urlunparse(("rtmps", new_netloc, u.path, u.params, u.query, u.fragment))
                    self.log.emit("[INFO] Preflight: RTMP failed, trying RTMPS fallback…")
                    ok2, err2, rc2 = try_push(rtmps_url)
                    if ok2:
                        self.log.emit("[INFO] Preflight: RTMPS OK")
                        # Update cfg to use rtmps for the session
                        self.cfg.rtmp_base = rtmps_url.rsplit("/", 1)[0]
                        self.cfg.stream_key = rtmps_url.rsplit("/", 1)[-1]
                        return True
                    if rc2 < 0:
                        self.log.emit(f"[WARN] RTMPS preflight crashed ({rc2}); skipping preflight.")
                        return True
                    self.log.emit(f"[ERROR] RTMPS preflight failed: {err2}")
            except Exception as e2:
                self.log.emit(f"[WARN] RTMPS fallback error: {e2}")

            if rc < 0:
                self.log.emit(f"[WARN] RTMP preflight crashed ({rc}); skipping preflight.")
                return True
            self.log.emit(f"[ERROR] RTMP preflight failed: {err}")
            return False
        except Exception as e:
            self.log.emit(f"[ERROR] RTMP preflight exception: {e}")
            return False

    def _terminate_ff_proc(self) -> None:
        """Attempt to gracefully terminate any running ffmpeg process."""
        proc = self.ff_proc
        if not proc or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
    # ---------- control ----------
    def stop(self):
        """Request the current ffmpeg process to terminate."""
        self._stop.set()
        self._terminate_ff_proc()
        self._stop_rtmp_bridge()
        self.log.emit("[INFO] Stop requested — stopping current stream…")

    def skip(self):
        """Abort the current video and advance to the next."""
        self._skip.set()
        self.log.emit("[INFO] Skip requested — advancing to next item…")

    # ---------- yt-dlp helpers ----------
    def get_video_ids(self, url: str) -> List[str]:
        """Return a list of video IDs from a YouTube playlist or single video URL."""
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp not found. Put it next to the app or in PATH.")
        
        input_type = detect_input_type(url)
        
        if input_type == 'youtube_video':
            # Single video - extract video ID directly
            self.log.emit(f"[INFO] Detected single YouTube video: {url}")
            cp = self._run_ytdlp(["--ignore-errors", "--get-id", url])
            if cp.returncode != 0:
                err = (cp.stderr or "").strip()
                if "Could not copy Chrome cookie database" in err:
                    self.log.emit("[WARN] Chrome cookie database locked. Single video will still work without auth.")
                raise RuntimeError(f"yt-dlp error: {err}")
            
            video_id = (cp.stdout or "").strip()
            if video_id:
                self.log.emit(f"[INFO] Video ID: {video_id}")
                return [video_id]
            else:
                raise RuntimeError("Could not extract video ID")
        
        elif input_type == 'youtube_playlist':
            # Playlist - extract all video IDs
            self.log.emit(f"[INFO] Extracting playlist IDs from: {url}")
            cp = self._run_ytdlp(["--ignore-errors", "--flat-playlist", "--get-id", url])
            if cp.returncode != 0:
                err = (cp.stderr or "").strip()
                # Common chromium-family profile locking issue
                if "Could not copy Chrome cookie database" in err:
                    fix = (
                        "Browser cookie database is locked by a running Edge/Chrome instance.\n"
                        "Close all Edge/Chrome windows (including background processes) and try again.\n\n"
                        "Advanced: Launch your browser with --disable-features=LockProfileCookieDatabase to prevent locking.\n"
                        "See: https://github.com/yt-dlp/yt-dlp/issues/7271"
                    )
                    raise RuntimeError(f"yt-dlp cookie error: {fix}")
                raise RuntimeError(f"yt-dlp error: {err}")
            
            ids = [line.strip() for line in (cp.stdout or "").splitlines() if line.strip()]
            self.log.emit(f"[INFO] Found {len(ids)} videos in playlist")
            
            if len(ids) > 10:
                self.log.emit(f"[INFO] First 10 video IDs: {ids[:10]}")
            else:
                self.log.emit(f"[INFO] Video IDs: {ids}")
                
            return ids
        
        else:
            raise RuntimeError(f"Unsupported URL type for video ID extraction: {input_type}")

    def get_metadata(self, video_id: str) -> Tuple[str, Optional[str]]:
        """Fetch the title and upload date for a video."""
        if not self.ytdlp_path:
            return self.get_title_legacy(video_id), None
        url = f"https://www.youtube.com/watch?v={video_id}"
        cp = self._run_ytdlp(["-j", url])
        if cp.returncode != 0 or not cp.stdout:
            if cp.returncode != 0 and cp.stderr and "Could not copy Chrome cookie database" in cp.stderr:
                self.log.emit("[WARN] Cookies locked by browser; close Edge/Chrome and retry (see issue #7271)")
            return self.get_title_legacy(video_id), None
        try:
            data = json.loads(cp.stdout.strip().splitlines()[-1])
        except Exception:
            return self.get_title_legacy(video_id), None
        title = data.get("title") or url
        pretty_date = fmt_yt_date(data.get("upload_date"), data.get("timestamp"), data.get("release_timestamp"))
        return title, pretty_date

    def get_title_legacy(self, video_id: str) -> str:
        """Fallback title retrieval using yt-dlp's --get-title."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        if not self.ytdlp_path:
            return url
        cp = self._run_ytdlp(["--get-title", url])
        return (cp.stdout or "").strip() if cp.returncode == 0 and cp.stdout else url

    def get_twitch_hls_url(self, twitch_url: str) -> str:
        """Extract the HLS manifest URL from a Twitch channel/stream URL using yt-dlp."""
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp not found.")
        
        self.log.emit(f"[INFO] Extracting Twitch HLS URL from: {twitch_url}")
        
        # Use yt-dlp to get the best quality stream URL
        cmd = [self.ytdlp_path, "-f", "best", "-g", twitch_url]
        cp = run_hidden(cmd)
        
        if cp.returncode != 0:
            err = (cp.stderr or "").strip()
            raise RuntimeError(f"Failed to extract Twitch stream URL: {err}")
        
        hls_url = (cp.stdout or "").strip()
        if not hls_url:
            raise RuntimeError("No HLS URL returned from yt-dlp for Twitch stream")
        
        self.log.emit(f"[INFO] Twitch HLS URL obtained: {hls_url[:80]}...")
        return hls_url

    def get_stream_urls(self, video_id: str) -> Tuple[str, Optional[str]]:
        """Return media URLs for a video. Tries HLS first for stability, then falls back to direct URLs."""
        if not self.ytdlp_path:
            raise RuntimeError("yt-dlp not found.")
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Strategy 1: Try HLS manifest (best for 24/7 streaming - no URL expiration)
        try:
            cp = self._run_ytdlp(["-g", "-f", "best", "--hls-prefer-native", url])
            if cp.returncode == 0 and cp.stdout:
                lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
                # If we get an m3u8 URL, use it (single stream with muxed audio/video)
                if lines and any('.m3u8' in line for line in lines):
                    hls_url = next((line for line in lines if '.m3u8' in line), None)
                    if hls_url:
                        self.log.emit(f"[INFO] Using HLS manifest for {video_id} (stable for long streams)")
                        return (hls_url, None)  # HLS contains both video and audio
        except Exception as e:
            self.log.emit(f"[DEBUG] HLS attempt failed: {e}")
        
        # Strategy 2: Try direct URLs with multiple format fallbacks (current method)
        format_strategies = [
            "bv*+ba/best",  # Best video + best audio (separate)
            "best[height<=?1080]",  # Best combined format up to 1080p
            "worst[height>=480]",  # Fallback to worst acceptable quality
            "best"  # Last resort - any available format
        ]
        
        for fmt in format_strategies:
            try:
                cp = self._run_ytdlp(["-g", "-f", fmt, url])
                if cp.returncode == 0 and cp.stdout:
                    lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
                    if lines:
                        # Skip if we got HLS URLs (we want direct URLs here)
                        if not any('.m3u8' in line for line in lines):
                            self.log.emit(f"[INFO] Using direct URLs for {video_id} (format: {fmt})")
                            return (lines[0], None) if len(lines) == 1 else (lines[0], lines[1])
            except Exception:
                continue
                
        # Strategy 3: Final fallback - try without format specification
        try:
            cp = self._run_ytdlp(["-g", url])
            if cp.returncode == 0 and cp.stdout:
                lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
                if lines:
                    return (lines[0], None) if len(lines) == 1 else (lines[0], lines[1])
        except Exception:
            pass
            
        raise RuntimeError(f"No playable formats found for video {video_id}. This may be due to YouTube restrictions or an outdated yt-dlp version.")

    def prefetch_next_video(self, video_id: str) -> None:
        """Prefetch metadata and stream URLs for the next video in a background thread."""
        def _fetch():
            try:
                self.log.emit(f"[PREFETCH] Loading next video: {video_id}")
                title, date = self.get_metadata(video_id)
                vurl, aurl = self.get_stream_urls(video_id)

                # Store in cache
                with self._prefetch_lock:
                    self._prefetch_video_id = video_id
                    self._prefetch_title = title
                    self._prefetch_date = date
                    self._prefetch_vurl = vurl
                    self._prefetch_aurl = aurl
                self.log.emit(f"[PREFETCH] Ready: {title}")
            except Exception as e:
                self.log.emit(f"[PREFETCH] Failed for {video_id}: {e}")
                # Clear cache on error
                with self._prefetch_lock:
                    self._prefetch_video_id = None
                    self._prefetch_title = None
                    self._prefetch_date = None
                    self._prefetch_vurl = None
                    self._prefetch_aurl = None
            finally:
                with self._prefetch_lock:
                    self._prefetch_target_video_id = None
                self._prefetch_ready.set()

        # Start prefetch in background thread
        with self._prefetch_lock:
            if self._prefetch_thread and self._prefetch_thread.is_alive():
                if self._prefetch_target_video_id == video_id:
                    self.log.emit(f"[PREFETCH] Already loading next video: {video_id}")
                else:
                    self.log.emit("[PREFETCH] Previous prefetch still running, skipping...")
                return
            self._prefetch_target_video_id = video_id
            self._prefetch_ready.clear()

        self._prefetch_thread = threading.Thread(target=_fetch, daemon=True)
        self._prefetch_thread.start()

    def _consume_prefetch(self, video_id: str) -> Optional[Tuple[str, Optional[str], str, Optional[str]]]:
        """Return cached prefetch payload for ``video_id`` if available."""
        with self._prefetch_lock:
            if self._prefetch_video_id == video_id and self._prefetch_vurl:
                title = self._prefetch_title or ""
                pretty_date = self._prefetch_date
                vurl = self._prefetch_vurl
                aurl = self._prefetch_aurl
                self._prefetch_video_id = None
                self._prefetch_title = None
                self._prefetch_date = None
                self._prefetch_vurl = None
                self._prefetch_aurl = None
                return (title, pretty_date, vurl, aurl)
            wait_for_prefetch = (
                self._prefetch_target_video_id == video_id
                and self._prefetch_thread is not None
                and self._prefetch_thread.is_alive()
            )

        if wait_for_prefetch:
            self.log.emit(f"[PREFETCH] Waiting for in-flight prefetch: {video_id}")
            self._prefetch_ready.wait(timeout=self._prefetch_wait_timeout())
            with self._prefetch_lock:
                if self._prefetch_video_id == video_id and self._prefetch_vurl:
                    title = self._prefetch_title or ""
                    pretty_date = self._prefetch_date
                    vurl = self._prefetch_vurl
                    aurl = self._prefetch_aurl
                    self._prefetch_video_id = None
                    self._prefetch_title = None
                    self._prefetch_date = None
                    self._prefetch_vurl = None
                    self._prefetch_aurl = None
                    return (title, pretty_date, vurl, aurl)
        return None

    def _post_video_handoff_delay(self) -> None:
        """Brief pause so RTMP servers fully release the prior session."""
        if self._is_youtube_rtmp():
            delay = self.RTMP_SKIP_HANDOFF_DELAY_SEC if self._skip.is_set() else self.RTMP_HANDOFF_DELAY_SEC
        else:
            delay = self.RTMP_FAST_SKIP_HANDOFF_DELAY_SEC if self._skip.is_set() else self.RTMP_FAST_HANDOFF_DELAY_SEC
        if delay <= 0:
            return
        end = time.monotonic() + delay
        while not self._stop.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))

    def _use_persistent_rtmp_bridge(self) -> bool:
        """Keep one RTMP session open for non-YouTube ingest targets."""
        out_url = self.cfg.rtmp_url().lower()
        return out_url.startswith(("rtmp://", "rtmps://")) and (not self._is_youtube_rtmp())

    def _use_rtmp_live_protocol_opts(self, out_url: str) -> bool:
        """Enable RTMP live/tcurl options by default, with runtime fallback disable."""
        return self._rtmp_live_protocol_opts_enabled and out_url.lower().startswith(("rtmp://", "rtmps://"))

    def _disable_rtmp_live_protocol_opts(self, context: str) -> None:
        """Disable RTMP live/tcurl options for this session after a connection failure."""
        if not self._rtmp_live_protocol_opts_enabled:
            return
        self._rtmp_live_protocol_opts_enabled = False
        self.log.emit(f"[WARN] RTMP live protocol options disabled for this session ({context}).")

    def _start_rtmp_bridge(self) -> bool:
        """Start persistent FFmpeg bridge that remuxes mpegts stdin to RTMP."""
        if not self._use_persistent_rtmp_bridge():
            return False
        if self._rtmp_bridge_proc and self._rtmp_bridge_proc.poll() is None and self._rtmp_bridge_write_fd is not None:
            return True
        self._stop_rtmp_bridge()
        if not self.ffmpeg_path:
            self.log.emit("[ERROR] Cannot start RTMP bridge: ffmpeg not found.")
            return False

        out_url = self.cfg.rtmp_url()
        cmd = [
            self.ffmpeg_path or "ffmpeg",
            "-hide_banner", "-loglevel", "warning", "-stats", "-nostdin",
            "-fflags", "+genpts",
            "-f", "mpegts", "-i", "pipe:0",
            "-c", "copy",
        ]
        used_rtmp_live_opts = self._use_rtmp_live_protocol_opts(out_url)
        if used_rtmp_live_opts:
            cmd += ["-rtmp_live", "live", "-rtmp_tcurl", self.cfg.rtmp_base]
        cmd += ["-f", "flv", out_url]

        read_fd, write_fd = os.pipe()
        self.log.emit(f"[CMD] ffmpeg (bridge): {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=read_fd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                close_fds=True,
            )
        except Exception as e:
            try:
                os.close(read_fd)
            except Exception:
                pass
            try:
                os.close(write_fd)
            except Exception:
                pass
            self.log.emit(f"[ERROR] Failed to start RTMP bridge: {e}")
            return False
        finally:
            try:
                os.close(read_fd)
            except Exception:
                pass

        self._rtmp_bridge_proc = proc
        self._rtmp_bridge_write_fd = write_fd

        def _reader(stream):
            for line in iter(stream.readline, ""):
                self._emit_ffmpeg_line(line)

        for stream in (proc.stdout, proc.stderr):
            if stream:
                t = threading.Thread(target=_reader, args=(stream,), daemon=True)
                t.start()

        time.sleep(0.15)
        if proc.poll() is not None:
            rc = proc.poll()
            self.log.emit(f"[ERROR] RTMP bridge exited early with code {rc}")
            self._stop_rtmp_bridge()
            if used_rtmp_live_opts and not self._stop.is_set():
                self._disable_rtmp_live_protocol_opts("bridge connection failed")
                self.log.emit("[INFO] Retrying RTMP bridge without live protocol options.")
                return self._start_rtmp_bridge()
            return False
        self.log.emit("[INFO] RTMP bridge started (persistent ingest session active).")
        return True

    def _stop_rtmp_bridge(self) -> None:
        """Stop persistent RTMP bridge and release pipe fds."""
        wfd = self._rtmp_bridge_write_fd
        self._rtmp_bridge_write_fd = None
        if wfd is not None:
            try:
                os.close(wfd)
            except Exception:
                pass
        proc = self._rtmp_bridge_proc
        self._rtmp_bridge_proc = None
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=1.5)
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass

    def _send_bridge_keepalive(self, duration_sec: float = 1.0) -> None:
        """Feed a short silent slate into the RTMP bridge to avoid ingest idle disconnects."""
        if not self._use_persistent_rtmp_bridge():
            return
        if duration_sec <= 0:
            return
        if not self._start_rtmp_bridge():
            return
        if not self.ffmpeg_path or self._rtmp_bridge_write_fd is None:
            return

        out_fd: Optional[int] = None
        try:
            out_fd = os.dup(self._rtmp_bridge_write_fd)
            duration = max(0.25, float(duration_sec))
            keepalive_cmd = [
                self.ffmpeg_path or "ffmpeg",
                "-hide_banner", "-loglevel", "warning", "-nostdin",
                "-re", "-f", "lavfi", "-i", f"color=black:s=640x360:rate={self.cfg.fps}",
                "-f", "lavfi", "-i", "anullsrc=cl=stereo:r=44100",
                "-t", f"{duration:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
                "-muxdelay", "0", "-muxpreload", "0",
                "-f", "mpegts", "pipe:1",
            ]
            cp = subprocess.run(
                keepalive_cmd,
                stdin=subprocess.DEVNULL,
                stdout=out_fd,
                stderr=subprocess.PIPE,
                text=True,
                timeout=max(3.0, duration + 2.0),
            )
            if cp.returncode != 0:
                self.log.emit(f"[WARN] Keepalive slate failed (rc={cp.returncode})")
        except Exception as e:
            self.log.emit(f"[WARN] Keepalive slate error: {e}")
        finally:
            if out_fd is not None:
                try:
                    os.close(out_fd)
                except Exception:
                    pass

    # ---------- encoder selection ----------
    def _apply_encoder_profile(self, encoder: str) -> bool:
        """Apply encoder-specific ffmpeg settings to the runtime config."""
        if encoder == "libx264":
            self.cfg.encoder = "libx264"
            self.cfg.encoder_name = "CPU x264"
            self.cfg.pix_fmt = "yuv420p"
            self.cfg.extra_venc_flags = ["-preset", "veryfast"]
            return True
        if encoder == "h264_nvenc":
            self.cfg.encoder = "h264_nvenc"
            self.cfg.encoder_name = "NVIDIA NVENC"
            self.cfg.pix_fmt = "yuv420p"
            # Keep NVENC args conservative for broad FFmpeg compatibility.
            self.cfg.extra_venc_flags = []
            return True
        if encoder == "h264_vaapi":
            self.cfg.encoder = "h264_vaapi"
            self.cfg.encoder_name = "VAAPI"
            self.cfg.pix_fmt = "nv12"
            self.cfg.extra_venc_flags = []
            return True
        if encoder == "h264_qsv":
            self.cfg.encoder = "h264_qsv"
            self.cfg.encoder_name = "Intel Quick Sync"
            self.cfg.pix_fmt = "nv12"
            # Keep QSV flags minimal for broad driver/platform compatibility.
            self.cfg.extra_venc_flags = []
            return True
        if encoder == "h264_amf":
            self.cfg.encoder = "h264_amf"
            self.cfg.encoder_name = "AMD AMF"
            self.cfg.pix_fmt = "yuv420p"
            # AMF option names vary across FFmpeg builds; avoid forcing optional knobs.
            self.cfg.extra_venc_flags = []
            return True
        return False

    def _encoder_available(self, encoder: str) -> bool:
        """Return whether an encoder is available on the current ffmpeg runtime."""
        if encoder == "libx264":
            return True
        if not self.ffmpeg_path:
            return False
        return ffprobe_encoder(self.ffmpeg_path, encoder)

    def select_encoder(self):
        """Choose the best available hardware encoder."""
        self._apply_encoder_profile("libx264")
        if not self.ffmpeg_path:
            return

        pref = (self.cfg.encoder_preference or "auto").strip().lower()
        if pref != "auto":
            if self._apply_encoder_profile(pref) and self._encoder_available(pref):
                return
            self.log.emit(f"[WARN] Requested encoder '{pref}' unavailable; falling back to auto selection.")
            # Ensure auto-selection starts from a known-safe CPU baseline.
            self._apply_encoder_profile("libx264")

        sys_name = platform.system().lower()
        if self._encoder_available("h264_nvenc"):
            self._apply_encoder_profile("h264_nvenc")
            return

        if sys_name == "linux":
            # On Intel Linux systems, QSV is usually the best hardware target.
            if self._encoder_available("h264_qsv"):
                self._apply_encoder_profile("h264_qsv")
                return
            if self._encoder_available("h264_vaapi"):
                self._apply_encoder_profile("h264_vaapi")
                return
            if self._encoder_available("h264_amf"):
                self._apply_encoder_profile("h264_amf")
                return
            return

        if self._encoder_available("h264_qsv"):
            self._apply_encoder_profile("h264_qsv")
            return
        if self._encoder_available("h264_amf"):
            self._apply_encoder_profile("h264_amf")
            return

    # ---------- ffmpeg ----------
    def build_ffmpeg_cmd(self, vurl: str, aurl: Optional[str], to_pipe: bool = False) -> List[str]:
        """Build the ffmpeg command for a single video stream."""
        gop = self.cfg.fps * 2
        vf = [f"scale=-2:{self.cfg.height}:flags=bicubic"]
        if self.cfg.overlay_titles:
            title_file = Path(self.cfg.title_file).as_posix().replace(":", r"\:").replace("'", r"\\'")
            fontsize = self.cfg._overlay_fontsize
            fontfile = find_drawtext_fontfile()
            if fontfile:
                esc = fontfile.replace(":", r"\:").replace("'", r"\\'")
                font_arg = f"fontfile='{esc}':"
            else:
                # Let ffmpeg pick a generic family via fontconfig if available
                font_arg = "font='Sans':"
            vf.append(
                f"drawtext=textfile='{title_file}':reload=1:" +
                font_arg +
                f"fontcolor=white:fontsize={fontsize}:box=1:boxcolor=black@0.5:x=10:y=10"
            )
        if self.cfg.encoder == "h264_vaapi":
            vf.append("format=nv12,hwupload")
        else:
            vf.append(f"format={self.cfg.pix_fmt}")  # keep format as a separate filter
        vf_chain = ",".join(vf)

        # Detect if we're using HLS (m3u8) or direct URLs
        is_hls = '.m3u8' in vurl.lower()
        
        # Get buffer settings based on selected mode
        buffer_settings = BUFFER_PRESETS.get(self.cfg.buffer_mode, BUFFER_PRESETS["Medium"])
        
        cmd = [
            self.ffmpeg_path or "ffmpeg",
            "-hide_banner", "-loglevel", "warning", "-stats", "-nostdin",
        ]
        
        # Add buffer-related input options before input URL
        cmd += [
            "-probesize", buffer_settings["probesize"],
            "-analyzeduration", buffer_settings["analyzeduration"],
        ]

        if self.cfg.encoder == "h264_vaapi":
            cmd += ["-vaapi_device", "/dev/dri/renderD128"]
        elif self.cfg.encoder == "h264_qsv" and Path("/dev/dri/renderD128").exists():
            # Help headless Linux/QSV setups bind to the Intel render node.
            cmd += ["-init_hw_device", "qsv=hw:/dev/dri/renderD128"]
        
        # HLS-specific input options for better stability
        if is_hls:
            cmd += [
                "-http_persistent", "0",  # Avoid keepalive reuse issues across changing CDN hosts
                "-reconnect", "1",  # Auto-reconnect on connection loss
                "-reconnect_streamed", "1",  # Reconnect for streamed protocols
                "-reconnect_delay_max", "5",  # Max 5s delay between reconnects
                "-live_start_index", "-3",  # Start from 3 segments before live edge
            ]
        
        cmd += ["-re", "-i", vurl]
        
        if aurl:
            cmd += ["-re", "-i", aurl]

        maps = ["-map", "0:v:0"]
        if aurl:
            maps += ["-map", "1:a:0"]
        else:
            maps += ["-map", "0:a:0?"]  # optional audio if progressive/HLS

        cmd += [
            *maps,
            "-c:v", self.cfg.encoder, *self.cfg.extra_venc_flags,
            "-fflags", "+genpts",
            "-r", str(self.cfg.fps), "-g", str(gop), "-keyint_min", str(gop),
            "-b:v", self.cfg.video_bitrate, "-maxrate", self.cfg.video_bitrate, "-bufsize", self.cfg.bufsize,
            "-vf", vf_chain,
            "-c:a", "aac", "-b:a", self.cfg.audio_bitrate, "-ar", "44100", "-ac", "2",
            # Add buffering for smoother streaming and handling network hiccups
            "-max_delay", buffer_settings["max_delay"],
        ]

        if to_pipe:
            cmd += [
                "-muxdelay", "0",
                "-muxpreload", "0",
                "-f", "mpegts", "pipe:1",
            ]
            return cmd

        cmd += ["-rtmp_buffer", buffer_settings["buffer_size"]]
        # Add RTMP live protocol options by default (runtime fallback can disable them).
        out_url = self.cfg.rtmp_url()
        if self._use_rtmp_live_protocol_opts(out_url):
            cmd += ["-rtmp_live", "live", "-rtmp_tcurl", self.cfg.rtmp_base]
        cmd += [
            "-f", "flv", out_url
        ]
        return cmd

    def run_twitch_stream(self, source_url: str):
        """Stream a Twitch stream continuously using ffmpeg.
        
        Args:
            source_url: Either a direct HLS .m3u8 URL or a Twitch channel URL
        """
        # Extract channel name from URL for overlay
        title = "Twitch Live Stream"
        
        # Try to extract channel name from URL
        if 'twitch.tv/' in source_url.lower():
            # Parse channel name from URL (e.g., https://www.twitch.tv/channelname)
            parts = source_url.rstrip('/').split('/')
            if parts:
                channel_name = parts[-1]
                # Remove any query parameters
                if '?' in channel_name:
                    channel_name = channel_name.split('?')[0]
                if channel_name and channel_name.lower() not in ('twitch.tv', 'www.twitch.tv'):
                    title = f"Twitch • {channel_name}"
        
        # Determine if we need to extract the HLS URL
        input_type = detect_input_type(source_url)
        
        if input_type == 'twitch_stream':
            # Extract HLS URL from Twitch channel using yt-dlp
            try:
                vurl = self.get_twitch_hls_url(source_url)
            except Exception as e:
                raise RuntimeError(f"Failed to get Twitch stream URL: {e}")
        elif input_type == 'direct_hls':
            # Already a direct HLS URL
            vurl = source_url
        else:
            raise RuntimeError(f"Invalid Twitch stream URL type: {input_type}")
        
        aurl = None  # Audio is included in the HLS stream
        
        # Title overlay for Twitch
        if self.cfg.overlay_titles:
            overlay_text = title
            self.cfg._overlay_fontsize = 24
            safe_write_text(Path(self.cfg.title_file), overlay_text)
        
        for attempt in range(2):
            ff_cmd = self.build_ffmpeg_cmd(vurl, aurl)
            used_rtmp_live_opts = "-rtmp_live" in ff_cmd
            self.log.emit(f"[CMD] ffmpeg: {' '.join(ff_cmd)}")
            self._skip.clear()
            self.ff_proc = subprocess.Popen(
                ff_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            def _reader(stream):
                for line in iter(stream.readline, ""):
                    self._emit_ffmpeg_line(line)

            readers = []
            if self.ff_proc.stdout:
                t = threading.Thread(target=_reader, args=(self.ff_proc.stdout,))
                t.daemon = True
                t.start()
                readers.append(t)
            if self.ff_proc.stderr:
                t = threading.Thread(target=_reader, args=(self.ff_proc.stderr,))
                t.daemon = True
                t.start()
                readers.append(t)

            # Wait until ffmpeg finishes or a stop is requested
            while self.ff_proc and self.ff_proc.poll() is None and not self._stop.is_set():
                time.sleep(0.05)

            if self._stop.is_set():
                self._terminate_ff_proc()
            else:
                try:
                    self.ff_proc.wait(timeout=2.0)
                except Exception:
                    self._terminate_ff_proc()

            join_timeout = self._io_join_timeout()
            for t in readers:
                t.join(timeout=join_timeout)

            # Ensure any buffered ffmpeg output is flushed after the process exits
            rc = None
            if self.ff_proc:
                rc = self.ff_proc.poll()
                for stream in (self.ff_proc.stdout, self.ff_proc.stderr):
                    if stream:
                        leftover = stream.read()
                        if leftover:
                            for line in leftover.splitlines():
                                self._emit_ffmpeg_line(line)
                        stream.close()
                self.ff_proc = None

            if rc is None or self._stop.is_set():
                return

            self.log.emit(f"[INFO] ffmpeg exited with code {rc}")
            if rc < 0 and self._maybe_switch_to_system_ffmpeg("ffmpeg crashed during Twitch stream"):
                raise RuntimeError("ffmpeg crashed; switched to system ffmpeg, retrying")
            if rc == 0:
                return
            if used_rtmp_live_opts and attempt == 0:
                self._disable_rtmp_live_protocol_opts("direct RTMP output failed")
                self.log.emit("[INFO] Retrying stream without RTMP live protocol options.")
                continue
            raise RuntimeError(f"ffmpeg exited with code {rc}")

    def run_one_video(self, video_id: str):
        """Stream a single video using ffmpeg."""
        # Check if this video was prefetched (or wait briefly for in-flight prefetch to finish).
        prefetched = self._consume_prefetch(video_id)
        if prefetched:
            self.log.emit(f"[PREFETCH] Using cached data for {video_id}")
            title, pretty_date, vurl, aurl = prefetched
        else:
            # Not prefetched, fetch normally
            try:
                title, pretty_date = self.get_metadata(video_id)
                vurl, aurl = self.get_stream_urls(video_id)
                self.log.emit(f"[INFO] Video URL obtained successfully for {video_id}")
            except Exception as e:
                self.log.emit(f"[ERROR] Failed to get video info for {video_id}: {e}")
                # Try to check if video is available at all
                url = f"https://www.youtube.com/watch?v={video_id}"
                self.log.emit(f"[INFO] Video might be private, deleted, or region-restricted: {url}")
                if self._use_persistent_rtmp_bridge():
                    self.log.emit("[INFO] Injecting short keepalive slate while skipping unavailable video.")
                    self._send_bridge_keepalive(1.2)
                return  # Skip this video and continue
        
        # Title + date overlay (truncate title; keep date intact)
        if self.cfg.overlay_titles:
            suffix = f" • {pretty_date}" if pretty_date else ""
            title_clean = (title or "").replace("\n", " ").strip()

            MAX_LEN = 75  # total length including suffix
            if len(title_clean) + len(suffix) > MAX_LEN:
                avail = max(10, MAX_LEN - len(suffix) - 3)  # leave room for "..."
                title_clean = title_clean[:avail] + "..."

            overlay_text = title_clean + suffix
            self.cfg._overlay_fontsize = 24
            safe_write_text(Path(self.cfg.title_file), overlay_text)
        else:
            # Reset fontsize to default when overlay is disabled
            self.cfg._overlay_fontsize = 24

        use_bridge = self._use_persistent_rtmp_bridge()
        if use_bridge and not self._start_rtmp_bridge():
            raise RuntimeError("Could not start persistent RTMP bridge")
        for attempt in range(2):
            bridge_out_fd: Optional[int] = None
            ff_cmd = self.build_ffmpeg_cmd(vurl, aurl, to_pipe=use_bridge)
            used_rtmp_live_opts = "-rtmp_live" in ff_cmd
            self.log.emit(f"[CMD] ffmpeg: {' '.join(ff_cmd)}")
            self._skip.clear()
            popen_kwargs: Dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stderr": subprocess.PIPE,
                "text": True,
                "bufsize": 1,
            }
            if use_bridge:
                if self._rtmp_bridge_write_fd is None:
                    raise RuntimeError("RTMP bridge write pipe is not available")
                bridge_out_fd = os.dup(self._rtmp_bridge_write_fd)
                popen_kwargs["stdout"] = bridge_out_fd
                popen_kwargs["close_fds"] = True
            else:
                popen_kwargs["stdout"] = subprocess.PIPE
            try:
                self.ff_proc = subprocess.Popen(ff_cmd, **popen_kwargs)
            finally:
                if bridge_out_fd is not None:
                    try:
                        os.close(bridge_out_fd)
                    except Exception:
                        pass

            def _reader(stream):
                for line in iter(stream.readline, ""):
                    self._emit_ffmpeg_line(line)

            readers = []
            if self.ff_proc.stdout and not use_bridge:
                t = threading.Thread(target=_reader, args=(self.ff_proc.stdout,))
                t.daemon = True
                t.start()
                readers.append(t)
            if self.ff_proc.stderr:
                t = threading.Thread(target=_reader, args=(self.ff_proc.stderr,))
                t.daemon = True
                t.start()
                readers.append(t)

            # Wait until ffmpeg finishes or a stop/skip is requested
            while self.ff_proc and self.ff_proc.poll() is None and not (
                self._stop.is_set() or self._skip.is_set()
            ):
                time.sleep(0.05)
            if self._stop.is_set() or self._skip.is_set():
                self._terminate_ff_proc()
            else:
                try:
                    if self.ff_proc:
                        self.ff_proc.wait(timeout=1.0)
                except Exception:
                    pass

            join_timeout = self._io_join_timeout()
            for t in readers:
                t.join(timeout=join_timeout)

            # Ensure any buffered ffmpeg output is flushed after the process exits
            rc = None
            if self.ff_proc:
                rc = self.ff_proc.poll()
                for stream in (self.ff_proc.stdout, self.ff_proc.stderr):
                    if stream:
                        leftover = stream.read()
                        if leftover:
                            for line in leftover.splitlines():
                                self._emit_ffmpeg_line(line)
                        stream.close()
                self.ff_proc = None

            if rc is None or (self._stop.is_set() or self._skip.is_set()):
                break

            self.log.emit(f"[INFO] ffmpeg exited with code {rc}")
            if rc < 0 and self._maybe_switch_to_system_ffmpeg("ffmpeg crashed during YouTube stream"):
                raise RuntimeError("ffmpeg crashed; switched to system ffmpeg, retrying")
            if rc == 0:
                break
            if (not use_bridge) and used_rtmp_live_opts and attempt == 0:
                self._disable_rtmp_live_protocol_opts("direct RTMP output failed")
                self.log.emit("[INFO] Retrying current item without RTMP live protocol options.")
                continue
            raise RuntimeError(f"ffmpeg exited with code {rc}")

        if use_bridge and self._rtmp_bridge_proc and self._rtmp_bridge_proc.poll() is not None:
            raise RuntimeError(f"RTMP bridge exited with code {self._rtmp_bridge_proc.poll()}")

        # Wait briefly for RTMP servers to release the previous session before reconnect.
        if (not self._stop.is_set()) and (not use_bridge):
            self._post_video_handoff_delay()


    # ---------- main loop ----------
    @QtCore.Slot()
    def run(self):
        """Main worker loop that continually streams the playlist."""
        # Try to self-heal dependencies at runtime
        try:
            self.ensure_binaries()
        except Exception:
            pass

        if not self.ffmpeg_path:
            self.log.emit("[ERROR] ffmpeg not found. Put ffmpeg next to the app or in PATH.")
            self.finished.emit()
            return
        if not self.ytdlp_path:
            self.log.emit("[ERROR] yt-dlp not found. Put yt-dlp next to the app or in PATH.")
            self.finished.emit()
            return

        self.select_encoder()
        if self.cfg.yt_auth_enabled:
            auth_browser = self._normalize_auth_browser()
            self.log.emit(f"[INFO] yt-dlp auth: browser cookies ({auth_browser})")
            if self.cfg.yt_auth_profile:
                self.log.emit(f"[INFO] yt-dlp profile override: {self.cfg.yt_auth_profile}")
        else:
            self.log.emit("[INFO] yt-dlp auth: none")

        # Validate RTMP connectivity with a 1s preflight push
        if not self.preflight_rtmp():
            self.status.emit("Stopped")
            self.finished.emit()
            return
        self.status.emit("Starting…")
        self.log.emit(f"[INFO] Encoder: {self.cfg.encoder_name} ({self.cfg.encoder})")
        self.log.emit(f"[INFO] Source:   {self.cfg.playlist_url}")
        self.log.emit(f"[INFO] RTMP:     {self.cfg.rtmp_url()}")
        self.log.emit(
            f"[INFO] Output:   {self.cfg.height}p@{self.cfg.fps}  ~{self.cfg.video_bitrate} video + {self.cfg.audio_bitrate} audio\n"
        )

        # Detect input type
        input_type = detect_input_type(self.cfg.playlist_url)
        try:
            if input_type in ('twitch_stream', 'direct_hls'):
                # Twitch stream or direct HLS - continuous streaming
                stream_type_name = "Twitch stream" if input_type == 'twitch_stream' else "HLS stream"
                self.log.emit(f"[INFO] Detected {stream_type_name} - streaming continuously...")
                while not self._stop.is_set():
                    try:
                        self.run_twitch_stream(self.cfg.playlist_url)
                        if not self._stop.is_set():
                            self.log.emit(f"[WARN] {stream_type_name} ended unexpectedly. Reconnecting in 5s...")
                            for _ in range(5):
                                if self._stop.is_set():
                                    break
                                time.sleep(1)
                    except Exception as e:
                        self.log.emit(f"[ERROR] {stream_type_name} error: {e}")
                        if not self._stop.is_set():
                            self.log.emit("[INFO] Retrying in 10s...")
                            for _ in range(10):
                                if self._stop.is_set():
                                    break
                                time.sleep(1)
            else:
                if self._use_persistent_rtmp_bridge():
                    if not self._start_rtmp_bridge():
                        raise RuntimeError("Failed to initialize persistent RTMP bridge")
                # YouTube playlist or video - loop through videos
                while not self._stop.is_set():
                    try:
                        ids = self.get_video_ids(self.cfg.playlist_url)
                        if not ids:
                            self.log.emit("[WARN] No IDs found; retrying in 30s…")
                            for _ in range(30):
                                if self._stop.is_set():
                                    break
                                time.sleep(1)
                            continue

                        if self.cfg.shuffle:
                            random.shuffle(ids)

                        for idx, vid in enumerate(ids, 1):
                            if self._stop.is_set():
                                break

                            self.log.emit("-" * 46)
                            self.log.emit(f"[INFO] Item #{idx} - https://www.youtube.com/watch?v={vid}")
                            self.log.emit("-" * 46)

                            # Prefetch the next video in the background (if available)
                            if idx < len(ids):
                                next_vid = ids[idx]  # idx is 1-based, so ids[idx] is the next video
                                self.prefetch_next_video(next_vid)

                            try:
                                self.run_one_video(vid)
                            except Exception as e:
                                self.log.emit(f"[WARN] Stream error for video {vid}: {e}")
                                if self._use_persistent_rtmp_bridge():
                                    self._send_bridge_keepalive(0.8)
                                self.log.emit("[INFO] Continuing to next video...")
                                # Add a small destination-aware delay before trying the next video.
                                if not self._stop.is_set():
                                    time.sleep(self._transition_retry_delay())

                            if self._stop.is_set():
                                break

                        if self._stop.is_set():
                            break
                        self.log.emit("\n[INFO] End of playlist. Refreshing IDs and looping…\n")

                    except Exception as e:
                        self.log.emit(f"[WARN] Loop error: {e}. Retrying in 30s…")
                        for _ in range(30):
                            if self._stop.is_set():
                                break
                            time.sleep(1)
        finally:
            self._stop_rtmp_bridge()
            self.status.emit("Stopped")
            self.finished.emit()

class HeadlessRuntime:
    """Run stream worker and web dashboard."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.runtime_state = RuntimeStateStore()
        self.runtime_state.set_meta(mode="headless")
        self.log_fh: Optional[TextIO] = None
        self._log_fh_lock = threading.Lock()
        self._app_log_to_file = False
        self._ffmpeg_log_to_file = False
        self.worker: Optional[StreamWorker] = None
        self.worker_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._binary_lock = threading.Lock()
        self._binary_state: Dict[str, object] = {
            "running": False,
            "last_result": None,
            "last_error": "",
            "started_at": 0.0,
            "finished_at": 0.0,
            "progress_percent": 0,
            "progress_message": "",
        }
        self._app_update_lock = threading.Lock()
        self._app_update_state: Dict[str, object] = {
            "running": False,
            "last_result": None,
            "last_error": "",
            "started_at": 0.0,
            "finished_at": 0.0,
            "downloaded_path": "",
            "progress_message": "",
            "mode": "manual",
            "selected_version": "",
            "selected_channel": "",
            "force_reinstall": False,
        }
        self.web_dashboard = LocalWebDashboard(
            host=self.host,
            port=self.port,
            state_provider=self.runtime_state.snapshot,
            settings_provider=self.get_settings,
            settings_updater=self.update_settings,
            binaries_status_provider=self.get_binaries_status,
            binaries_update_trigger=self.trigger_binaries_update,
            app_update_status_provider=self.get_app_update_status,
            app_update_check_trigger=self.trigger_app_update_check,
            app_update_download_trigger=self.trigger_app_update_download,
            start_cb=self.start_stream,
            stop_cb=self.stop_stream,
            skip_cb=self.skip_stream,
            log_cb=self.log,
        )
        self._sync_file_logging(rotate=True)

    def log(self, text: str) -> None:
        try:
            self.runtime_state.append_log(text)
            is_ffmpeg = RuntimeStateStore._is_ffmpeg_log(text)
            with self._log_fh_lock:
                if not self.log_fh:
                    return
                if is_ffmpeg and not self._ffmpeg_log_to_file:
                    return
                if (not is_ffmpeg) and (not self._app_log_to_file):
                    return
                try:
                    self.log_fh.write(f"{text}\n")
                    self.log_fh.flush()
                except Exception:
                    pass
        except KeyboardInterrupt:
            # Allow Ctrl+C to terminate cleanly without cascading tracebacks.
            return

    def _sync_file_logging(self, rotate: bool = False) -> None:
        cfg = load_config_json()
        app_enabled = _to_bool(cfg.get("log_to_file", False), False)
        ffmpeg_enabled = _to_bool(cfg.get("ffmpeg_log_to_file", False), False)
        enabled = app_enabled or ffmpeg_enabled
        with self._log_fh_lock:
            self._app_log_to_file = app_enabled
            self._ffmpeg_log_to_file = ffmpeg_enabled
            if not enabled:
                if self.log_fh:
                    try:
                        self.log_fh.close()
                    except Exception:
                        pass
                    self.log_fh = None
                return
            if self.log_fh and not rotate:
                return
            if self.log_fh:
                try:
                    self.log_fh.close()
                except Exception:
                    pass
                self.log_fh = None
            self.log_fh, _ = open_rotating_latest_log()

    def get_settings(self) -> Dict[str, object]:
        return web_settings_payload_from_config(load_config_json())

    def update_settings(self, payload: Dict[str, object]) -> Dict[str, object]:
        current = load_config_json()
        merged = apply_web_settings_payload(current, payload)
        save_config_json(merged)
        with self._app_update_lock:
            self._app_update_state["last_result"] = None
            self._app_update_state["last_error"] = ""
            self._app_update_state["finished_at"] = 0.0
        self._sync_file_logging(rotate=False)
        snapshot = web_settings_payload_from_config(merged)
        self.runtime_state.set_meta(
            source=str(snapshot.get("playlist_url", "")),
            resolution=str(snapshot.get("resolution", "")),
            fps=str(snapshot.get("framerate", "")),
        )
        if self.worker_thread and self.worker_thread.is_alive():
            self.log("[INFO] Web settings saved. Changes apply on next start.")
        return snapshot

    def get_binaries_status(self) -> Dict[str, object]:
        with self._binary_lock:
            running = bool(self._binary_state.get("running", False))
            if (not running) and self._binary_state.get("last_result") is None and not self._binary_state.get("last_error"):
                # Lazy initial status for first page load.
                try:
                    self._binary_state["last_result"] = gather_binary_update_status()
                    self._binary_state["finished_at"] = time.time()
                except Exception as e:
                    self._binary_state["last_error"] = str(e)
                    self._binary_state["finished_at"] = time.time()
            return dict(self._binary_state)

    def _run_binaries_update(self) -> None:
        try:
            settings = web_settings_payload_from_config(load_config_json())
            cap = int(settings.get("update_download_cap_mbps", 25) or 25)
            cap = max(1, min(25, cap))
            worker = StreamWorker(StreamConfig(playlist_url="", stream_key="", update_download_cap_mbps=cap))
            worker.log.connect(self.log, QtCore.Qt.ConnectionType.DirectConnection)
            self.log("[INFO] Starting binaries update (yt-dlp, FFmpeg)...")
            def _progress_cb(message: str, percent: int) -> None:
                with self._binary_lock:
                    self._binary_state["progress_message"] = str(message)
                    self._binary_state["progress_percent"] = max(0, min(100, int(percent)))

            _progress_cb("Preparing binary update...", 2)
            worker.ensure_binaries(force=True, progress_cb=_progress_cb)
            result = gather_binary_update_status()
            with self._binary_lock:
                self._binary_state["last_result"] = result
                self._binary_state["last_error"] = ""
                self._binary_state["running"] = False
                self._binary_state["finished_at"] = time.time()
                self._binary_state["progress_percent"] = 100
                self._binary_state["progress_message"] = "Binary update complete."
            self.log("[INFO] Binaries update finished.")
        except Exception as e:
            with self._binary_lock:
                self._binary_state["last_error"] = str(e)
                self._binary_state["running"] = False
                self._binary_state["finished_at"] = time.time()
                self._binary_state["progress_message"] = "Binary update failed."
            self.log(f"[ERROR] Binaries update failed: {e}")

    def trigger_binaries_update(self) -> Dict[str, object]:
        with self._binary_lock:
            if self._binary_state.get("running", False):
                return dict(self._binary_state)
            self._binary_state["running"] = True
            self._binary_state["started_at"] = time.time()
            self._binary_state["last_error"] = ""
            self._binary_state["last_result"] = None
            self._binary_state["progress_percent"] = 0
            self._binary_state["progress_message"] = "Queued binary update..."
        t = threading.Thread(target=self._run_binaries_update, daemon=True)
        t.start()
        return self.get_binaries_status()

    def get_app_update_status(self) -> Dict[str, object]:
        with self._app_update_lock:
            running = bool(self._app_update_state.get("running", False))
            if (not running) and self._app_update_state.get("last_result") is None and not self._app_update_state.get("last_error"):
                try:
                    settings = web_settings_payload_from_config(load_config_json())
                    selected_channel = str(self._app_update_state.get("selected_channel", "") or "").strip().lower()
                    channel = selected_channel or str(settings.get("app_update_channel", "release"))
                    if channel not in WEB_ALLOWED_UPDATE_CHANNELS:
                        channel = "release"
                    selected_version = str(self._app_update_state.get("selected_version", "") or "").strip()
                    self._app_update_state["last_result"] = fetch_latest_app_release_info(channel, selected_version=selected_version or None)
                    self._app_update_state["finished_at"] = time.time()
                except Exception as e:
                    self._app_update_state["last_error"] = str(e)
                    self._app_update_state["finished_at"] = time.time()
            return dict(self._app_update_state)

    @staticmethod
    def _sanitize_selected_version(payload: Optional[Dict[str, object]]) -> str:
        if not isinstance(payload, dict):
            return ""
        selected = str(payload.get("selected_version", "") or "").strip().lstrip("vV")
        if selected and (not _is_supported_update_version(selected)):
            return ""
        return selected

    @staticmethod
    def _sanitize_selected_channel(payload: Optional[Dict[str, object]], fallback: str = "release") -> str:
        channel = fallback
        if isinstance(payload, dict):
            channel = str(payload.get("channel", fallback) or fallback).strip().lower()
        if channel not in WEB_ALLOWED_UPDATE_CHANNELS:
            channel = "release"
        return channel

    @staticmethod
    def _sanitize_force_reinstall(payload: Optional[Dict[str, object]]) -> bool:
        if not isinstance(payload, dict):
            return False
        return _to_bool(payload.get("force_reinstall", False), False)

    def trigger_app_update_check(self, payload: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        selected_version = self._sanitize_selected_version(payload)
        settings = web_settings_payload_from_config(load_config_json())
        channel = self._sanitize_selected_channel(payload, str(settings.get("app_update_channel", "release")))
        with self._app_update_lock:
            if self._app_update_state.get("running", False):
                return dict(self._app_update_state)
            self._app_update_state["last_result"] = None
            self._app_update_state["last_error"] = ""
            self._app_update_state["progress_message"] = "Checking latest app release..."
            self._app_update_state["selected_version"] = selected_version
            self._app_update_state["selected_channel"] = channel
            self._app_update_state["force_reinstall"] = False
        status = self.get_app_update_status()
        with self._app_update_lock:
            if not self._app_update_state.get("running", False):
                self._app_update_state["progress_message"] = ""
            status = dict(self._app_update_state)
        return status

    def _set_app_update_progress(self, message: str) -> None:
        with self._app_update_lock:
            self._app_update_state["progress_message"] = str(message or "")

    def _is_supported_update_asset(self, asset_path: Path) -> bool:
        return _is_release_asset_self_installable(asset_path.name)

    def _spawn_update_helper_and_exit(self, staged_path: Path) -> None:
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Self-install requires packaged binary mode.")
        current_exe = Path(sys.executable).resolve()
        staged_abs = staged_path.resolve()
        updates_dir = staged_abs.parent
        if not staged_abs.exists():
            raise RuntimeError("Downloaded update file is missing.")
        if not self._is_supported_update_asset(staged_abs):
            raise RuntimeError(f"Unsupported update asset for self-install: {staged_abs.name}")
        managed_by_systemd = _running_under_systemd()
        if managed_by_systemd:
            # Replace binary in-place, then let systemd perform the restart.
            os.replace(str(staged_abs), str(current_exe))
            try:
                os.chmod(current_exe, 0o755)
            except Exception:
                pass
            self.log("[INFO] Systemd service detected; handing restart back to systemd.")
        else:
            helper_path = updates_dir / f"apply-update-{int(time.time())}.sh"
            helper = (
                "#!/usr/bin/env sh\n"
                "set -eu\n"
                f"PID='{os.getpid()}'\n"
                f"SRC={shlex.quote(str(staged_abs))}\n"
                f"DST={shlex.quote(str(current_exe))}\n"
                "while kill -0 \"$PID\" 2>/dev/null; do sleep 0.25; done\n"
                "mv -f \"$SRC\" \"$DST\"\n"
                "chmod +x \"$DST\" || true\n"
                "\"$DST\" >/dev/null 2>&1 &\n"
                "rm -f \"$0\"\n"
            )
            helper_path.write_text(helper, encoding="utf-8")
            os.chmod(helper_path, 0o755)
            subprocess.Popen(
                [str(helper_path)],
                cwd=str(current_exe.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        try:
            self.stop_stream()
        except Exception:
            pass
        try:
            self.web_dashboard.stop()
        except Exception:
            pass
        with self._log_fh_lock:
            if self.log_fh:
                try:
                    self.log_fh.flush()
                    self.log_fh.close()
                except Exception:
                    pass
                self.log_fh = None
        os._exit(0)

    def _run_app_update_download(
        self,
        auto_mode: bool = False,
        selected_version: str = "",
        selected_channel: str = "",
        force_reinstall: bool = False,
    ) -> None:
        try:
            settings = web_settings_payload_from_config(load_config_json())
            cap = int(settings.get("update_download_cap_mbps", 25) or 25)
            cap = max(1, min(25, cap))
            channel = str(selected_channel or settings.get("app_update_channel", "release")).strip().lower()
            if channel not in WEB_ALLOWED_UPDATE_CHANNELS:
                channel = "release"
            if auto_mode:
                mode_label = "automatic"
            elif force_reinstall:
                mode_label = "manual (reinstall)"
            else:
                mode_label = "manual"
            self._set_app_update_progress("Checking latest app release...")
            info = fetch_latest_app_release_info(channel, selected_version=selected_version or None)
            if force_reinstall:
                info["should_install"] = True
            if (not bool(info.get("should_install", False))) and (not force_reinstall):
                with self._app_update_lock:
                    self._app_update_state["last_result"] = info
                    self._app_update_state["last_error"] = ""
                    self._app_update_state["running"] = False
                    self._app_update_state["finished_at"] = time.time()
                    self._app_update_state["progress_message"] = "Already on selected channel/version."
                self.log("[INFO] No app install needed for selected channel/version.")
                return
            dl_url = str(info.get("download_url", "")).strip()
            asset_name = str(info.get("asset_name", "")).strip()
            if not dl_url:
                raise RuntimeError("No downloadable release asset found for this platform.")
            if not asset_name:
                asset_name = Path(urlsplit(dl_url).path).name or "app-update"
            base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else _app_dir()
            updates_dir = base_dir / "updates"
            updates_dir.mkdir(parents=True, exist_ok=True)
            dest = updates_dir / asset_name
            self.log(f"[INFO] Starting {mode_label} app update install.")
            self.log(f"[INFO] Downloading app update to {dest} ...")
            self._set_app_update_progress("Downloading app update...")
            _download_url(dl_url, dest, user_agent=f"{APP_NAME}/{APP_VERSION}", max_mbps=cap)
            if dest.exists():
                try:
                    os.chmod(dest, 0o755)
                except Exception:
                    pass
            with self._app_update_lock:
                self._app_update_state["last_result"] = info
                self._app_update_state["last_error"] = ""
                self._app_update_state["running"] = False
                self._app_update_state["finished_at"] = time.time()
                self._app_update_state["downloaded_path"] = dest.as_posix()
                self._app_update_state["progress_message"] = "Installing update and restarting..."
                self._app_update_state["selected_version"] = str(info.get("selected_version", "") or "")
                self._app_update_state["selected_channel"] = str(info.get("channel", channel) or channel)
                self._app_update_state["force_reinstall"] = bool(force_reinstall)
            self.log(f"[INFO] App update downloaded: {dest}")
            if not getattr(sys, "frozen", False):
                with self._app_update_lock:
                    self._app_update_state["progress_message"] = "Downloaded update (source mode: install manually)."
                self.log("[INFO] Source mode detected. Automatic install/restart is only available in packaged builds.")
                return
            self.log("[INFO] Installing app update and restarting...")
            self._spawn_update_helper_and_exit(dest)
        except Exception as e:
            with self._app_update_lock:
                self._app_update_state["last_error"] = str(e)
                self._app_update_state["running"] = False
                self._app_update_state["finished_at"] = time.time()
                self._app_update_state["progress_message"] = "App update failed."
            self.log(f"[ERROR] App update install failed: {e}")

    def trigger_app_update_download(
        self,
        payload: Optional[Dict[str, object]] = None,
        auto_mode: bool = False,
    ) -> Dict[str, object]:
        selected_version = self._sanitize_selected_version(payload)
        settings = web_settings_payload_from_config(load_config_json())
        channel = self._sanitize_selected_channel(payload, str(settings.get("app_update_channel", "release")))
        force_reinstall = self._sanitize_force_reinstall(payload)
        with self._app_update_lock:
            if self._app_update_state.get("running", False):
                return dict(self._app_update_state)
            self._app_update_state["running"] = True
            self._app_update_state["started_at"] = time.time()
            self._app_update_state["last_error"] = ""
            self._app_update_state["last_result"] = None
            self._app_update_state["progress_message"] = "Queued app update..."
            self._app_update_state["mode"] = "automatic" if auto_mode else "manual"
            self._app_update_state["selected_version"] = selected_version
            self._app_update_state["selected_channel"] = channel
            self._app_update_state["force_reinstall"] = bool(force_reinstall)
        t = threading.Thread(
            target=self._run_app_update_download,
            args=(auto_mode, selected_version, channel, force_reinstall),
            daemon=True,
        )
        t.start()
        return self.get_app_update_status()

    def _maybe_startup_auto_app_update(self) -> None:
        settings = web_settings_payload_from_config(load_config_json())
        check_on_start = bool(settings.get("check_updates_startup", True))
        auto_updates = bool(settings.get("auto_app_updates", False))
        channel = str(settings.get("app_update_channel", "release"))
        if not check_on_start:
            self.log("[INFO] Startup update check is disabled.")
            return
        if not auto_updates:
            self.log("[INFO] Automatic app updates are disabled (manual install mode).")
            return
        if not getattr(sys, "frozen", False):
            self.log("[INFO] Automatic app updates skipped in source mode.")
            return
        try:
            info = fetch_latest_app_release_info(channel)
            with self._app_update_lock:
                self._app_update_state["last_result"] = info
                self._app_update_state["last_error"] = ""
                self._app_update_state["finished_at"] = time.time()
            if bool(info.get("should_install", False)):
                self.log("[INFO] Different app version detected at startup; beginning automatic install.")
                self.trigger_app_update_download(auto_mode=True)
            else:
                self.log("[INFO] Startup update check: already on selected channel/version.")
        except Exception as e:
            self.log(f"[WARN] Startup app update check failed: {e}")

    def _on_worker_status(self, status: str) -> None:
        self.runtime_state.set_status(status)
        self.log(f"[STATUS] {status}")

    def _on_worker_finished(self) -> None:
        self.log("[INFO] Worker finished.")
        with self._lock:
            self.runtime_state.set_streaming(False)
            self.runtime_state.set_status("Stopped")
            self.worker = None
            self.worker_thread = None

    def start_stream(self) -> None:
        with self._lock:
            if self.worker_thread and self.worker_thread.is_alive():
                return
            self._sync_file_logging(rotate=True)
            cfg_data = load_config_json()
            cfg = stream_config_from_settings(cfg_data)
            if not cfg.playlist_url or not cfg.rtmp_base or not cfg.stream_key:
                self.log("[WARN] Cannot start: set playlist_url, rtmp_base, and stream_key in config.json.")
                return
            worker = StreamWorker(cfg)
            worker.log.connect(self.log, QtCore.Qt.ConnectionType.DirectConnection)
            worker.status.connect(self._on_worker_status, QtCore.Qt.ConnectionType.DirectConnection)
            worker.finished.connect(self._on_worker_finished, QtCore.Qt.ConnectionType.DirectConnection)
            t = threading.Thread(target=worker.run, daemon=True)
            self.worker = worker
            self.worker_thread = t
            self.runtime_state.set_streaming(True)
            self.runtime_state.set_status("Starting")
            self.runtime_state.set_meta(
                source=cfg.playlist_url,
                resolution=f"{cfg.height}p",
                fps=str(cfg.fps),
            )
            self.log("[INFO] Starting stream (headless)...")
            t.start()

    def stop_stream(self) -> None:
        with self._lock:
            w = self.worker
        if not w:
            return
        try:
            self.runtime_state.set_status("Stopping")
        except KeyboardInterrupt:
            pass
        try:
            self.log("[INFO] Stopping stream...")
        except KeyboardInterrupt:
            pass
        try:
            w.stop()
        except Exception:
            pass

    def skip_stream(self) -> None:
        with self._lock:
            w = self.worker
        if not w:
            return
        self.log("[INFO] Skipping current video...")
        try:
            w.skip()
        except Exception:
            pass

    def run_forever(self) -> int:
        started = self.web_dashboard.start()
        announce_host = self.host.strip() or "127.0.0.1"
        if announce_host in ("0.0.0.0", "::"):
            announce_host = "127.0.0.1"
        dashboard_url = f"http://{announce_host}:{self.port}"
        try:
            print(f"{APP_NAME} headless runtime is running.", flush=True)
            if started:
                print(f"Dashboard URL: {dashboard_url}", flush=True)
            else:
                print(f"Dashboard failed to start on {self.host}:{self.port}", flush=True)
        except BaseException:
            pass
        self.log("[INFO] Headless runtime active.")
        threading.Thread(target=self._maybe_startup_auto_app_update, daemon=True).start()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            try:
                print("Shutdown requested.", flush=True)
            except BaseException:
                pass
        except BaseException as e:
            # Keep shutdown path deterministic in packaged/headless runs.
            try:
                print(f"Runtime error: {e}", flush=True)
            except BaseException:
                pass
        finally:
            try:
                self.stop_stream()
            except BaseException:
                pass
            try:
                self.web_dashboard.stop()
            except BaseException:
                pass
            try:
                with self._log_fh_lock:
                    if self.log_fh:
                        try:
                            self.log_fh.close()
                        except Exception:
                            pass
                        self.log_fh = None
            except BaseException:
                pass
            try:
                restore_terminal_state()
            except BaseException:
                pass
        return 0


# ---------- entry ----------
def main():
    """Entry point for webserver-only runtime."""
    _enabled, web_host, web_port, _web_autostart = read_web_server_settings()
    runtime = HeadlessRuntime(web_host, web_port)
    rc = 0
    try:
        rc = runtime.run_forever()
    except KeyboardInterrupt:
        rc = 0
    finally:
        restore_terminal_state()
    sys.exit(rc)

if __name__ == "__main__":
    main()
