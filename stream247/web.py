import json
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from .constants import APP_NAME, APP_VERSION, GITHUB_REPO
from .utils import resource_path

class RuntimeStateStore:
    """Thread-safe runtime state used by the runtime and web dashboard."""

    CONSOLE_LOG_MAX_LINES = 100

    def __init__(self, log_limit: int = CONSOLE_LOG_MAX_LINES):
        self._lock = threading.Lock()
        self._log_limit = max(1, int(log_limit))
        self._logs: deque[Tuple[float, str]] = deque()
        self._logs_other: deque[Tuple[float, str]] = deque()
        self._logs_ffmpeg: deque[Tuple[float, str]] = deque()
        self._status = "Idle"
        self._streaming = False
        self._updated_at = time.time()
        self._meta: Dict[str, object] = {}

    @staticmethod
    def _trim_to_limit(bucket: deque[Tuple[float, str]], limit: int) -> None:
        while len(bucket) > limit:
            bucket.popleft()

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

    def append_log(self, line: str, is_ffmpeg: Optional[bool] = None) -> None:
        text = (line or "").rstrip()
        if not text:
            return
        now = time.time()
        ffmpeg_line = self._is_ffmpeg_log(text) if is_ffmpeg is None else bool(is_ffmpeg)
        with self._lock:
            self._logs.append((now, text))
            self._trim_to_limit(self._logs, self._log_limit)
            if ffmpeg_line:
                self._logs_ffmpeg.append((now, text))
                self._trim_to_limit(self._logs_ffmpeg, self._log_limit)
            else:
                self._logs_other.append((now, text))
                self._trim_to_limit(self._logs_other, self._log_limit)
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

            def _send_bytes(
                self,
                data: bytes,
                content_type: str,
                status: int = 200,
                cache_control: str = "public, max-age=3600",
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", cache_control)
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
                        self._send_bytes(
                            css_path.read_bytes(),
                            "text/css; charset=utf-8",
                            cache_control="no-store, max-age=0",
                        )
                    except Exception:
                        self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Failed to read style.css")
                    return
                if path == "/assets/app.js":
                    js_path = Path(resource_path("web/app.js"))
                    if not js_path.exists():
                        self.send_error(HTTPStatus.NOT_FOUND, "app.js not found")
                        return
                    try:
                        self._send_bytes(
                            js_path.read_bytes(),
                            "application/javascript; charset=utf-8",
                            cache_control="no-store, max-age=0",
                        )
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
