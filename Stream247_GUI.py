# Stream247_GUI.py — GUI YouTube 24/7 streamer
# - Uses yt-dlp (next to the app) for playlist IDs / titles / direct URLs
# - Auto-selects NVENC > QSV > AMF > x264 via safe probe
# - Runs ffmpeg and yt-dlp with hidden windows (no console)
# - Clean Start/Stop (kills ffmpeg reliably; Windows fallback uses taskkill /T /F)
# - Saves config to config.json next to the EXE
# - Overlay shows: "<TITLE> • <Pretty Date>" with title truncation (date preserved)

import os, sys, time, json, random, shutil, subprocess, threading, datetime, webbrowser
import zipfile, tempfile, platform, tarfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple, TYPE_CHECKING
from collections import deque
from pathlib import Path
try:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
    HAS_QT = True
except Exception:
    HAS_QT = False

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

    class _QtDummy:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            return _QtDummy()

        def __getattr__(self, _name):
            return _QtDummy()

    class _QObjectShim:
        def __init__(self, *args, **kwargs):
            pass

    class _QtCoreShim:
        QObject = _QObjectShim
        QThread = _QtDummy
        QTimer = _QtDummy

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

            class AlignmentFlag:
                AlignRight = 0
                AlignVCenter = 0
                AlignTop = 0

            class ScrollBarPolicy:
                ScrollBarAsNeeded = 0
                ScrollBarAlwaysOff = 0

            class WindowType:
                WindowContextHelpButtonHint = 0
                MSWindowsFixedSizeDialogHint = 0

            class WindowModality:
                ApplicationModal = 0

    class _QtGuiShim(_QtDummy):
        QCloseEvent = _QtDummy
        QTextCursor = _QtDummy
        QIcon = _QtDummy

    class _QtWidgetsShim(_QtDummy):
        QWidget = object
        QMessageBox = _QtDummy

    QtCore = _QtCoreShim()  # type: ignore
    QtGui = _QtGuiShim()    # type: ignore
    QtWidgets = _QtWidgetsShim()  # type: ignore
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import re

if TYPE_CHECKING:
    from PySide6.QtCore import QThread as QtThreadT
    from PySide6.QtGui import QCloseEvent as QtCloseEventT
    from PySide6.QtWidgets import QComboBox as QtComboBoxT
    from PySide6.QtWidgets import QProgressDialog as QtProgressDialogT
else:
    QtThreadT = Any
    QtCloseEventT = Any
    QtComboBoxT = Any
    QtProgressDialogT = Any

# General application metadata and platform helpers
APP_NAME = "Stream247"  # Name shown in the GUI and taskbar
APP_VERSION = "2.0"  # Current version
GITHUB_REPO = "TheDoctorTTV/247-steam"  # GitHub repository for updates
IS_WIN = (os.name == "nt")  # True when running on Windows
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0  # Hide console windows
CREATE_NEW_PROCESS_GROUP = 0x00000200 if IS_WIN else 0  # Allow child process killing

# Startup information for subprocesses (only meaningful on Windows)
STARTUPINFO = None
if IS_WIN:
    STARTUPINFO = subprocess.STARTUPINFO()
    # Prevent ffmpeg/yt-dlp windows from flashing on screen
    STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # hide windows

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
    host = str(cfg.get("web_server_host", "127.0.0.1")).strip() or "127.0.0.1"
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
    "auto", "libx264", "h264_nvenc", "h264_qsv", "h264_amf", "h264_vaapi", "h264_videotoolbox"
)
WEB_ALLOWED_BROWSERS = (
    "auto", "firefox", "chrome", "edge", "chromium", "brave", "vivaldi", "opera", "safari"
)


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


def web_settings_payload_from_config(data: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Return normalized stream settings for the web UI/API."""
    cfg = data or {}
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
        cap = int(cfg.get("update_download_cap_mbps", 10))
    except Exception:
        cap = 10
    cap = max(1, min(25, cap))
    return {
        "playlist_url": str(cfg.get("playlist_url", "")).strip(),
        "rtmp_base": str(cfg.get("rtmp_base", "rtmp://a.rtmp.youtube.com/live2")).strip(),
        "stream_key": str(cfg.get("stream_key", "")).strip(),
        "resolution": resolution,
        "framerate": framerate,
        "video_bitrate": str(cfg.get("video_bitrate", "2300k")).strip(),
        "bufsize": str(cfg.get("bufsize", "4600k")).strip(),
        "buffer_mode": buffer_mode,
        "encoder_preference": encoder,
        "overlay_titles": _to_bool(cfg.get("overlay_titles", True), True),
        "shuffle": _to_bool(cfg.get("shuffle", False), False),
        "log_to_file": _to_bool(cfg.get("log_to_file", False), False),
        "rtmp_live": _to_bool(cfg.get("rtmp_live", False), False),
        "remember": _to_bool(cfg.get("remember", True), True),
        "check_updates_startup": _to_bool(cfg.get("check_updates_startup", True), True),
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
    """Return a font file path suitable for ffmpeg drawtext across platforms.

    Tries common system fonts on Windows, macOS, and Linux. Returns None if not found.
    """
    candidates: List[Path] = []
    if IS_WIN:
        windir = os.environ.get("WINDIR", r"C:\\Windows")
        candidates += [
            Path(windir) / "Fonts" / name
            for name in ("segoeui.ttf", "arial.ttf", "calibri.ttf", "tahoma.ttf")
        ]
    else:
        # macOS common font locations
        candidates += [
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/Library/Fonts/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Helvetica.ttc"),
            Path("/System/Library/Fonts/Helvetica.ttc"),
        ]
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
    return find_binary(["ffmpeg", "ffmpeg.exe"])

def find_ytdlp() -> Optional[str]:
    """Locate a yt-dlp binary in PATH or alongside the executable."""
    # First try the Python-installed version (usually more up-to-date)
    candidates = ["yt-dlp", "yt-dlp.exe"]
    
    # Check PATH first
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    
    # Then check local resources
    for c in ["yt-dlp.exe", "yt-dlp"]:
        rp = resource_path(c)
        if Path(rp).exists():
            return rp
    
    return None


class RuntimeStateStore:
    """Thread-safe runtime state used by GUI/headless and web dashboard."""

    def __init__(self, log_limit: int = 500):
        self._lock = threading.Lock()
        self._logs: deque[str] = deque(maxlen=max(50, int(log_limit)))
        self._logs_other: deque[str] = deque(maxlen=max(50, int(log_limit)))
        self._logs_ffmpeg: deque[str] = deque(maxlen=max(50, int(log_limit)))
        self._status = "Idle"
        self._streaming = False
        self._updated_at = time.time()
        self._meta: Dict[str, object] = {}

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
        with self._lock:
            self._logs.append(text)
            if self._is_ffmpeg_log(text):
                self._logs_ffmpeg.append(text)
            else:
                self._logs_other.append(text)
            self._updated_at = time.time()

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
            return {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "streaming": self._streaming,
                "status": self._status,
                "updated_at": self._updated_at,
                "meta": dict(self._meta),
                "logs": list(self._logs),
                "logs_other": list(self._logs_other),
                "logs_ffmpeg": list(self._logs_ffmpeg),
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
        app_update_download_trigger: Callable[[], Dict[str, object]],
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
                        info = dashboard._app_update_download_trigger()
                        self._send_json({"ok": True, "app_update": info})
                    except Exception as e:
                        self._send_json({"ok": False, "error": str(e)}, status=400)
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        return Handler

    def _build_index_html(self) -> str:
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_NAME} Dashboard</title>
  <style>
    :root {{
      --bg: #0b1220;
      --card: #121b2c;
      --text: #e7eefc;
      --muted: #98a9c6;
      --ok: #3db37a;
      --warn: #e7a23c;
      --err: #de5a5a;
      --btn: #2379f5;
      --btn2: #2a3348;
      --border: #22324a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(circle at top, #15243f 0%, var(--bg) 45%);
      color: var(--text);
      font-family: "Segoe UI", Tahoma, Arial, sans-serif;
      padding: 20px;
    }}
    .wrap {{ max-width: 1120px; margin: 0 auto; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
      margin-bottom: 14px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 1.35rem; }}
    h2 {{ margin: 0 0 10px; font-size: 1.05rem; color: #cbd8f4; }}
    .muted {{ color: var(--muted); }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    .tabs {{ display: flex; gap: 8px; margin-top: 10px; }}
    .tab-btn {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 8px 12px;
      color: var(--text);
      background: #16233a;
      cursor: pointer;
      font-weight: 600;
    }}
    .tab-btn.active {{ background: var(--btn); }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .subtabs {{ display: flex; gap: 8px; margin-bottom: 10px; }}
    .subtab-btn {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 7px 10px;
      background: #101a2b;
      color: #c6d6f3;
      cursor: pointer;
      font-size: 12px;
      font-weight: 600;
    }}
    .subtab-btn.active {{ background: #21436f; color: #fff; }}
    .subtab-panel {{ display: none; }}
    .subtab-panel.active {{ display: block; }}
    .grid {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .field {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .field.wide {{ grid-column: 1 / -1; }}
    label {{ font-size: 12px; color: #adc0e1; }}
    input[type="text"], input[type="password"], select {{
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #0f1729;
      color: var(--text);
      padding: 9px 10px;
      width: 100%;
    }}
    .checks {{
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .check {{
      display: flex;
      gap: 8px;
      align-items: center;
      color: #c4d3ee;
      font-size: 13px;
    }}
    button {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 9px 12px;
      color: var(--text);
      background: var(--btn2);
      cursor: pointer;
      font-weight: 600;
    }}
    button.primary {{ background: var(--btn); }}
    button:disabled {{ opacity: 0.45; cursor: default; }}
    .status {{
      font-weight: 700;
      color: var(--warn);
    }}
    .status.on {{ color: var(--ok); }}
    .statusline {{ color: var(--muted); min-height: 1.2em; }}
    .statusline.ok {{ color: var(--ok); }}
    .statusline.err {{ color: var(--err); }}
    .statusline.warn {{ color: var(--warn); }}
    .about-list {{ margin: 0; padding-left: 18px; color: #c8d8f0; line-height: 1.5; }}
    a {{ color: #7fd6ff; }}
    a:hover {{ color: #a8e8ff; }}
    pre {{
      background: #0a101b;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      margin: 0;
      min-height: 250px;
      max-height: 62vh;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      line-height: 1.35;
    }}
    @media (max-width: 900px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .checks {{ grid-template-columns: 1fr; }}
      .tabs {{ flex-wrap: wrap; }}
      .subtabs {{ flex-wrap: wrap; }}
    }}
    code {{ color: #b9c9e8; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{APP_NAME} Web Dashboard</h1>
      <div class="row">
        <div>State: <span id="stateText" class="status">Unknown</span></div>
        <div class="muted" id="metaText"></div>
      </div>
      <div class="tabs">
        <button class="tab-btn active" data-tab="tab-stream">Stream</button>
        <button class="tab-btn" data-tab="tab-console">Console</button>
        <button class="tab-btn" data-tab="tab-about">About</button>
      </div>
    </div>
    <div class="tab-panel active" id="tab-stream">
      <div class="card">
        <div class="row">
          <button class="primary" id="startBtn">Start Stream</button>
          <button id="stopBtn">Stop Stream</button>
          <button id="skipBtn">Skip Video</button>
          <button id="refreshBtn">Refresh</button>
        </div>
      </div>
      <div class="card">
        <h2>Stream Settings</h2>
        <div class="grid">
          <div class="field wide"><label for="playlist_url">Source URL</label><input id="playlist_url" type="text"></div>
          <div class="field"><label for="rtmp_base">Stream URL</label><input id="rtmp_base" type="text"></div>
          <div class="field"><label for="stream_key">Stream Key</label><input id="stream_key" type="password"></div>
          <div class="field"><label for="resolution">Resolution</label><select id="resolution"><option>480p</option><option>720p</option><option>1080p</option><option>1440p</option><option>2160p</option></select></div>
          <div class="field"><label for="framerate">Frame Rate</label><select id="framerate"><option value="30">30</option><option value="60">60</option></select></div>
          <div class="field"><label for="video_bitrate">Video Bitrate</label><input id="video_bitrate" type="text"></div>
          <div class="field"><label for="bufsize">Buffer Size</label><input id="bufsize" type="text"></div>
          <div class="field"><label for="buffer_mode">Stream Buffer</label><select id="buffer_mode"><option>Low</option><option>Medium</option><option>High</option><option>Ultra</option></select></div>
          <div class="field"><label for="encoder_preference">Encoder</label><select id="encoder_preference"><option value="auto">Auto</option><option value="libx264">CPU x264</option><option value="h264_nvenc">NVIDIA NVENC</option><option value="h264_qsv">Intel QSV</option><option value="h264_amf">AMD AMF</option><option value="h264_vaapi">VAAPI</option><option value="h264_videotoolbox">VideoToolbox</option></select></div>
          <div class="field"><label for="update_download_cap_mbps">Update Download Cap (Mbps)</label><select id="update_download_cap_mbps"></select></div>
          <div class="field"><label for="yt_auth_enabled">YouTube Auth</label><select id="yt_auth_enabled"><option value="false">Disabled</option><option value="true">Enabled</option></select></div>
          <div class="field"><label for="yt_auth_browser">YouTube Browser</label><select id="yt_auth_browser"><option value="auto">Auto</option><option value="firefox">Firefox</option><option value="chrome">Chrome</option><option value="edge">Edge</option><option value="chromium">Chromium</option><option value="brave">Brave</option><option value="vivaldi">Vivaldi</option><option value="opera">Opera</option><option value="safari">Safari</option></select></div>
          <div class="field wide"><label for="yt_auth_profile">YouTube Profile Path</label><input id="yt_auth_profile" type="text"></div>
        </div>
        <div class="checks" style="margin-top:12px;">
          <label class="check"><input id="overlay_titles" type="checkbox">Overlay current title</label>
          <label class="check"><input id="shuffle" type="checkbox">Shuffle playlist</label>
          <label class="check"><input id="log_to_file" type="checkbox">Log to file</label>
          <label class="check"><input id="rtmp_live" type="checkbox">RTMP live mode</label>
          <label class="check"><input id="remember" type="checkbox">Remember playlist and key</label>
          <label class="check"><input id="check_updates_startup" type="checkbox">Check updates on startup</label>
        </div>
        <div class="row" style="margin-top:12px;">
          <button class="primary" id="saveSettingsBtn">Save Settings</button>
          <button id="reloadSettingsBtn">Reload Settings</button>
          <div id="settingsStatus" class="statusline"></div>
        </div>
      </div>
    </div>
    <div class="tab-panel" id="tab-console">
      <div class="card">
        <div class="subtabs">
          <button class="subtab-btn active" data-subtab="subtab-other">App / Other Output</button>
          <button class="subtab-btn" data-subtab="subtab-ffmpeg">FFmpeg Output</button>
        </div>
        <div class="subtab-panel active" id="subtab-other"><pre id="otherLogBox">Loading...</pre></div>
        <div class="subtab-panel" id="subtab-ffmpeg"><pre id="ffmpegLogBox">Loading...</pre></div>
      </div>
    </div>
    <div class="tab-panel" id="tab-about">
      <div class="card">
        <h2>About</h2>
        <p><strong>{APP_NAME}</strong> - YouTube 24/7 VOD Streamer<br>Version {APP_VERSION}</p>
        <p><a href="https://github.com/{GITHUB_REPO}" target="_blank" rel="noreferrer">GitHub Repository</a></p>
        <div class="row" style="margin:10px 0 8px;">
          <button id="checkAppUpdateBtn">Check App Update</button>
          <button class="primary" id="downloadAppUpdateBtn">Download App Update</button>
        </div>
        <div id="appUpdateStatus" class="statusline">App update status not loaded.</div>
        <div class="row" style="margin:10px 0 8px;">
          <button id="checkBinariesBtn">Check Binaries</button>
          <button class="primary" id="updateBinariesBtn">Update Binaries (yt-dlp & FFmpeg)</button>
        </div>
        <div id="binariesStatus" class="statusline">Binary status not loaded.</div>
        <ul class="about-list">
          <li>Interface is fully web-based.</li>
          <li>Server bind is configured in <code>config.json</code>.</li>
          <li>Use Update Binaries to refresh yt-dlp and FFmpeg next to the app.</li>
        </ul>
      </div>
    </div>
  </div>
  <script>
    const stateText = document.getElementById("stateText");
    const metaText = document.getElementById("metaText");
    const otherLogBox = document.getElementById("otherLogBox");
    const ffmpegLogBox = document.getElementById("ffmpegLogBox");
    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");
    const skipBtn = document.getElementById("skipBtn");
    const refreshBtn = document.getElementById("refreshBtn");
    const saveSettingsBtn = document.getElementById("saveSettingsBtn");
    const reloadSettingsBtn = document.getElementById("reloadSettingsBtn");
    const settingsStatus = document.getElementById("settingsStatus");
    const checkAppUpdateBtn = document.getElementById("checkAppUpdateBtn");
    const downloadAppUpdateBtn = document.getElementById("downloadAppUpdateBtn");
    const appUpdateStatus = document.getElementById("appUpdateStatus");
    const checkBinariesBtn = document.getElementById("checkBinariesBtn");
    const updateBinariesBtn = document.getElementById("updateBinariesBtn");
    const binariesStatus = document.getElementById("binariesStatus");
    const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
    const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));
    const subtabButtons = Array.from(document.querySelectorAll(".subtab-btn"));
    const subtabPanels = Array.from(document.querySelectorAll(".subtab-panel"));
    let busy = false;
    const fieldIds = ["playlist_url", "rtmp_base", "stream_key", "resolution", "framerate", "video_bitrate", "bufsize", "buffer_mode", "encoder_preference", "yt_auth_enabled", "yt_auth_browser", "yt_auth_profile", "overlay_titles", "shuffle", "log_to_file", "rtmp_live", "remember", "check_updates_startup", "update_download_cap_mbps"];

    for (let i = 1; i <= 25; i++) {{
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = String(i);
      document.getElementById("update_download_cap_mbps").appendChild(o);
    }}

    function setSettingsStatus(text, level) {{
      settingsStatus.textContent = text || "";
      settingsStatus.className = "statusline" + (level ? (" " + level) : "");
    }}

    function setBinariesStatus(text, level) {{
      binariesStatus.textContent = text || "";
      binariesStatus.className = "statusline" + (level ? (" " + level) : "");
    }}

    function setAppUpdateStatus(text, level) {{
      appUpdateStatus.textContent = text || "";
      appUpdateStatus.className = "statusline" + (level ? (" " + level) : "");
    }}

    async function api(path) {{
      await fetch(path, {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: "{{}}" }});
      await refreshState(true);
    }}

    function updateLogBox(el, arr, forceScroll) {{
      const logs = Array.isArray(arr) ? arr : [];
      const text = logs.join("\\n");
      const wasAtBottom = (el.scrollTop + el.clientHeight + 20 >= el.scrollHeight);
      el.textContent = text || "No logs yet.";
      if (forceScroll || wasAtBottom) {{
        el.scrollTop = el.scrollHeight;
      }}
    }}

    async function refreshState(forceScroll) {{
      if (busy) return;
      busy = true;
      try {{
        const res = await fetch("/api/state?ts=" + Date.now(), {{ cache: "no-store" }});
        if (!res.ok) throw new Error("state fetch failed");
        const s = await res.json();
        const streaming = !!s.streaming;
        stateText.textContent = s.status || "Unknown";
        stateText.className = "status" + (streaming ? " on" : "");
        const meta = s.meta || {{}};
        const parts = [];
        if (meta.mode) parts.push("mode: " + meta.mode);
        if (meta.source) parts.push("source: " + meta.source);
        metaText.textContent = parts.join(" | ");
        updateLogBox(otherLogBox, s.logs_other, forceScroll);
        updateLogBox(ffmpegLogBox, s.logs_ffmpeg, forceScroll);
        startBtn.disabled = streaming;
        stopBtn.disabled = !streaming;
        skipBtn.disabled = !streaming;
      }} catch (err) {{
        stateText.textContent = "Dashboard disconnected";
        stateText.className = "status";
      }} finally {{
        busy = false;
      }}
    }}

    function applySettingsToForm(s) {{
      for (const id of fieldIds) {{
        const el = document.getElementById(id);
        if (!el || !(id in s)) continue;
        if (el.type === "checkbox") {{
          el.checked = !!s[id];
        }} else if (id === "yt_auth_enabled") {{
          el.value = s[id] ? "true" : "false";
        }} else {{
          el.value = String(s[id] ?? "");
        }}
      }}
    }}

    function formToPayload() {{
      const out = {{}};
      for (const id of fieldIds) {{
        const el = document.getElementById(id);
        if (!el) continue;
        out[id] = (el.type === "checkbox") ? !!el.checked : el.value;
      }}
      out.framerate = Number(out.framerate || 30);
      out.update_download_cap_mbps = Number(out.update_download_cap_mbps || 10);
      out.yt_auth_enabled = (String(out.yt_auth_enabled).toLowerCase() === "true");
      return out;
    }}

    async function loadSettings() {{
      setSettingsStatus("Loading settings...", "");
      try {{
        const res = await fetch("/api/settings?ts=" + Date.now(), {{ cache: "no-store" }});
        if (!res.ok) throw new Error("load settings failed");
        const payload = await res.json();
        if (!payload.ok || !payload.settings) throw new Error(payload.error || "invalid response");
        applySettingsToForm(payload.settings);
        setSettingsStatus("Settings loaded.", "ok");
      }} catch (err) {{
        setSettingsStatus("Failed to load settings.", "err");
      }}
    }}

    async function saveSettings() {{
      setSettingsStatus("Saving settings...", "");
      try {{
        const res = await fetch("/api/settings", {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(formToPayload()) }});
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "save failed");
        applySettingsToForm(payload.settings || {{}});
        setSettingsStatus("Settings saved.", "ok");
      }} catch (err) {{
        setSettingsStatus("Failed to save settings.", "err");
      }}
    }}

    function formatAppUpdateSummary(a) {{
      if (!a) return "No app update status available.";
      if (a.running) return "App update download is running...";
      if (a.last_error) return "App update error: " + a.last_error;
      const r = a.last_result || null;
      if (!r) return "App update status not available yet.";
      let text = "Current: " + (r.current_version || "unknown") + " | Latest: " + (r.latest_version || "unknown");
      if (r.is_newer) text += " | Update available";
      else text += " | Up to date";
      if (a.downloaded_path) text += " | Downloaded: " + a.downloaded_path;
      return text;
    }}

    async function loadAppUpdateStatus() {{
      try {{
        const res = await fetch("/api/app-update?ts=" + Date.now(), {{ cache: "no-store" }});
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        const info = payload.app_update || {{}};
        let level = "ok";
        if (info.running) level = "warn";
        if (info.last_error) level = "err";
        setAppUpdateStatus(formatAppUpdateSummary(info), level);
        downloadAppUpdateBtn.disabled = !!info.running;
      }} catch (err) {{
        setAppUpdateStatus("Failed to load app update status.", "err");
      }}
    }}

    async function triggerAppUpdateDownload() {{
      setAppUpdateStatus("Starting app update download...", "warn");
      try {{
        const res = await fetch("/api/app-update/download", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: "{{}}"
        }});
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        await loadAppUpdateStatus();
      }} catch (err) {{
        setAppUpdateStatus("Failed to start app update download.", "err");
      }}
    }}

    function formatBinariesSummary(b) {{
      if (!b) return "No binary status available.";
      if (b.running) return "Binary update is running...";
      if (b.last_error) return "Binary update error: " + b.last_error;
      const r = b.last_result || null;
      if (!r) return "Binary status not available yet.";
      const y = r["yt-dlp"] || {{}};
      const f = r["ffmpeg"] || {{}};
      return "yt-dlp: " + (y.current_version || "unknown") + " -> " + (y.latest_version || "unknown") + " (" + (y.status || "unknown") + ") | " +
             "ffmpeg: " + (f.current_version || "unknown") + " -> " + (f.latest_version || "unknown") + " (" + (f.status || "unknown") + ")";
    }}

    async function loadBinariesStatus() {{
      try {{
        const res = await fetch("/api/binaries?ts=" + Date.now(), {{ cache: "no-store" }});
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        const info = payload.binaries || {{}};
        let level = "ok";
        if (info.running) level = "warn";
        if (info.last_error) level = "err";
        setBinariesStatus(formatBinariesSummary(info), level);
        updateBinariesBtn.disabled = !!info.running;
      }} catch (err) {{
        setBinariesStatus("Failed to load binary status.", "err");
      }}
    }}

    async function triggerBinariesUpdate() {{
      setBinariesStatus("Starting binary update...", "warn");
      try {{
        const res = await fetch("/api/binaries/update", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: "{{}}"
        }});
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || "failed");
        await loadBinariesStatus();
      }} catch (err) {{
        setBinariesStatus("Failed to start binary update.", "err");
      }}
    }}

    tabButtons.forEach((btn) => {{
      btn.addEventListener("click", () => {{
        const tabId = btn.dataset.tab;
        tabButtons.forEach((b) => b.classList.toggle("active", b === btn));
        tabPanels.forEach((p) => p.classList.toggle("active", p.id === tabId));
      }});
    }});
    subtabButtons.forEach((btn) => {{
      btn.addEventListener("click", () => {{
        const tabId = btn.dataset.subtab;
        subtabButtons.forEach((b) => b.classList.toggle("active", b === btn));
        subtabPanels.forEach((p) => p.classList.toggle("active", p.id === tabId));
      }});
    }});

    startBtn.addEventListener("click", () => api("/api/start"));
    stopBtn.addEventListener("click", () => api("/api/stop"));
    skipBtn.addEventListener("click", () => api("/api/skip"));
    refreshBtn.addEventListener("click", () => refreshState(true));
    saveSettingsBtn.addEventListener("click", () => saveSettings());
    reloadSettingsBtn.addEventListener("click", () => loadSettings());
    checkAppUpdateBtn.addEventListener("click", () => loadAppUpdateStatus());
    downloadAppUpdateBtn.addEventListener("click", () => triggerAppUpdateDownload());
    checkBinariesBtn.addEventListener("click", () => loadBinariesStatus());
    updateBinariesBtn.addEventListener("click", () => triggerBinariesUpdate());
    loadSettings();
    loadAppUpdateStatus();
    loadBinariesStatus();
    refreshState(true);
    setInterval(() => {{
      refreshState(false);
    }}, 1200);
    setInterval(() => {{
      loadAppUpdateStatus();
      loadBinariesStatus();
    }}, 60000);
  </script>
</body>
</html>
"""

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
    parallel_chunks: int = 4,
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
    chunk_size = 1024 * 64
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
            accepts_ranges = (head_resp.headers.get("Accept-Ranges", "").lower() == "bytes")
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

        with ThreadPoolExecutor(max_workers=max(2, min(8, parallel_chunks))) as ex:
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
    if IS_WIN:
        kwargs["startupinfo"] = STARTUPINFO
        kwargs["creationflags"] = CREATE_NO_WINDOW
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
    if IS_WIN:
        return
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
        null = "NUL" if IS_WIN else "/dev/null"
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
            if not IS_WIN and Path("/dev/dri/renderD128").exists():
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
            return dt_for_format.strftime("%b %#d, %Y") if IS_WIN else dt_for_format.strftime("%b %-d, %Y")
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
            return dt.strftime("%b %#d, %Y") if IS_WIN else dt.strftime("%b %-d, %Y")
        except Exception:
            pass
    return None


# ---------- update checker ----------
class UpdateChecker(QtCore.QObject):
    """Background worker to check for application updates."""
    
    update_checked = QtCore.Signal(dict)  # Emits update info
    error_occurred = QtCore.Signal(str)   # Emits error message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.repo = GITHUB_REPO
        
    @QtCore.Slot()
    def check_for_updates(self):
        """Check GitHub releases for newer versions."""
        try:
            url = f"https://api.github.com/repos/{self.repo}/releases/latest"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', f'{APP_NAME}/{APP_VERSION}')
            
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                
            # Extract version info
            latest_version = data.get('tag_name', '').lstrip('v')
            release_name = data.get('name', '')
            release_notes = data.get('body', '')
            release_url = data.get('html_url', '')
            published_at = data.get('published_at', '')
            
            # Parse published date
            published_date = None
            if published_at:
                try:
                    dt = datetime.datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                    published_date = dt.strftime("%b %d, %Y")
                except Exception:
                    published_date = published_at
            
            # Compare versions (simple string comparison for now)
            current_version = APP_VERSION
            is_newer = self._is_version_newer(latest_version, current_version)
            
            # Find download URL for Windows executable
            download_url = None
            assets = data.get('assets', [])
            for asset in assets:
                name = asset.get('name', '').lower()
                if name.endswith('.exe') and 'stream247' in name:
                    download_url = asset.get('browser_download_url')
                    break
            
            result = {
                'current_version': current_version,
                'latest_version': latest_version,
                'is_newer': is_newer,
                'release_name': release_name,
                'release_notes': release_notes,
                'release_url': release_url,
                'download_url': download_url,
                'published_date': published_date
            }
            
            self.update_checked.emit(result)
            
        except urllib.error.URLError as e:
            self.error_occurred.emit(f"Network error: {e}")
        except json.JSONDecodeError:
            self.error_occurred.emit("Failed to parse update information")
        except Exception as e:
            self.error_occurred.emit(f"Update check failed: {e}")
    
    def _is_version_newer(self, latest: str, current: str) -> bool:
        """Compare version strings to determine if latest is newer than current."""
        try:
            # Simple version comparison (handles x.y.z format)
            latest_parts = [int(x) for x in latest.split('.')]
            current_parts = [int(x) for x in current.split('.')]
            
            # Pad shorter version with zeros
            max_len = max(len(latest_parts), len(current_parts))
            latest_parts.extend([0] * (max_len - len(latest_parts)))
            current_parts.extend([0] * (max_len - len(current_parts)))
            
            return latest_parts > current_parts
        except (ValueError, AttributeError):
            # Fallback to string comparison
            return latest != current and latest > current


def _binary_names_for_platform() -> Tuple[str, str]:
    """Return (yt-dlp-name, ffmpeg-name) for the current platform."""
    if platform.system().lower() == "windows":
        return ("yt-dlp.exe", "ffmpeg.exe")
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


def _is_version_newer(latest: str, current: str) -> bool:
    """Compare semantic-like version strings (x.y.z)."""
    try:
        latest_parts = [int(x) for x in str(latest).split(".")]
        current_parts = [int(x) for x in str(current).split(".")]
        max_len = max(len(latest_parts), len(current_parts))
        latest_parts.extend([0] * (max_len - len(latest_parts)))
        current_parts.extend([0] * (max_len - len(current_parts)))
        return latest_parts > current_parts
    except Exception:
        return str(latest) != str(current) and str(latest) > str(current)


def _pick_release_asset(assets: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    """Pick the best app update asset for current OS (prefers updater scripts)."""
    if not assets:
        return None
    sys_name = platform.system().lower()
    target_script = "build_windows.ps1" if sys_name == "windows" else "build_linux.sh"

    def score(asset: Dict[str, object]) -> Tuple[int, int, int, int, int]:
        name = str(asset.get("name", "")).lower()
        pri_exact_script = 0 if name == target_script else 1
        pri_script_ext = 0 if ((sys_name == "windows" and name.endswith(".ps1")) or (sys_name == "linux" and name.endswith(".sh"))) else 1
        pri_platform = 0 if ((sys_name == "windows" and "win" in name) or (sys_name == "linux" and "linux" in name)) else 1
        pri_server = 0 if "server" in name else 1
        pri_app = 0 if "stream247" in name else 1
        return (pri_exact_script, pri_script_ext, pri_platform, pri_server, pri_app)

    try:
        return sorted(assets, key=score)[0]
    except Exception:
        return assets[0]


def fetch_latest_app_release_info() -> Dict[str, object]:
    """Return latest app release metadata for web updater."""
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
    req.add_header("User-Agent", f"{APP_NAME}/{APP_VERSION}")
    with urllib.request.urlopen(req, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    latest_version = str(data.get("tag_name", "")).lstrip("v")
    release_url = str(data.get("html_url", ""))
    assets = data.get("assets", []) or []
    selected = _pick_release_asset(assets)
    download_url = ""
    asset_name = ""
    target_script = "build_windows.ps1" if platform.system().lower() == "windows" else "build_linux.sh"
    if isinstance(selected, dict):
        download_url = str(selected.get("browser_download_url", ""))
        asset_name = str(selected.get("name", ""))
    # Fallback to script in repo if release does not include script assets.
    if (not download_url) or (not asset_name.lower().endswith((".ps1", ".sh"))):
        download_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{target_script}"
        asset_name = target_script
    return {
        "current_version": APP_VERSION,
        "latest_version": latest_version,
        "is_newer": _is_version_newer(latest_version, APP_VERSION),
        "release_url": release_url,
        "download_url": download_url,
        "asset_name": asset_name,
    }


class BinaryVersionChecker(QtCore.QObject):
    """Background worker for yt-dlp/ffmpeg version checks."""

    checked = QtCore.Signal(dict)
    error_occurred = QtCore.Signal(str)

    @QtCore.Slot()
    def check(self):
        try:
            self.checked.emit(gather_binary_update_status())
        except Exception as e:
            self.error_occurred.emit(f"Binary version check failed: {e}")


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

# Shared presets for GUI and headless config parsing.
RESOLUTION_PRESETS: Dict[str, Tuple[int, str, str]] = {
    "480p": (480, "1000k", "2000k"),
    "720p": (720, "2300k", "4600k"),
    "1080p": (1080, "6000k", "9000k"),
    "1440p": (1440, "9000k", "12000k"),
    "2160p": (2160, "35000k", "35000k"),
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
    video_bitrate: str = "2300k"
    bufsize: str = "4600k"
    audio_bitrate: str = "128k"
    overlay_titles: bool = True
    shuffle: bool = False
    title_file: str = "current_title.txt"
    rtmp_live: bool = False
    buffer_mode: str = "Medium"  # Low, Medium, or High
    yt_auth_enabled: bool = False
    yt_auth_browser: str = "auto"  # auto, chrome, edge, firefox, ...
    yt_auth_profile: str = ""  # Optional custom profile root path
    yt_auth_allow_unauth_fallback: bool = True
    update_download_cap_mbps: int = 10
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
    resolution = str(data.get("resolution", "720p"))
    height, preset_bitrate, preset_bufsize = RESOLUTION_PRESETS.get(
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
        cap_mbps = int(data.get("update_download_cap_mbps", 10) or 10)
    except Exception:
        cap_mbps = 10
    cap_mbps = max(1, min(25, cap_mbps))

    return StreamConfig(
        playlist_url=str(data.get("playlist_url", "")).strip(),
        stream_key=str(data.get("stream_key", "")).strip(),
        rtmp_base=str(data.get("rtmp_base", "rtmp://a.rtmp.youtube.com/live2")).strip(),
        fps=fps,
        height=height,
        video_bitrate=str(data.get("video_bitrate", preset_bitrate)).strip() or preset_bitrate,
        bufsize=str(data.get("bufsize", preset_bufsize)).strip() or preset_bufsize,
        audio_bitrate=str(data.get("audio_bitrate", "128k")).strip() or "128k",
        overlay_titles=bool(data.get("overlay_titles", True)),
        shuffle=bool(data.get("shuffle", False)),
        title_file=str(data.get("title_file", "current_title.txt")).strip() or "current_title.txt",
        rtmp_live=bool(data.get("rtmp_live", False)),
        buffer_mode=str(data.get("buffer_mode", "Medium")).strip() or "Medium",
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
        # Prefetch cache for next video
        self._prefetch_video_id: Optional[str] = None
        self._prefetch_title: Optional[str] = None
        self._prefetch_date: Optional[str] = None
        self._prefetch_vurl: Optional[str] = None
        self._prefetch_aurl: Optional[str] = None
        self._prefetch_thread: Optional[threading.Thread] = None
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
        """Switch to PATH ffmpeg on non-Windows when the bundled binary misbehaves."""
        if IS_WIN:
            return False
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

    def _default_auth_browsers(self) -> List[str]:
        """Return a browser probe order based on OS for --cookies-from-browser."""
        sys_name = platform.system().lower()
        if sys_name == "windows":
            return ["edge", "chrome", "brave", "chromium", "firefox", "vivaldi", "opera"]
        if sys_name == "darwin":
            return ["safari", "chrome", "edge", "brave", "firefox", "chromium", "vivaldi", "opera"]
        # Linux and others
        return ["firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera"]

    def _normalize_auth_browser(self) -> str:
        """Return the configured browser in normalized yt-dlp naming."""
        b = (self.cfg.yt_auth_browser or "auto").strip().lower()
        allowed = {"auto", "chrome", "chromium", "edge", "firefox", "brave", "vivaldi", "opera", "safari"}
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

        if sys_name == "windows":
            ytdlp_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
            ytdlp_regex = r"yt-dlp.*\.exe$"
            ytdlp_local_name = "yt-dlp.exe"
            ffmpeg_local_name = "ffmpeg.exe"
        elif sys_name == "linux":
            ytdlp_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux"
            ytdlp_regex = r"yt-dlp_linux$"
            ytdlp_local_name = "yt-dlp"
            ffmpeg_local_name = "ffmpeg"
        elif sys_name == "darwin":
            ytdlp_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
            ytdlp_regex = r"yt-dlp_macos$"
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
                if sys_name == "windows":
                    if force_ffmpeg:
                        self.log.emit("[INFO] Updating ffmpeg.exe to latest Windows build…")
                    else:
                        self.log.emit("[INFO] ffmpeg.exe not found next to the app — downloading latest Windows build…")
                    _emit_progress("Starting FFmpeg download...", 60)
                    ff_zip_api_url = github_latest_asset_url(
                        "BtbN/FFmpeg-Builds",
                        prefer_substrings=["win64", "lgpl", "shared", "zip"],
                        must_match_regex=r"ffmpeg-.*win64.*zip$",
                        user_agent=f"{APP_NAME}/{APP_VERSION}"
                    )
                    if not ff_zip_api_url:
                        raise RuntimeError("Could not determine latest FFmpeg Windows zip from GitHub API")
                    dest_zip = app_dir / "ffmpeg-latest.zip"
                    _download_url(
                        ff_zip_api_url,
                        dest_zip,
                        user_agent=f"{APP_NAME}/{APP_VERSION}",
                        progress_cb=_mk_dl_progress(60, 30, "ffmpeg bundle"),
                        max_mbps=self.cfg.update_download_cap_mbps,
                    )

                    ffmpeg_bin_path: Optional[Path] = None
                    try:
                        with zipfile.ZipFile(dest_zip, 'r') as zf:
                            cand = [n for n in zf.namelist() if n.lower().endswith('/bin/ffmpeg.exe') or n.lower().endswith('ffmpeg.exe')]
                            if cand:
                                target = local_ffmpeg
                                member_name = cand[0]
                                with zf.open(member_name) as src, open(target, 'wb') as out:
                                    shutil.copyfileobj(src, out)
                                ffmpeg_bin_path = target
                            else:
                                with tempfile.TemporaryDirectory() as tmpd:
                                    zf.extractall(tmpd)
                                    for root, _dirs, files in os.walk(tmpd):
                                        for f in files:
                                            if f.lower() == 'ffmpeg.exe':
                                                ffmpeg_bin_path = Path(root) / f
                                                break
                                        if ffmpeg_bin_path:
                                            break
                                    if not ffmpeg_bin_path:
                                        raise RuntimeError("ffmpeg.exe not found inside archive")
                                    target = local_ffmpeg
                                    shutil.copy2(ffmpeg_bin_path, target)
                                    ffmpeg_bin_path = target
                    finally:
                        try:
                            dest_zip.unlink(missing_ok=True)
                        except Exception:
                            pass
                elif sys_name == "linux":
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
                else:
                    if force_ffmpeg:
                        self.log.emit("[INFO] Updating ffmpeg to latest macOS build…")
                    else:
                        self.log.emit("[INFO] ffmpeg not found next to the app — downloading latest macOS build…")
                    _emit_progress("Starting FFmpeg download...", 60)
                    ffmpeg_url = "https://evermeet.cx/ffmpeg/getrelease/zip"
                    dest_zip = app_dir / "ffmpeg-latest.zip"
                    _download_url(
                        ffmpeg_url,
                        dest_zip,
                        user_agent=f"{APP_NAME}/{APP_VERSION}",
                        progress_cb=_mk_dl_progress(60, 30, "ffmpeg"),
                        max_mbps=self.cfg.update_download_cap_mbps,
                    )
                    ffmpeg_bin_path = None
                    try:
                        with zipfile.ZipFile(dest_zip, 'r') as zf:
                            cand = [n for n in zf.namelist() if n.lower().endswith('/ffmpeg') or n.lower() == 'ffmpeg']
                            if not cand:
                                raise RuntimeError("ffmpeg not found inside archive")
                            target = local_ffmpeg
                            member_name = cand[0]
                            with zf.open(member_name) as src, open(target, 'wb') as out:
                                shutil.copyfileobj(src, out)
                            ffmpeg_bin_path = target
                    finally:
                        try:
                            dest_zip.unlink(missing_ok=True)
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
                    if rc2 < 0 and not IS_WIN:
                        self.log.emit(f"[WARN] RTMPS preflight crashed ({rc2}); skipping preflight.")
                        return True
                    self.log.emit(f"[ERROR] RTMPS preflight failed: {err2}")
            except Exception as e2:
                self.log.emit(f"[WARN] RTMPS fallback error: {e2}")

            if rc < 0 and not IS_WIN:
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
        if IS_WIN and proc.poll() is None:
            for cmd in (
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                ["taskkill", "/IM", "ffmpeg.exe", "/T", "/F"],
            ):
                try:
                    run_hidden(cmd, capture=False)
                except Exception:
                    pass
                if proc.poll() is not None:
                    break

    # ---------- control ----------
    def stop(self):
        """Request the current ffmpeg process to terminate."""
        self._stop.set()
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
                # Common Windows chromium-family locking issue
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
                self._prefetch_video_id = video_id
                self._prefetch_title = title
                self._prefetch_date = date
                self._prefetch_vurl = vurl
                self._prefetch_aurl = aurl
                self.log.emit(f"[PREFETCH] Ready: {title}")
            except Exception as e:
                self.log.emit(f"[PREFETCH] Failed for {video_id}: {e}")
                # Clear cache on error
                self._prefetch_video_id = None
                self._prefetch_title = None
                self._prefetch_date = None
                self._prefetch_vurl = None
                self._prefetch_aurl = None
        
        # Start prefetch in background thread
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self.log.emit("[PREFETCH] Previous prefetch still running, skipping...")
            return
        
        self._prefetch_thread = threading.Thread(target=_fetch, daemon=True)
        self._prefetch_thread.start()

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
        if encoder == "h264_videotoolbox":
            self.cfg.encoder = "h264_videotoolbox"
            self.cfg.encoder_name = "Apple VideoToolbox"
            self.cfg.pix_fmt = "yuv420p"
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

        if sys_name == "darwin":
            if self._encoder_available("h264_videotoolbox"):
                self._apply_encoder_profile("h264_videotoolbox")
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
    def build_ffmpeg_cmd(self, vurl: str, aurl: Optional[str]) -> List[str]:
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
        elif self.cfg.encoder == "h264_qsv" and (not IS_WIN) and Path("/dev/dri/renderD128").exists():
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
            "-rtmp_buffer", buffer_settings["buffer_size"],
        ]

        # Add RTMP-specific protocol options if enabled
        out_url = self.cfg.rtmp_url()
        if out_url.lower().startswith(("rtmp://", "rtmps://")) and self.cfg.rtmp_live:
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
        
        ff_cmd = self.build_ffmpeg_cmd(vurl, aurl)
        self.log.emit(f"[CMD] ffmpeg: {' '.join(ff_cmd)}")
        self._skip.clear()
        self.ff_proc = subprocess.Popen(
            ff_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            startupinfo=STARTUPINFO,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
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

        for t in readers:
            t.join(timeout=0.2)

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
        if rc is not None and not self._stop.is_set():
            self.log.emit(f"[INFO] ffmpeg exited with code {rc}")
            if rc < 0 and self._maybe_switch_to_system_ffmpeg("ffmpeg crashed during Twitch stream"):
                raise RuntimeError("ffmpeg crashed; switched to system ffmpeg, retrying")
            if rc != 0:
                raise RuntimeError(f"ffmpeg exited with code {rc}")

    def run_one_video(self, video_id: str):
        """Stream a single video using ffmpeg."""
        # Check if this video was prefetched
        if self._prefetch_video_id == video_id and self._prefetch_vurl:
            self.log.emit(f"[PREFETCH] Using cached data for {video_id}")
            title = self._prefetch_title
            pretty_date = self._prefetch_date
            vurl = self._prefetch_vurl
            aurl = self._prefetch_aurl
            # Clear prefetch cache after use
            self._prefetch_video_id = None
            self._prefetch_title = None
            self._prefetch_date = None
            self._prefetch_vurl = None
            self._prefetch_aurl = None
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

        ff_cmd = self.build_ffmpeg_cmd(vurl, aurl)
        self.log.emit(f"[CMD] ffmpeg: {' '.join(ff_cmd)}")
        self._skip.clear()
        self.ff_proc = subprocess.Popen(
            ff_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            startupinfo=STARTUPINFO,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
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

        for t in readers:
            t.join(timeout=0.2)

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
        if rc is not None and not (self._stop.is_set() or self._skip.is_set()):
            self.log.emit(f"[INFO] ffmpeg exited with code {rc}")
            if rc < 0 and self._maybe_switch_to_system_ffmpeg("ffmpeg crashed during YouTube stream"):
                raise RuntimeError("ffmpeg crashed; switched to system ffmpeg, retrying")
            if rc != 0:
                raise RuntimeError(f"ffmpeg exited with code {rc}")
        
        # CRITICAL: Wait for RTMP connection to fully close before starting next video
        # Without this delay, the RTMP server rejects the new connection (only 1 connection per key allowed)
        if not self._stop.is_set():
            time.sleep(2)


    # ---------- main loop ----------
    @QtCore.Slot()
    def run(self):
        """Main worker loop that continually streams the playlist."""
        # Try to self-heal dependencies on Windows
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
                            self.log.emit("[INFO] Continuing to next video...")
                            # Add a small delay before trying the next video
                            if not self._stop.is_set():
                                time.sleep(2)

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

        self.status.emit("Stopped")
        self.finished.emit()

# ---------- GUI (modern, readable dark theme) ----------
DARK_QSS = """
* {
  color: #e8edf7;
  font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
  font-size: 10.5pt;
}
QWidget { background: #0c111b; }
QLabel { background: transparent; }
QLineEdit, QComboBox, QTextEdit, QSpinBox, QPlainTextEdit {
  background: #141b28;
  border: 1px solid #263246;
  border-radius: 10px;
  padding: 7px 9px;
  selection-background-color: #2e7de4;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {
  border: 1px solid #3f97ff;
}
QPushButton {
  background: #1f6ad8;
  border: 1px solid #2a7aef;
  border-radius: 11px;
  padding: 8px 14px;
  font-weight: 600;
}
QPushButton:hover { background: #2d79e6; }
QPushButton:pressed { background: #1d5fbe; }
QPushButton:disabled {
  background: #1a2231;
  border: 1px solid #273448;
  color: #7f8da3;
}
QTabWidget::pane {
  border: 1px solid #253146;
  border-radius: 12px;
  background: #0f1624;
  top: -1px;
}
QTabBar::tab {
  background: #101826;
  border: 1px solid #253146;
  border-bottom: none;
  border-top-left-radius: 10px;
  border-top-right-radius: 10px;
  padding: 8px 14px;
  margin-right: 5px;
}
QTabBar::tab:selected {
  background: #182337;
  color: #ffffff;
}
QTabBar::tab:!selected { color: #99a6bc; }
QGroupBox {
  border: 1px solid #263246;
  border-radius: 12px;
  margin-top: 14px;
  padding-top: 10px;
}
QGroupBox::title {
  subcontrol-origin: margin;
  left: 11px;
  padding: 0 6px;
  color: #c6d8f5;
}
QCheckBox::indicator {
  width: 18px;
  height: 18px;
  border: 1px solid #2d3a52;
  border-radius: 5px;
  background: #141b28;
}
QCheckBox::indicator:checked {
  background: #2d81f7;
  border: 1px solid #2d81f7;
  image: none;
}
QCheckBox::indicator:unchecked {
  background: #141b28;
  image: none;
}
QToolTip {
  background: #131b2a;
  color: #e8edf7;
  border: 1px solid #2b3a52;
  padding: 5px;
}
QScrollBar:vertical {
  background: #0f1624;
  width: 12px;
  margin: 0;
}
QScrollBar::handle:vertical {
  background: #2a3a53;
  min-height: 28px;
  border-radius: 6px;
}
QScrollBar::handle:vertical:hover { background: #385276; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

class MainWindow(QtWidgets.QWidget):
    """Main application window housing the GUI and controls."""

    RESOLUTION_PRESETS = RESOLUTION_PRESETS
    
    FRAMERATE_OPTIONS = [30, 60]
    LOG_FLUSH_INTERVAL_MS = 50
    APP_LOG_PREFIXES = (
        "[INFO]", "[WARN]", "[ERROR]", "[STATUS]", "[PREFETCH]", "[CMD]", "[DETAIL]", "[DEBUG]"
    )
    web_start_requested = QtCore.Signal()
    web_stop_requested = QtCore.Signal()
    web_skip_requested = QtCore.Signal()
    web_apply_settings_requested = QtCore.Signal(dict)

    def _configure_combo_popup(self, combo: QtComboBoxT, max_visible: int = 8) -> None:
        """Use a bounded, scrollable combo popup that behaves well across platforms."""
        combo.setMaxVisibleItems(max_visible)
        view = QtWidgets.QListView(combo)
        combo.setView(view)
        view.setUniformItemSizes(True)
        view.setWordWrap(False)
        view.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def __init__(self):
        """Initialise all widgets and connect signals."""
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — YouTube 24/7 VOD Streamer")
        # Provide a sensible minimum size and allow resizing.
        self.setMinimumSize(960, 480)
        self.resize(1200, 700)
        self.worker_thread: Optional[QtThreadT] = None
        self.worker: Optional[StreamWorker] = None
        self.streaming = False
        self.log_fh = None
        self.runtime_state = RuntimeStateStore()
        self.runtime_state.set_meta(mode="gui")
        self.web_dashboard: Optional[LocalWebDashboard] = None
        
        # Update checker components
        self.update_thread: Optional[QtThreadT] = None
        self.update_checker: Optional[UpdateChecker] = None
        self.binary_check_thread: Optional[QtThreadT] = None
        self.binary_checker: Optional[BinaryVersionChecker] = None
        self.binary_update_thread: Optional[QtThreadT] = None
        self.binary_update_worker = None
        self.binary_check_dialog: Optional[QtProgressDialogT] = None
        self.binary_update_dialog: Optional[QtProgressDialogT] = None

        # Inputs
        self.playlist_edit = QtWidgets.QLineEdit("")
        self.playlist_edit.setPlaceholderText("YouTube playlist/video URL or Twitch stream URL…")
        self.playlist_edit.setToolTip("Supports:\n• YouTube playlists (list=...)\n• YouTube videos (watch?v=...)\n• Twitch channels (twitch.tv/username)\n• Direct HLS streams (.m3u8 URLs)")
        self.rtmp_edit = QtWidgets.QLineEdit("rtmp://a.rtmp.youtube.com/live2")
        self.rtmp_edit.setPlaceholderText("RTMP ingest URL (e.g., rtmp://a.rtmp.youtube.com/live2)")
        self.key_edit = QtWidgets.QLineEdit("")
        self.key_edit.setPlaceholderText("Your YouTube stream key…")
        self.key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)

        self.res_combo = QtWidgets.QComboBox()
        self.res_combo.addItems(["480p", "720p", "1080p", "1440p", "2160p"])
        self.res_combo.setCurrentText("720p")
        self._configure_combo_popup(self.res_combo)
        
        self.fps_combo = QtWidgets.QComboBox()
        self.fps_combo.addItems(["30", "60"])
        self.fps_combo.setCurrentText("30")
        self._configure_combo_popup(self.fps_combo)
        
        self.buffer_combo = QtWidgets.QComboBox()
        self.buffer_combo.addItems(["Low", "Medium", "High", "Ultra"])
        self.buffer_combo.setCurrentText("Medium")
        self.buffer_combo.setToolTip("Buffering helps smooth out network hiccups.\nLow: 3s, Medium: 7s (default), High: 12s, Ultra: 25s")
        self._configure_combo_popup(self.buffer_combo)
        self.encoder_combo = QtWidgets.QComboBox()
        self.encoder_combo.addItem("Auto (recommended)", "auto")
        self.encoder_combo.addItem("CPU x264", "libx264")
        self.encoder_combo.addItem("NVIDIA NVENC", "h264_nvenc")
        self.encoder_combo.addItem("Intel Quick Sync", "h264_qsv")
        self.encoder_combo.addItem("AMD AMF", "h264_amf")
        self.encoder_combo.addItem("VAAPI (Linux)", "h264_vaapi")
        self.encoder_combo.addItem("Apple VideoToolbox (macOS)", "h264_videotoolbox")
        self.encoder_combo.setToolTip("Auto picks the best available encoder. Manual mode forces your chosen encoder if supported.")
        self._configure_combo_popup(self.encoder_combo)
        self.update_cap_combo = QtWidgets.QComboBox()
        for mbps in range(1, 26):
            self.update_cap_combo.addItem(f"{mbps} Mbps", mbps)
        self.update_cap_combo.setCurrentIndex(9)  # 10 Mbps default
        self.update_cap_combo.setToolTip("Max download rate for updater (combined across update download threads).")
        self._configure_combo_popup(self.update_cap_combo)
        
        self.bitrate_edit = QtWidgets.QLineEdit("2300k")
        self.bufsize_edit = QtWidgets.QLineEdit("4600k")

        self.overlay_chk = QtWidgets.QCheckBox("Overlay current VOD title")
        self.overlay_chk.setChecked(True)
        self.shuffle_chk = QtWidgets.QCheckBox("Shuffle playlist order")
        self.shuffle_chk.setToolTip("Only applies to YouTube playlists")
        self.logfile_chk = QtWidgets.QCheckBox("Log to file")
        self.remember_chk = QtWidgets.QCheckBox("Save playlist and key")
        self.remember_chk.setChecked(True)
        self.check_updates_startup_chk = QtWidgets.QCheckBox("Check for updates on startup")
        self.check_updates_startup_chk.setChecked(True)

        self.console_ffmpeg = QtWidgets.QPlainTextEdit()
        self.console_ffmpeg.setReadOnly(True)
        self.console_ffmpeg.setVisible(True)
        self.console_ffmpeg.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.console_ffmpeg.setMaximumBlockCount(5000)
        self.console_other = QtWidgets.QPlainTextEdit()
        self.console_other.setReadOnly(True)
        self.console_other.setVisible(True)
        self.console_other.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.console_other.setMaximumBlockCount(5000)
        self._pending_logs: deque[Tuple[bool, str]] = deque()
        self._log_flush_timer = QtCore.QTimer(self)
        self._log_flush_timer.setInterval(self.LOG_FLUSH_INTERVAL_MS)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)

        self.start_btn = QtWidgets.QPushButton("Start Stream")
        self.stop_btn = QtWidgets.QPushButton("Stop Stream")
        self.stop_btn.setEnabled(False)
        self.skip_btn = QtWidgets.QPushButton("Skip Video")
        self.skip_btn.setEnabled(False)

        # --- Tabs ---
        tabs = QtWidgets.QTabWidget()
        tabs.setDocumentMode(True)

        # Stream Tab (scrollable for small window sizes)
        stream_tab = QtWidgets.QScrollArea()
        stream_tab.setWidgetResizable(True)
        stream_tab.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        stream_content = QtWidgets.QWidget()
        stream_layout = QtWidgets.QVBoxLayout(stream_content)
        stream_layout.setContentsMargins(16, 16, 16, 16)
        stream_layout.setSpacing(12)

        stream_header = QtWidgets.QLabel("Streaming Session")
        stream_header.setStyleSheet("font-size: 13pt; font-weight: 700; color: #d7e7ff;")
        stream_layout.addWidget(stream_header)

        stream_group = QtWidgets.QGroupBox("Connection")
        stream_group_layout = QtWidgets.QFormLayout(stream_group)
        stream_group_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        stream_group_layout.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        stream_group_layout.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        stream_group_layout.setHorizontalSpacing(14)
        stream_group_layout.setVerticalSpacing(10)
        stream_group_layout.addRow("Source URL", self.playlist_edit)
        stream_group_layout.addRow("Stream URL", self.rtmp_edit)
        stream_group_layout.addRow("Stream Key", self.key_edit)
        stream_layout.addWidget(stream_group)

        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(10)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.skip_btn)
        btns.addStretch(1)
        stream_layout.addLayout(btns)

        # Console Tab
        console_tab = QtWidgets.QWidget()
        console_layout = QtWidgets.QVBoxLayout(console_tab)
        console_layout.setContentsMargins(16, 16, 16, 16)
        console_layout.setSpacing(10)
        self.console_tabs = QtWidgets.QTabWidget()
        self.console_tabs.addTab(self.console_other, "App / Other Output")
        self.console_tabs.addTab(self.console_ffmpeg, "FFmpeg Output")
        console_layout.addWidget(self.console_tabs)
        tabs.addTab(console_tab, "Console")

        # Stream Settings Section (moved from the old Settings tab)
        stream_settings_group = QtWidgets.QGroupBox("Stream Settings")
        settings_layout = QtWidgets.QVBoxLayout(stream_settings_group)
        settings_layout.setSpacing(12)

        quality_group = QtWidgets.QGroupBox("Encoding and Quality")
        quality_layout = QtWidgets.QGridLayout(quality_group)
        quality_layout.setHorizontalSpacing(14)
        quality_layout.setVerticalSpacing(10)
        quality_layout.addWidget(QtWidgets.QLabel("Resolution"), 0, 0)
        quality_layout.addWidget(self.res_combo, 0, 1)
        quality_layout.addWidget(QtWidgets.QLabel("Frame Rate"), 0, 2)
        quality_layout.addWidget(self.fps_combo, 0, 3)
        quality_layout.addWidget(QtWidgets.QLabel("Video Bitrate"), 1, 0)
        quality_layout.addWidget(self.bitrate_edit, 1, 1)
        quality_layout.addWidget(QtWidgets.QLabel("Buffer Size"), 1, 2)
        quality_layout.addWidget(self.bufsize_edit, 1, 3)
        quality_layout.addWidget(QtWidgets.QLabel("Stream Buffer"), 2, 0)
        quality_layout.addWidget(self.buffer_combo, 2, 1)
        quality_layout.addWidget(QtWidgets.QLabel("Update Download Cap"), 2, 2)
        quality_layout.addWidget(self.update_cap_combo, 2, 3)
        quality_layout.addWidget(QtWidgets.QLabel("Encoder"), 3, 0)
        quality_layout.addWidget(self.encoder_combo, 3, 1)
        settings_layout.addWidget(quality_group)

        behavior_group = QtWidgets.QGroupBox("Behavior")
        behavior_layout = QtWidgets.QHBoxLayout(behavior_group)
        behavior_layout.setSpacing(12)
        behavior_layout.addWidget(self.overlay_chk)
        behavior_layout.addWidget(self.shuffle_chk)
        behavior_layout.addWidget(self.logfile_chk)
        self.rtmp_live_chk = QtWidgets.QCheckBox("RTMP live mode (Owncast)")
        self.rtmp_live_chk.setToolTip("Adds -rtmp_live live and tcurl for better compatibility with Owncast and some servers")
        behavior_layout.addWidget(self.rtmp_live_chk)
        behavior_layout.addStretch(1)

        auth_group = QtWidgets.QGroupBox("YouTube Auth (Optional)")
        auth_layout = QtWidgets.QGridLayout(auth_group)
        self.yt_auth_chk = QtWidgets.QCheckBox("Use browser cookies for age-restricted/private-access videos")
        self.yt_auth_chk.setToolTip(
            "Uses yt-dlp --cookies-from-browser.\n"
            "Useful for age-restricted, members-only, or region/account-gated videos."
        )
        auth_layout.addWidget(self.yt_auth_chk, 0, 0, 1, 4)

        auth_layout.addWidget(QtWidgets.QLabel("Browser"), 1, 0)
        self.yt_browser_combo = QtWidgets.QComboBox()
        self.yt_browser_combo.addItem("Auto (try common browsers)", "auto")
        self.yt_browser_combo.addItem("Firefox", "firefox")
        self.yt_browser_combo.addItem("Chrome", "chrome")
        self.yt_browser_combo.addItem("Edge", "edge")
        self.yt_browser_combo.addItem("Chromium", "chromium")
        self.yt_browser_combo.addItem("Brave", "brave")
        self.yt_browser_combo.addItem("Vivaldi", "vivaldi")
        self.yt_browser_combo.addItem("Opera", "opera")
        self.yt_browser_combo.addItem("Safari", "safari")
        self._configure_combo_popup(self.yt_browser_combo)
        auth_layout.addWidget(self.yt_browser_combo, 1, 1)

        auth_layout.addWidget(QtWidgets.QLabel("Profile Path"), 1, 2)
        self.yt_profile_edit = QtWidgets.QLineEdit("")
        self.yt_profile_edit.setPlaceholderText("Optional profile root path (supports Flatpak/Snap layouts)")
        self.yt_profile_edit.setToolTip(
            "Examples:\n"
            "Linux Flatpak Firefox: ~/.var/app/org.mozilla.firefox/.mozilla/firefox\n"
            "Linux Flatpak Chromium: ~/.var/app/org.chromium.Chromium/config/chromium\n"
            "Leave blank to let yt-dlp auto-detect."
        )
        auth_layout.addWidget(self.yt_profile_edit, 1, 3)

        self.advanced_toggle = QtWidgets.QCheckBox("Show advanced settings")
        self.advanced_toggle.setChecked(False)
        settings_layout.addWidget(self.advanced_toggle)

        self.advanced_section = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QVBoxLayout(self.advanced_section)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(12)
        advanced_layout.addWidget(behavior_group)
        advanced_layout.addWidget(auth_group)

        bottom_opts = QtWidgets.QHBoxLayout()
        bottom_opts.addWidget(self.remember_chk)
        bottom_opts.addStretch(1)
        advanced_layout.addLayout(bottom_opts)

        settings_layout.addWidget(self.advanced_section)
        self._set_advanced_visible(self.advanced_toggle.isChecked())

        stream_layout.addWidget(stream_settings_group)
        stream_layout.addStretch(1)
        stream_tab.setWidget(stream_content)
        tabs.insertTab(0, stream_tab, "Stream")

        # About Tab
        about_tab = QtWidgets.QWidget()
        about_layout = QtWidgets.QVBoxLayout(about_tab)
        about_layout.setContentsMargins(16, 16, 16, 16)
        about_layout.setSpacing(12)
        
        # Version info
        version_layout = QtWidgets.QHBoxLayout()
        about_text = QtWidgets.QLabel(f"<b>{APP_NAME}</b> - YouTube 24/7 VOD Streamer<br>Version {APP_VERSION}")
        about_text.setWordWrap(True)
        version_layout.addWidget(about_text)
        version_layout.addStretch(1)
        
        # Update checker section
        update_group = QtWidgets.QGroupBox("Updates")
        update_group_layout = QtWidgets.QVBoxLayout(update_group)
        
        # Update status and buttons
        update_controls_layout = QtWidgets.QHBoxLayout()
        self.check_update_btn = QtWidgets.QPushButton("Check for Updates")
        self.check_update_btn.clicked.connect(self.check_for_updates)
        update_controls_layout.addWidget(self.check_update_btn)
        self.force_update_btn = QtWidgets.QPushButton("Force Update Binaries (yt-dlp & FFmpeg)")
        self.force_update_btn.setToolTip("Re-download latest yt-dlp and FFmpeg next to the app")
        self.force_update_btn.clicked.connect(self.on_force_update_binaries)
        update_controls_layout.addWidget(self.force_update_btn)
        update_controls_layout.addStretch(1)
        
        # Update status label
        self.update_status_label = QtWidgets.QLabel("Click 'Check for Updates' to check for new versions")
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setStyleSheet("color: #888; font-style: italic;")
        
        # Check on startup toggle
        startup_check_layout = QtWidgets.QHBoxLayout()
        startup_check_layout.addWidget(self.check_updates_startup_chk)
        startup_check_layout.addStretch(1)
        
        update_group_layout.addLayout(update_controls_layout)
        update_group_layout.addWidget(self.update_status_label)
        update_group_layout.addLayout(startup_check_layout)
        
        # Credits
        credits_text = QtWidgets.QLabel("Open-source tool created by TheDoctorTTV<br>"
                                       f"<a href='https://github.com/{GITHUB_REPO}' style='color: #5DADE2;'>GitHub Repository</a>")
        credits_text.setWordWrap(True)
        credits_text.setOpenExternalLinks(True)
        
        about_layout.addLayout(version_layout)
        about_layout.addWidget(update_group)
        about_layout.addStretch(1)
        about_layout.addWidget(credits_text)
        tabs.addTab(about_tab, "About")
        tabs.setCurrentIndex(0)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addWidget(tabs)

        # Signals
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)
        self.skip_btn.clicked.connect(self.on_skip)
        self.res_combo.currentIndexChanged.connect(self.on_quality_change)
        self.fps_combo.currentIndexChanged.connect(self.on_quality_change)
        self.web_start_requested.connect(self.on_start)
        self.web_stop_requested.connect(self.on_stop)
        self.web_skip_requested.connect(self.on_skip)
        self.web_apply_settings_requested.connect(self._apply_web_settings_from_web)

        # restore persisted settings before wiring save handlers
        self.on_quality_change()
        self.load_settings()

        self._update_auth_ui_state()

        # persist as you tweak
        self.remember_chk.toggled.connect(lambda _: self.save_settings())
        self.overlay_chk.toggled.connect(lambda _: self.save_settings())
        self.shuffle_chk.toggled.connect(lambda _: self.save_settings())
        self.logfile_chk.toggled.connect(lambda _: self.save_settings())
        if hasattr(self, "rtmp_live_chk"):
            self.rtmp_live_chk.toggled.connect(lambda _: self.save_settings())
        self.check_updates_startup_chk.toggled.connect(lambda _: self.save_settings())
        self.res_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.fps_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.buffer_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.encoder_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.update_cap_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.bitrate_edit.textChanged.connect(lambda _: self.save_settings())
        self.bufsize_edit.textChanged.connect(lambda _: self.save_settings())
        self.rtmp_edit.textChanged.connect(lambda _: self.save_settings())
        self.yt_auth_chk.toggled.connect(self._update_auth_ui_state)
        self.yt_auth_chk.toggled.connect(lambda _: self.save_settings())
        self.yt_browser_combo.currentIndexChanged.connect(lambda _: self.save_settings())
        self.yt_profile_edit.textChanged.connect(lambda _: self.save_settings())
        self.advanced_toggle.toggled.connect(self._set_advanced_visible)
        self.advanced_toggle.toggled.connect(lambda _: self.save_settings())
        self._init_web_dashboard_from_config()
        
        # Check for updates on startup if enabled
        if self.check_updates_startup_chk.isChecked():
            QtCore.QTimer.singleShot(2000, self.check_for_updates_silent)

    def _update_auth_ui_state(self):
        enabled = self.yt_auth_chk.isChecked()
        self.yt_browser_combo.setEnabled(enabled)
        self.yt_profile_edit.setEnabled(enabled)

    def _set_advanced_visible(self, visible: bool):
        self.advanced_section.setVisible(bool(visible))

    def _web_state_snapshot(self) -> Dict[str, object]:
        return self.runtime_state.snapshot()

    def _web_get_settings(self) -> Dict[str, object]:
        return {
            "playlist_url": self.playlist_edit.text().strip(),
            "rtmp_base": self.rtmp_edit.text().strip(),
            "stream_key": self.key_edit.text().strip(),
            "resolution": self.res_combo.currentText(),
            "framerate": int(self.fps_combo.currentText()),
            "video_bitrate": self.bitrate_edit.text().strip(),
            "bufsize": self.bufsize_edit.text().strip(),
            "buffer_mode": self.buffer_combo.currentText(),
            "encoder_preference": str(self.encoder_combo.currentData() or "auto"),
            "overlay_titles": self.overlay_chk.isChecked(),
            "shuffle": self.shuffle_chk.isChecked(),
            "log_to_file": self.logfile_chk.isChecked(),
            "rtmp_live": (self.rtmp_live_chk.isChecked() if hasattr(self, "rtmp_live_chk") else False),
            "remember": self.remember_chk.isChecked(),
            "check_updates_startup": self.check_updates_startup_chk.isChecked(),
            "yt_auth_enabled": self.yt_auth_chk.isChecked(),
            "yt_auth_browser": str(self.yt_browser_combo.currentData() or "auto"),
            "yt_auth_profile": self.yt_profile_edit.text().strip(),
            "update_download_cap_mbps": int(self.update_cap_combo.currentData() or 10),
        }

    def _web_set_settings(self, payload: Dict[str, object]) -> Dict[str, object]:
        current = load_config_json()
        merged = apply_web_settings_payload(current, payload)
        save_config_json(merged)
        self.web_apply_settings_requested.emit(dict(merged))
        return web_settings_payload_from_config(merged)

    def _web_get_binaries(self) -> Dict[str, object]:
        info = gather_binary_update_status()
        return {
            "running": False,
            "last_result": info,
            "last_error": "",
            "started_at": 0.0,
            "finished_at": time.time(),
        }

    def _web_trigger_binaries_update(self) -> Dict[str, object]:
        # GUI runtime keeps native updater flow; web call triggers the same action.
        try:
            self.on_force_update_binaries()
        except Exception:
            pass
        return self._web_get_binaries()

    def _web_get_app_update(self) -> Dict[str, object]:
        try:
            info = fetch_latest_app_release_info()
            return {
                "running": False,
                "last_result": info,
                "last_error": "",
                "started_at": 0.0,
                "finished_at": time.time(),
                "downloaded_path": "",
            }
        except Exception as e:
            return {
                "running": False,
                "last_result": None,
                "last_error": str(e),
                "started_at": 0.0,
                "finished_at": time.time(),
                "downloaded_path": "",
            }

    def _web_trigger_app_update_download(self) -> Dict[str, object]:
        # GUI runtime path: we only report latest release; manual desktop updater flow remains in GUI.
        return self._web_get_app_update()

    @QtCore.Slot(dict)
    def _apply_web_settings_from_web(self, cfg: Dict[str, object]) -> None:
        remember = _to_bool(cfg.get("remember", True), True)
        self.remember_chk.setChecked(remember)
        self.playlist_edit.setText(str(cfg.get("playlist_url", "")).strip())
        self.rtmp_edit.setText(str(cfg.get("rtmp_base", "rtmp://a.rtmp.youtube.com/live2")).strip())
        self.key_edit.setText(str(cfg.get("stream_key", "")).strip())

        self.overlay_chk.setChecked(_to_bool(cfg.get("overlay_titles", True), True))
        self.shuffle_chk.setChecked(_to_bool(cfg.get("shuffle", False), False))
        self.logfile_chk.setChecked(_to_bool(cfg.get("log_to_file", False), False))
        self.check_updates_startup_chk.setChecked(_to_bool(cfg.get("check_updates_startup", True), True))
        self.yt_auth_chk.setChecked(_to_bool(cfg.get("yt_auth_enabled", False), False))
        self.yt_profile_edit.setText(str(cfg.get("yt_auth_profile", "")).strip())
        self._update_auth_ui_state()
        if hasattr(self, "rtmp_live_chk"):
            self.rtmp_live_chk.setChecked(_to_bool(cfg.get("rtmp_live", False), False))

        res = str(cfg.get("resolution", "720p"))
        idx = self.res_combo.findText(res)
        if idx >= 0:
            self.res_combo.setCurrentIndex(idx)
        fps = str(cfg.get("framerate", 30))
        idx = self.fps_combo.findText(fps)
        if idx >= 0:
            self.fps_combo.setCurrentIndex(idx)
        buf_mode = str(cfg.get("buffer_mode", "Medium"))
        idx = self.buffer_combo.findText(buf_mode)
        if idx >= 0:
            self.buffer_combo.setCurrentIndex(idx)
        enc = str(cfg.get("encoder_preference", "auto")).strip().lower()
        idx = self.encoder_combo.findData(enc)
        if idx < 0:
            idx = self.encoder_combo.findData("auto")
        if idx >= 0:
            self.encoder_combo.setCurrentIndex(idx)
        br = str(cfg.get("yt_auth_browser", "auto")).strip().lower()
        idx = self.yt_browser_combo.findData(br)
        if idx < 0:
            idx = self.yt_browser_combo.findData("auto")
        if idx >= 0:
            self.yt_browser_combo.setCurrentIndex(idx)
        try:
            cap = int(cfg.get("update_download_cap_mbps", 10))
        except Exception:
            cap = 10
        cap = max(1, min(25, cap))
        idx = self.update_cap_combo.findData(cap)
        if idx >= 0:
            self.update_cap_combo.setCurrentIndex(idx)
        self.bitrate_edit.setText(str(cfg.get("video_bitrate", "")).strip())
        self.bufsize_edit.setText(str(cfg.get("bufsize", "")).strip())

    def _init_web_dashboard_from_config(self) -> None:
        enabled, host, port, _autostart = read_web_server_settings()
        if not enabled:
            return
        self.web_dashboard = LocalWebDashboard(
            host=host,
            port=port,
            state_provider=self._web_state_snapshot,
            settings_provider=self._web_get_settings,
            settings_updater=self._web_set_settings,
            binaries_status_provider=self._web_get_binaries,
            binaries_update_trigger=self._web_trigger_binaries_update,
            app_update_status_provider=self._web_get_app_update,
            app_update_download_trigger=self._web_trigger_app_update_download,
            start_cb=lambda: self.web_start_requested.emit(),
            stop_cb=lambda: self.web_stop_requested.emit(),
            skip_cb=lambda: self.web_skip_requested.emit(),
            log_cb=self.append_log,
        )
        self.web_dashboard.start()

    def _prepare_fixed_update_dialog(self, dialog: QtProgressDialogT, width: int = 760):
        """Make updater dialogs wider and non-resizable."""
        dialog.setWindowFlag(QtCore.Qt.WindowType.WindowContextHelpButtonHint, False)
        dialog.setWindowFlag(QtCore.Qt.WindowType.MSWindowsFixedSizeDialogHint, True)
        dialog.setSizeGripEnabled(False)
        h = max(120, dialog.sizeHint().height())
        dialog.setFixedSize(width, h)

    # --- Force update binaries ---
    def on_force_update_binaries(self):
        if self.streaming:
            QtWidgets.QMessageBox.information(self, APP_NAME, "Stop streaming before updating binaries.")
            return
        if self.binary_check_thread and self.binary_check_thread.isRunning():
            return
        if self.binary_update_thread and self.binary_update_thread.isRunning():
            return

        self.force_update_btn.setEnabled(False)
        self.append_log("[INFO] Checking yt-dlp and FFmpeg versions...")

        self.binary_check_dialog = QtWidgets.QProgressDialog("Checking current and latest binary versions...", "", 0, 0, self)
        self.binary_check_dialog.setWindowTitle("Binary Updates")
        self.binary_check_dialog.setCancelButton(None)
        self.binary_check_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self.binary_check_dialog.setMinimumDuration(0)
        self._prepare_fixed_update_dialog(self.binary_check_dialog)
        self.binary_check_dialog.show()

        self.binary_check_thread = QtCore.QThread(self)
        self.binary_checker = BinaryVersionChecker()
        self.binary_checker.moveToThread(self.binary_check_thread)
        self.binary_check_thread.started.connect(self.binary_checker.check)
        self.binary_checker.checked.connect(self._on_binary_versions_checked)
        self.binary_checker.error_occurred.connect(self._on_binary_versions_error)
        self.binary_checker.checked.connect(self.binary_check_thread.quit)
        self.binary_checker.error_occurred.connect(self.binary_check_thread.quit)
        self.binary_check_thread.finished.connect(self._cleanup_binary_check_thread)
        self.binary_check_thread.start()

    def _cleanup_binary_check_thread(self):
        if self.binary_checker:
            self.binary_checker.deleteLater()
            self.binary_checker = None
        if self.binary_check_thread:
            self.binary_check_thread.deleteLater()
            self.binary_check_thread = None
        if self.binary_check_dialog:
            self.binary_check_dialog.close()
            self.binary_check_dialog.deleteLater()
            self.binary_check_dialog = None

    def _format_binary_summary(self, info: dict) -> str:
        def fmt(tool: str) -> str:
            row = info.get(tool, {})
            current = row.get("current_version") or "Unknown"
            latest = row.get("latest_version") or "Unknown"
            status = row.get("status") or "unknown"
            label = {
                "up_to_date": "Up to date",
                "update_available": "Update available",
                "unknown": "Could not verify",
            }.get(status, "Unknown")
            return f"{tool}: current {current} | latest {latest} | {label}"
        return "\n".join([fmt("yt-dlp"), fmt("ffmpeg")])

    def _on_binary_versions_checked(self, info: dict):
        if self.binary_check_dialog:
            self.binary_check_dialog.close()
        summary = self._format_binary_summary(info)
        self.append_log("[INFO] Binary version check complete.")
        self.append_log(f"[INFO] {summary.replace(chr(10), ' || ')}")

        if info.get("all_up_to_date"):
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                f"You're already on the latest versions.\n\n{summary}",
            )
            self.force_update_btn.setEnabled(True)
            return

        prompt = (
            "Binary update status:\n\n"
            f"{summary}\n\n"
            "Do you want to update yt-dlp and FFmpeg now?"
        )
        reply = QtWidgets.QMessageBox.question(
            self,
            APP_NAME,
            prompt,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            self.append_log("[INFO] Binary update canceled by user.")
            self.force_update_btn.setEnabled(True)
            return

        self._start_binary_update(info)

    def _on_binary_versions_error(self, message: str):
        if self.binary_check_dialog:
            self.binary_check_dialog.close()
        self.append_log(f"[WARN] {message}")
        reply = QtWidgets.QMessageBox.question(
            self,
            APP_NAME,
            f"{message}\n\nDo you still want to force update binaries?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self._start_binary_update()
        else:
            self.force_update_btn.setEnabled(True)

    def _start_binary_update(self, info: Optional[dict] = None):
        self.append_log("[INFO] Starting binaries update (yt-dlp, FFmpeg)…")
        update_ytdlp = True
        update_ffmpeg = True
        update_cap_mbps = int(self.update_cap_combo.currentData() or 10)
        if info:
            update_ytdlp = ((info.get("yt-dlp", {}) or {}).get("status") == "update_available")
            update_ffmpeg = ((info.get("ffmpeg", {}) or {}).get("status") == "update_available")
            if not update_ytdlp and not update_ffmpeg:
                self.force_update_btn.setEnabled(True)
                QtWidgets.QMessageBox.information(self, APP_NAME, "You're already on the latest versions.")
                return

        self.binary_update_dialog = QtWidgets.QProgressDialog("Preparing binary update...", "", 0, 100, self)
        self.binary_update_dialog.setWindowTitle("Updating Binaries")
        self.binary_update_dialog.setCancelButton(None)
        self.binary_update_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self.binary_update_dialog.setMinimumDuration(0)
        self.binary_update_dialog.setValue(0)
        self._prepare_fixed_update_dialog(self.binary_update_dialog)
        self.binary_update_dialog.show()

        class _BinaryUpdater(QtCore.QObject):
            done = QtCore.Signal(bool, object)
            log = QtCore.Signal(str)
            progress = QtCore.Signal(str, int)

            def __init__(self, do_ytdlp: bool, do_ffmpeg: bool, cap_mbps: int):
                super().__init__()
                self.do_ytdlp = do_ytdlp
                self.do_ffmpeg = do_ffmpeg
                self.cap_mbps = max(1, min(25, int(cap_mbps)))

            @QtCore.Slot()
            def run(self):
                try:
                    cfg = StreamConfig(playlist_url="", stream_key="", update_download_cap_mbps=self.cap_mbps)
                    worker = StreamWorker(cfg)
                    worker.log.connect(self.log)
                    self.progress.emit("Preparing update...", 2)
                    worker.ensure_binaries(
                        force=False,
                        progress_cb=lambda msg, pct: self.progress.emit(msg, pct),
                        force_ytdlp=self.do_ytdlp,
                        force_ffmpeg=self.do_ffmpeg,
                    )
                    self.progress.emit("Verifying installed versions...", 98)
                    status = gather_binary_update_status()
                    self.done.emit(True, status)
                except Exception as e:
                    self.done.emit(False, str(e))

        self.binary_update_thread = QtCore.QThread(self)
        self.binary_update_worker = _BinaryUpdater(update_ytdlp, update_ffmpeg, update_cap_mbps)
        self.binary_update_worker.moveToThread(self.binary_update_thread)
        self.binary_update_thread.started.connect(self.binary_update_worker.run)
        self.binary_update_worker.log.connect(self._on_binary_update_log)
        self.binary_update_worker.progress.connect(self._on_binary_update_progress)
        self.binary_update_worker.done.connect(self._on_binary_update_done)
        self.binary_update_worker.done.connect(self.binary_update_thread.quit)
        self.binary_update_thread.finished.connect(self._cleanup_binary_update_thread)
        self.binary_update_thread.start()

    def _cleanup_binary_update_thread(self):
        if self.binary_update_worker:
            self.binary_update_worker.deleteLater()
            self.binary_update_worker = None
        if self.binary_update_thread:
            self.binary_update_thread.deleteLater()
            self.binary_update_thread = None

    def _on_binary_update_log(self, text: str):
        self.append_log(text)

    def _on_binary_update_progress(self, message: str, percent: int):
        if not self.binary_update_dialog:
            return
        self.binary_update_dialog.setLabelText(message)
        self.binary_update_dialog.setValue(max(0, min(100, int(percent))))

    def _on_binary_update_done(self, ok: bool, payload: object):
        if self.binary_update_dialog:
            if ok:
                self.binary_update_dialog.setValue(100)
            self.binary_update_dialog.close()
            self.binary_update_dialog.deleteLater()
            self.binary_update_dialog = None

        self.force_update_btn.setEnabled(True)

        if not ok:
            self.append_log(f"[ERROR] Force update failed: {payload}")
            QtWidgets.QMessageBox.warning(self, APP_NAME, f"Binary update failed:\n{payload}")
            return

        info = payload if isinstance(payload, dict) else {}
        summary = self._format_binary_summary(info) if info else "Binary update finished."
        self.append_log("[INFO] Binaries update finished.")
        if info.get("all_up_to_date"):
            QtWidgets.QMessageBox.information(self, APP_NAME, f"Update complete.\n\n{summary}")
        else:
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                f"Update completed, but version status is not fully up to date.\n\n{summary}",
            )

    # --- settings (config.json) ---
    def load_settings(self):
        """Restore persisted settings from ``config.json``."""
        cfg = load_config_json()
        remember = str(cfg.get("remember", True)).lower() in ("1", "true", "yes", "on")
        self.remember_chk.setChecked(remember)

        if remember:
            self.playlist_edit.setText(cfg.get("playlist_url", ""))
            self.rtmp_edit.setText(cfg.get("rtmp_base", "rtmp://a.rtmp.youtube.com/live2"))
            self.key_edit.setText(cfg.get("stream_key", ""))

        self.overlay_chk.setChecked(bool(cfg.get("overlay_titles", True)))
        self.shuffle_chk.setChecked(bool(cfg.get("shuffle", False)))
        self.logfile_chk.setChecked(bool(cfg.get("log_to_file", False)))
        self.check_updates_startup_chk.setChecked(bool(cfg.get("check_updates_startup", True)))
        self.yt_auth_chk.setChecked(bool(cfg.get("yt_auth_enabled", False)))
        saved_browser = str(cfg.get("yt_auth_browser", "auto")).strip().lower()
        idx = self.yt_browser_combo.findData(saved_browser)
        if idx < 0:
            idx = self.yt_browser_combo.findData("auto")
        if idx >= 0:
            self.yt_browser_combo.setCurrentIndex(idx)
        self.yt_profile_edit.setText(str(cfg.get("yt_auth_profile", "")))
        self._update_auth_ui_state()
        self.advanced_toggle.setChecked(bool(cfg.get("show_advanced_settings", False)))
        self._set_advanced_visible(self.advanced_toggle.isChecked())
        # RTMP live mode compatibility toggle
        try:
            self.rtmp_live_chk.setChecked(bool(cfg.get("rtmp_live", False)))
        except Exception:
            pass

        if "resolution" in cfg:
            idx = self.res_combo.findText(cfg["resolution"])
            if idx >= 0:
                self.res_combo.setCurrentIndex(idx)
        
        if "framerate" in cfg:
            idx = self.fps_combo.findText(str(cfg["framerate"]))
            if idx >= 0:
                self.fps_combo.setCurrentIndex(idx)
        
        if "buffer_mode" in cfg:
            idx = self.buffer_combo.findText(cfg["buffer_mode"])
            if idx >= 0:
                self.buffer_combo.setCurrentIndex(idx)
        saved_encoder = str(cfg.get("encoder_preference", "auto")).strip().lower()
        idx = self.encoder_combo.findData(saved_encoder)
        if idx < 0:
            idx = self.encoder_combo.findData("auto")
        if idx >= 0:
            self.encoder_combo.setCurrentIndex(idx)
        try:
            cap = int(cfg.get("update_download_cap_mbps", 10))
        except Exception:
            cap = 10
        cap = max(1, min(25, cap))
        idx = self.update_cap_combo.findData(cap)
        if idx >= 0:
            self.update_cap_combo.setCurrentIndex(idx)
                
        if "video_bitrate" in cfg:
            self.bitrate_edit.setText(cfg["video_bitrate"])
        if "bufsize" in cfg:
            self.bufsize_edit.setText(cfg["bufsize"])
        self.runtime_state.set_meta(
            source=self.playlist_edit.text().strip(),
            resolution=self.res_combo.currentText(),
            fps=self.fps_combo.currentText(),
        )

    def save_settings(self):
        """Persist user settings to ``config.json``."""
        data = load_config_json()
        data.update({
            "remember": self.remember_chk.isChecked(),
            "overlay_titles": self.overlay_chk.isChecked(),
            "shuffle": self.shuffle_chk.isChecked(),
            "log_to_file": self.logfile_chk.isChecked(),
            "rtmp_live": (self.rtmp_live_chk.isChecked() if hasattr(self, "rtmp_live_chk") and self.rtmp_live_chk is not None else False),
            "check_updates_startup": self.check_updates_startup_chk.isChecked(),
            "resolution": self.res_combo.currentText(),
            "framerate": int(self.fps_combo.currentText()),
            "buffer_mode": self.buffer_combo.currentText(),
            "encoder_preference": (self.encoder_combo.currentData() or "auto"),
            "video_bitrate": self.bitrate_edit.text().strip(),
            "bufsize": self.bufsize_edit.text().strip(),
            "update_download_cap_mbps": int(self.update_cap_combo.currentData() or 10),
            "yt_auth_enabled": self.yt_auth_chk.isChecked(),
            "yt_auth_browser": (self.yt_browser_combo.currentData() or "auto"),
            "yt_auth_profile": self.yt_profile_edit.text().strip(),
            "show_advanced_settings": self.advanced_toggle.isChecked(),
        })
        if self.remember_chk.isChecked():
            data["playlist_url"] = self.playlist_edit.text().strip()
            data["rtmp_base"] = self.rtmp_edit.text().strip()
            data["stream_key"] = self.key_edit.text().strip()
        else:
            data.pop("playlist_url", None)
            data.pop("rtmp_base", None)
            data.pop("stream_key", None)
        save_config_json(data)
        self.runtime_state.set_meta(
            source=self.playlist_edit.text().strip(),
            resolution=self.res_combo.currentText(),
            fps=self.fps_combo.currentText(),
        )

    def closeEvent(self, event: QtCloseEventT) -> None:
        """Persist settings when the window is closed."""
        self._flush_log_buffer()
        self.save_settings()
        if self.web_dashboard:
            self.web_dashboard.stop()
        return super().closeEvent(event)

    # --- UI helpers ---
    def _is_ffmpeg_log(self, text: str) -> bool:
        """Route untagged process output to FFmpeg tab while keeping app logs separate."""
        s = (text or "").strip()
        if not s:
            return False
        lower = s.lower()
        if "[cmd] ffmpeg" in lower or "ffmpeg exited with code" in lower:
            return True
        if s.startswith("frame=") or s.startswith("size="):
            return True
        if any(s.startswith(prefix) for prefix in self.APP_LOG_PREFIXES):
            return False
        return True

    def append_log(self, text: str):
        """Queue a log line for batched console and file writes."""
        self.runtime_state.append_log(text)
        self._pending_logs.append((self._is_ffmpeg_log(text), text))
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    @QtCore.Slot(str)
    def _on_worker_status(self, status: str) -> None:
        self.runtime_state.set_status(status)
        self.append_log(f"[STATUS] {status}")

    def _flush_log_buffer(self):
        """Flush queued log lines in batches to reduce UI churn."""
        if not self._pending_logs:
            self._log_flush_timer.stop()
            return
        batch = list(self._pending_logs)
        self._pending_logs.clear()
        ffmpeg_lines = [line for is_ffmpeg, line in batch if is_ffmpeg]
        other_lines = [line for is_ffmpeg, line in batch if not is_ffmpeg]

        if other_lines:
            joined_other = "\n".join(other_lines)
            self.console_other.appendPlainText(joined_other)
            self.console_other.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        if ffmpeg_lines:
            joined_ffmpeg = "\n".join(ffmpeg_lines)
            self.console_ffmpeg.appendPlainText(joined_ffmpeg)
            self.console_ffmpeg.moveCursor(QtGui.QTextCursor.MoveOperation.End)

        joined = "\n".join(line for _is_ffmpeg, line in batch)
        if self.log_fh:
            try:
                self.log_fh.write(joined + "\n")
                self.log_fh.flush()
            except Exception:
                pass
        if not self._pending_logs:
            self._log_flush_timer.stop()

    def on_quality_change(self):
        """Update internal FPS/height presets when the quality dropdown changes."""
        resolution = self.res_combo.currentText()
        framerate = int(self.fps_combo.currentText())
        
        height, suggested_bitrate, suggested_bufsize = self.RESOLUTION_PRESETS.get(
            resolution, self.RESOLUTION_PRESETS["720p"]
        )
        
        self._fps = framerate
        self._height = height
        
        # Only update bitrate/bufsize if not currently streaming
        if not self.streaming:
            self.bitrate_edit.setText(suggested_bitrate)
            self.bufsize_edit.setText(suggested_bufsize)

    def make_config(self) -> StreamConfig:
        """Create a StreamConfig from the current UI state."""
        return StreamConfig(
            playlist_url=self.playlist_edit.text().strip(),
            stream_key=self.key_edit.text().strip(),
            rtmp_base=self.rtmp_edit.text().strip(),
            fps=self._fps,
            height=self._height,
            video_bitrate=self.bitrate_edit.text().strip(),
            bufsize=self.bufsize_edit.text().strip(),
            audio_bitrate="128k",
            overlay_titles=self.overlay_chk.isChecked(),
            shuffle=self.shuffle_chk.isChecked(),
            title_file="current_title.txt",
            rtmp_live=(self.rtmp_live_chk.isChecked() if hasattr(self, "rtmp_live_chk") and self.rtmp_live_chk is not None else False),
            buffer_mode=self.buffer_combo.currentText(),
            encoder_preference=str(self.encoder_combo.currentData() or "auto"),
            update_download_cap_mbps=int(self.update_cap_combo.currentData() or 10),
            yt_auth_enabled=self.yt_auth_chk.isChecked(),
            yt_auth_browser=str(self.yt_browser_combo.currentData() or "auto"),
            yt_auth_profile=self.yt_profile_edit.text().strip(),
        )

    # --- update checker ---
    def check_for_updates(self):
        """Manually check for updates (triggered by button click)."""
        if self.update_thread and self.update_thread.isRunning():
            return  # Already checking
            
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("Checking...")
        self.update_status_label.setText("Checking for updates...")
        self.update_status_label.setStyleSheet("color: #888; font-style: italic;")
        
        self._start_update_check()
    
    def check_for_updates_silent(self):
        """Silently check for updates on startup."""
        if self.update_thread and self.update_thread.isRunning():
            return  # Already checking
            
        self._start_update_check()
    
    def _start_update_check(self):
        """Start the update checking process in a background thread."""
        self.update_thread = QtCore.QThread(self)
        self.update_checker = UpdateChecker()
        self.update_checker.moveToThread(self.update_thread)
        
        self.update_thread.started.connect(self.update_checker.check_for_updates)
        self.update_checker.update_checked.connect(self._on_update_checked)
        self.update_checker.error_occurred.connect(self._on_update_error)
        self.update_checker.update_checked.connect(self.update_thread.quit)
        self.update_checker.error_occurred.connect(self.update_thread.quit)
        self.update_thread.finished.connect(self._on_update_check_finished)
        
        self.update_thread.start()
    
    def _on_update_checked(self, update_info: dict):
        """Handle successful update check."""
        current_version = update_info.get('current_version', APP_VERSION)
        latest_version = update_info.get('latest_version', 'Unknown')
        is_newer = update_info.get('is_newer', False)
        release_url = update_info.get('release_url', '')
        download_url = update_info.get('download_url', '')
        published_date = update_info.get('published_date', '')
        
        if is_newer:
            # Update available
            self.update_status_label.setText(
                f"<b>Update available!</b> v{latest_version} "
                f"(Current: v{current_version})"
            )
            self.update_status_label.setStyleSheet("color: #5DADE2; font-weight: bold;")
            
            # Show update dialog
            self._show_update_dialog(update_info)
        else:
            # Up to date
            self.update_status_label.setText(f"You're up to date! (v{current_version})")
            self.update_status_label.setStyleSheet("color: #58D68D; font-weight: bold;")
    
    def _on_update_error(self, error_message: str):
        """Handle update check error."""
        self.update_status_label.setText(f"Update check failed: {error_message}")
        self.update_status_label.setStyleSheet("color: #E74C3C; font-style: italic;")
    
    def _on_update_check_finished(self):
        """Re-enable the check button after update check completes."""
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("Check for Updates")
        
        # Clean up
        if self.update_thread:
            self.update_thread.deleteLater()
            self.update_thread = None
        if self.update_checker:
            self.update_checker.deleteLater()
            self.update_checker = None
    
    def _show_update_dialog(self, update_info: dict):
        """Show a dialog with update information."""
        latest_version = update_info.get('latest_version', 'Unknown')
        release_name = update_info.get('release_name', '')
        release_notes = update_info.get('release_notes', '')
        release_url = update_info.get('release_url', '')
        download_url = update_info.get('download_url', '')
        published_date = update_info.get('published_date', '')
        
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Update Available - {APP_NAME}")
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(400)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # Header
        header = QtWidgets.QLabel(f"<h2>Update Available!</h2>")
        layout.addWidget(header)
        
        # Version info
        version_text = f"<b>Current Version:</b> {APP_VERSION}<br>"
        version_text += f"<b>Latest Version:</b> {latest_version}"
        if published_date:
            version_text += f"<br><b>Released:</b> {published_date}"
        
        version_label = QtWidgets.QLabel(version_text)
        layout.addWidget(version_label)
        
        # Release notes
        if release_notes:
            notes_label = QtWidgets.QLabel("<b>Release Notes:</b>")
            layout.addWidget(notes_label)
            
            notes_text = QtWidgets.QTextEdit()
            notes_text.setPlainText(release_notes)
            notes_text.setMaximumHeight(200)
            notes_text.setReadOnly(True)
            layout.addWidget(notes_text)
        
        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        
        if download_url:
            download_btn = QtWidgets.QPushButton("Download Update")
            download_btn.clicked.connect(lambda: self._open_url(download_url))
            button_layout.addWidget(download_btn)
        
        if release_url:
            view_btn = QtWidgets.QPushButton("View on GitHub")
            view_btn.clicked.connect(lambda: self._open_url(release_url))
            button_layout.addWidget(view_btn)
        
        button_layout.addStretch()
        
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def _open_url(self, url: str):
        """Open URL in default browser."""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    # --- start/stop wiring ---
    def on_start(self):
        """Validate input and start the background streaming worker."""
        if self.streaming:
            return
        cfg = self.make_config()
        if not cfg.playlist_url or not cfg.rtmp_base or not cfg.stream_key:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Please enter Source URL, Stream URL, and Stream Key.")
            return

        # save settings right when starting (if 'remember' is checked)
        self.save_settings()

        if self.log_fh:
            try:
                self.log_fh.close()
            except Exception:
                pass
            self.log_fh = None
        if self.logfile_chk.isChecked():
            self.log_fh, _ = open_rotating_latest_log()

        self.streaming = True
        self.runtime_state.set_streaming(True)
        self.runtime_state.set_status("Starting")
        self.runtime_state.set_meta(
            source=cfg.playlist_url,
            resolution=f"{cfg.height}p",
            fps=str(cfg.fps),
        )
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.skip_btn.setEnabled(True)
        self.append_log("[INFO] Starting stream…")

        self.worker_thread = QtCore.QThread(self)
        self.worker = StreamWorker(cfg)
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.status.connect(self._on_worker_status)
        self.worker.finished.connect(self.on_finished)

        self.worker_thread.start()

    def on_stop(self):
        """Stop the streaming worker gracefully."""
        if not self.streaming:
            return
        self.runtime_state.set_status("Stopping")
        self.append_log("[INFO] Stopping…")

        # Call directly so stop is immediate even while worker loop is busy.
        try:
            if self.worker:
                self.worker.stop()
        except Exception:
            pass

        # Do not quit the thread here; let on_finished() handle cleanup
        self.stop_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)

    def on_skip(self):
        """Skip the current video."""
        if not self.streaming or not self.worker:
            return
        self.append_log("[INFO] Skipping…")
        try:
            self.worker.skip()
        except Exception:
            pass

    def on_test_rtmp(self):
        """Run a 1-second RTMP preflight without starting the full stream."""
        if self.streaming:
            QtWidgets.QMessageBox.information(self, APP_NAME, "Stop streaming to test RTMP.")
            return
        cfg = self.make_config()
        if not cfg.rtmp_base or not cfg.stream_key:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "Please enter Stream URL and Stream Key to test RTMP.")
            return
        self.append_log("[INFO] Testing RTMP connectivity…")

        class _RTMPTester(QtCore.QObject):
            done = QtCore.Signal(bool)
            log = QtCore.Signal(str)
            def __init__(self, cfg: StreamConfig):
                super().__init__()
                self.cfg = cfg
            def run(self):
                ok = False
                try:
                    worker = StreamWorker(self.cfg)
                    worker.log.connect(self.log)
                    worker.ensure_binaries()
                    worker.select_encoder()
                    ok = worker.preflight_rtmp()
                except Exception as e:
                    self.log.emit(f"[ERROR] RTMP test failed: {e}")
                finally:
                    self.done.emit(ok)

        self._rtmp_thread = QtCore.QThread(self)
        self._rtmp_worker = _RTMPTester(cfg)
        self._rtmp_worker.moveToThread(self._rtmp_thread)
        self._rtmp_thread.started.connect(self._rtmp_worker.run)
        self._rtmp_worker.log.connect(self.append_log)
        def _finish(ok: bool):
            if ok:
                self.append_log("[INFO] RTMP test succeeded.")
            else:
                self.append_log("[WARN] RTMP test failed. Check URL/port/app/key and TLS (rtmp vs rtmps).")
            self._rtmp_worker.deleteLater()
            self._rtmp_thread.deleteLater()
        self._rtmp_worker.done.connect(_finish)
        self._rtmp_thread.start()

    def on_finished(self):
        """Cleanup once the worker thread stops."""
        self.append_log("[INFO] Worker finished.")
        self._flush_log_buffer()
        if self.log_fh:
            try:
                self.log_fh.close()
            except Exception:
                pass
            self.log_fh = None
        try:
            if self.worker_thread:
                self.worker_thread.quit()
                self.worker_thread.wait(5000)
        except Exception:
            pass
        self.worker = None
        self.worker_thread = None
        self.streaming = False
        self.runtime_state.set_streaming(False)
        self.runtime_state.set_status("Stopped")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)

class HeadlessRuntime:
    """Run stream worker and optional web dashboard without a GUI window."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.runtime_state = RuntimeStateStore()
        self.runtime_state.set_meta(mode="headless")
        self.log_fh: Optional[TextIO] = None
        self._log_fh_lock = threading.Lock()
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
        }
        self._app_update_lock = threading.Lock()
        self._app_update_state: Dict[str, object] = {
            "running": False,
            "last_result": None,
            "last_error": "",
            "started_at": 0.0,
            "finished_at": 0.0,
            "downloaded_path": "",
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
            if RuntimeStateStore._is_ffmpeg_log(text):
                return
            with self._log_fh_lock:
                if not self.log_fh:
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
        # In headless mode, always persist logs so latest.log mirrors dashboard console output.
        enabled = True
        with self._log_fh_lock:
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
            cap = int(settings.get("update_download_cap_mbps", 10) or 10)
            cap = max(1, min(25, cap))
            worker = StreamWorker(StreamConfig(playlist_url="", stream_key="", update_download_cap_mbps=cap))
            worker.log.connect(self.log, QtCore.Qt.ConnectionType.DirectConnection)
            self.log("[INFO] Starting binaries update (yt-dlp, FFmpeg)...")
            worker.ensure_binaries(force=True)
            result = gather_binary_update_status()
            with self._binary_lock:
                self._binary_state["last_result"] = result
                self._binary_state["last_error"] = ""
                self._binary_state["running"] = False
                self._binary_state["finished_at"] = time.time()
            self.log("[INFO] Binaries update finished.")
        except Exception as e:
            with self._binary_lock:
                self._binary_state["last_error"] = str(e)
                self._binary_state["running"] = False
                self._binary_state["finished_at"] = time.time()
            self.log(f"[ERROR] Binaries update failed: {e}")

    def trigger_binaries_update(self) -> Dict[str, object]:
        with self._binary_lock:
            if self._binary_state.get("running", False):
                return dict(self._binary_state)
            self._binary_state["running"] = True
            self._binary_state["started_at"] = time.time()
            self._binary_state["last_error"] = ""
        t = threading.Thread(target=self._run_binaries_update, daemon=True)
        t.start()
        return self.get_binaries_status()

    def get_app_update_status(self) -> Dict[str, object]:
        with self._app_update_lock:
            running = bool(self._app_update_state.get("running", False))
            if (not running) and self._app_update_state.get("last_result") is None and not self._app_update_state.get("last_error"):
                try:
                    self._app_update_state["last_result"] = fetch_latest_app_release_info()
                    self._app_update_state["finished_at"] = time.time()
                except Exception as e:
                    self._app_update_state["last_error"] = str(e)
                    self._app_update_state["finished_at"] = time.time()
            return dict(self._app_update_state)

    def _run_app_update_download(self) -> None:
        try:
            info = fetch_latest_app_release_info()
            dl_url = str(info.get("download_url", "")).strip()
            asset_name = str(info.get("asset_name", "")).strip()
            if not dl_url:
                raise RuntimeError("No downloadable release asset found for this platform.")
            if not asset_name:
                asset_name = Path(urlsplit(dl_url).path).name or "app-update-script"
            base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else _app_dir()
            updates_dir = base_dir / "updates"
            updates_dir.mkdir(parents=True, exist_ok=True)
            dest = updates_dir / asset_name
            self.log(f"[INFO] Downloading app update to {dest} ...")
            _download_url(dl_url, dest, user_agent=f"{APP_NAME}/{APP_VERSION}")
            if (not IS_WIN) and dest.exists():
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
            self.log(f"[INFO] App update downloaded: {dest}")
            if dest.suffix.lower() in (".ps1", ".sh"):
                self.log("[INFO] Manual install: run the downloaded update script for your OS.")
            else:
                self.log("[INFO] Manual install: stop service and replace current binary with the downloaded file.")
        except Exception as e:
            with self._app_update_lock:
                self._app_update_state["last_error"] = str(e)
                self._app_update_state["running"] = False
                self._app_update_state["finished_at"] = time.time()
            self.log(f"[ERROR] App update download failed: {e}")

    def trigger_app_update_download(self) -> Dict[str, object]:
        with self._app_update_lock:
            if self._app_update_state.get("running", False):
                return dict(self._app_update_state)
            self._app_update_state["running"] = True
            self._app_update_state["started_at"] = time.time()
            self._app_update_state["last_error"] = ""
        t = threading.Thread(target=self._run_app_update_download, daemon=True)
        t.start()
        return self.get_app_update_status()

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
