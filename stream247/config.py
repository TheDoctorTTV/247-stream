"""Config helpers and settings normalization."""

import json
import platform
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .constants import (
    BITRATE_DEFAULT_KBPS,
    BITRATE_MAX_KBPS,
    BITRATE_MIN_KBPS,
    BITRATE_STEP_KBPS,
    BUFFER_MODE_BUFSIZE_MULTIPLIER,
    WEB_ALLOWED_BROWSERS,
    WEB_ALLOWED_BUFFER_MODES,
    WEB_ALLOWED_ENCODERS,
    WEB_ALLOWED_FRAMERATES,
    WEB_ALLOWED_RESOLUTIONS,
    WEB_ALLOWED_UPDATE_CHANNELS,
)


def _app_dir() -> Path:
    """Return the directory where the app is running from."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).parent
    return Path.cwd()


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
        "youtube_persistent_output": _to_bool(cfg.get("youtube_persistent_output", True), True),
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
        out.pop("bufsize", None)
    return out
