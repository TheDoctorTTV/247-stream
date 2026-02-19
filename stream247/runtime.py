import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, TextIO
from urllib.parse import urlsplit

from .config import (
    _app_dir,
    _running_under_systemd,
    _to_bool,
    apply_web_settings_payload,
    load_config_json,
    read_web_server_settings,
    save_config_json,
    web_settings_payload_from_config,
)
from .constants import APP_NAME, APP_VERSION, WEB_ALLOWED_UPDATE_CHANNELS
from .models import StreamConfig, stream_config_from_settings
from .qtcompat import QtCore
from .updates import (
    _is_release_asset_self_installable,
    _is_supported_update_version,
    fetch_latest_app_release_info,
    gather_binary_update_status,
)
from .utils import _download_url, open_rotating_latest_log, restore_terminal_state
from .web import RuntimeStateStore, LocalWebDashboard
from .worker import StreamWorker

class HeadlessRuntime:
    """Run stream worker and web dashboard."""

    _LOG_LEVEL_RE = re.compile(r"^\[(INFO|WARN|ERROR|STATUS|PREFETCH|CMD|DETAIL|DEBUG)\]\s*(.*)$")
    _FFMPEG_STAT_RE = re.compile(r"([A-Za-z_]+)=\s*([^\s]+)")
    _FFMPEG_STAT_FIELDS = ("frame", "fps", "q", "size", "time", "bitrate", "speed", "dup", "drop")

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
            "progress_percent": 0,
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
            raw_text = str(text or "")
            is_ffmpeg = RuntimeStateStore._is_ffmpeg_log(raw_text)
            formatted_lines = self._format_log_lines(raw_text, is_ffmpeg)
            if not formatted_lines:
                return
            for line in formatted_lines:
                self.runtime_state.append_log(line, is_ffmpeg=is_ffmpeg)
            with self._log_fh_lock:
                if not self.log_fh:
                    return
                if is_ffmpeg and not self._ffmpeg_log_to_file:
                    return
                if (not is_ffmpeg) and (not self._app_log_to_file):
                    return
                try:
                    for line in formatted_lines:
                        self.log_fh.write(f"{line}\n")
                    self.log_fh.flush()
                except Exception:
                    pass
        except KeyboardInterrupt:
            # Allow Ctrl+C to terminate cleanly without cascading tracebacks.
            return

    @classmethod
    def _format_ffmpeg_stats(cls, text: str) -> Optional[str]:
        pairs = cls._FFMPEG_STAT_RE.findall(text)
        if not pairs:
            return None
        values = {k.lower(): v for k, v in pairs}
        if "frame" not in values:
            return None
        ordered: List[str] = []
        for key in cls._FFMPEG_STAT_FIELDS:
            val = values.pop(key, "")
            if val:
                ordered.append(f"{key}={val}")
        for key in sorted(values):
            ordered.append(f"{key}={values[key]}")
        return " | ".join(ordered)

    @classmethod
    def _format_log_line(cls, text: str, is_ffmpeg: bool, ts_label: str) -> str:
        line = str(text or "").replace("\t", "    ").strip()
        if not line:
            return ""
        match = cls._LOG_LEVEL_RE.match(line)
        if match:
            level = match.group(1)
            message = match.group(2).strip()
            return f"[{ts_label}] {level:<7} {message or '-'}"
        if is_ffmpeg:
            stats = cls._format_ffmpeg_stats(line)
            if stats:
                return f"[{ts_label}] FFMPEG  {stats}"
            return f"[{ts_label}] FFMPEG  {line}"
        return f"[{ts_label}] LOG     {line}"

    @classmethod
    def _format_log_lines(cls, text: str, is_ffmpeg: bool) -> List[str]:
        raw = str(text or "").replace("\r", "\n")
        lines = [line for line in raw.splitlines() if line.strip()]
        if not lines:
            return []
        ts_label = time.strftime("%H:%M:%S")
        out: List[str] = []
        for line in lines:
            formatted = cls._format_log_line(line, is_ffmpeg, ts_label)
            if formatted:
                out.append(formatted)
        return out

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
            cap = 50
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
            self._app_update_state["progress_percent"] = 0
            self._app_update_state["selected_version"] = selected_version
            self._app_update_state["selected_channel"] = channel
            self._app_update_state["force_reinstall"] = False
        status = self.get_app_update_status()
        with self._app_update_lock:
            if not self._app_update_state.get("running", False):
                self._app_update_state["progress_message"] = ""
            status = dict(self._app_update_state)
        return status

    def _set_app_update_progress(self, message: str, percent: Optional[int] = None) -> None:
        with self._app_update_lock:
            self._app_update_state["progress_message"] = str(message or "")
            if percent is not None:
                self._app_update_state["progress_percent"] = max(0, min(100, int(percent)))

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
            cap = 50
            channel = str(selected_channel or settings.get("app_update_channel", "release")).strip().lower()
            if channel not in WEB_ALLOWED_UPDATE_CHANNELS:
                channel = "release"
            if auto_mode:
                mode_label = "automatic"
            elif force_reinstall:
                mode_label = "manual (reinstall)"
            else:
                mode_label = "manual"
            self._set_app_update_progress("Checking latest app release...", 5)
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
                    self._app_update_state["progress_percent"] = 100
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
            self._set_app_update_progress("Downloading app update...", 12)

            def _progress_cb(done: int, total: int) -> None:
                if total <= 0:
                    return
                pct = int((done * 100) / total)
                mapped = 12 + int((80 * pct) / 100)
                self._set_app_update_progress(f"Downloading app update... {pct}%", mapped)

            _download_url(
                dl_url,
                dest,
                user_agent=f"{APP_NAME}/{APP_VERSION}",
                progress_cb=_progress_cb,
                max_mbps=cap,
            )
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
                self._app_update_state["progress_percent"] = 95
                self._app_update_state["selected_version"] = str(info.get("selected_version", "") or "")
                self._app_update_state["selected_channel"] = str(info.get("channel", channel) or channel)
                self._app_update_state["force_reinstall"] = bool(force_reinstall)
            self.log(f"[INFO] App update downloaded: {dest}")
            if not getattr(sys, "frozen", False):
                with self._app_update_lock:
                    self._app_update_state["progress_message"] = "Downloaded update (source mode: install manually)."
                    self._app_update_state["progress_percent"] = 100
                self.log("[INFO] Source mode detected. Automatic install/restart is only available in packaged builds.")
                return
            self.log("[INFO] Installing app update and restarting...")
            self._set_app_update_progress("Installing update and restarting...", 100)
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
            self._app_update_state["progress_percent"] = 0
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
