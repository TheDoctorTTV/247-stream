"""Streaming data models and config translation."""

from dataclasses import dataclass
from typing import Dict, List

from .config import _kbps_to_text, _parse_bitrate_kbps, _resolved_sources_and_playlist, _to_bool
from .constants import (
    BITRATE_DEFAULT_KBPS,
    BITRATE_MIN_KBPS,
    BUFFER_MODE_BUFSIZE_MULTIPLIER,
    RESOLUTION_PRESETS,
    WEB_ALLOWED_BUFFER_MODES,
)


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
    buffer_mode: str = "Medium"
    yt_auth_enabled: bool = False
    yt_auth_browser: str = "auto"
    yt_auth_profile: str = ""
    yt_auth_allow_unauth_fallback: bool = True
    youtube_persistent_output: bool = True
    update_download_cap_mbps: int = 50
    encoder_preference: str = "auto"

    encoder: str = "libx264"
    encoder_name: str = "CPU x264"
    pix_fmt: str = "yuv420p"
    extra_venc_flags: List[str] = None  # type: ignore
    _overlay_fontsize: int = 24

    def rtmp_url(self) -> str:
        return f"{self.rtmp_base}/{self.stream_key}"


def stream_config_from_settings(data: Dict[str, object]) -> StreamConfig:
    """Build StreamConfig from a persisted settings dictionary."""
    _, playlist_url = _resolved_sources_and_playlist(data)
    resolution = str(data.get("resolution", "720p"))
    height, preset_bitrate = RESOLUTION_PRESETS.get(resolution, RESOLUTION_PRESETS["720p"])
    fps_raw = data.get("framerate", 30)
    try:
        fps = int(fps_raw)
    except Exception:
        fps = 30
    if fps not in (30, 60):
        fps = 30
    cap_mbps = 50
    buffer_mode = str(data.get("buffer_mode", "Medium")).strip() or "Medium"
    if buffer_mode not in WEB_ALLOWED_BUFFER_MODES:
        buffer_mode = "Medium"
    preset_kbps = _parse_bitrate_kbps(preset_bitrate, BITRATE_DEFAULT_KBPS)
    video_kbps = _parse_bitrate_kbps(data.get("video_bitrate", preset_bitrate), preset_kbps)
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
        youtube_persistent_output=_to_bool(data.get("youtube_persistent_output", True), True),
        update_download_cap_mbps=cap_mbps,
        encoder_preference=str(data.get("encoder_preference", "auto")).strip() or "auto",
    )
