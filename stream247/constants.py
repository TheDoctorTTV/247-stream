"""Static constants and presets for Stream247."""

from typing import Dict, Tuple

APP_NAME = "Stream247"  # Name shown in logs and dashboard
APP_VERSION = "2.1.1"  # Current version
GITHUB_REPO = "TheDoctorTTV/247-stream"  # GitHub repository for updates
APP_UPDATE_MIN_VERSION = "2.0-pre-release-2"  # Oldest version eligible for in-app updater

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

# Buffer presets for FFmpeg input/output behavior.
BUFFER_PRESETS = {
    "Low": {
        "probesize": "15M",
        "analyzeduration": "5000000",
        "buffer_size": "2048k",
        "max_delay": "3000000",
    },
    "Medium": {
        "probesize": "25M",
        "analyzeduration": "10000000",
        "buffer_size": "4096k",
        "max_delay": "7000000",
    },
    "High": {
        "probesize": "40M",
        "analyzeduration": "15000000",
        "buffer_size": "6144k",
        "max_delay": "12000000",
    },
    "Ultra": {
        "probesize": "50M",
        "analyzeduration": "30000000",
        "buffer_size": "8192k",
        "max_delay": "25000000",
    },
}

# Shared resolution presets used by config parsing.
RESOLUTION_PRESETS: Dict[str, Tuple[int, str]] = {
    "480p": (480, "1500k"),
    "720p": (720, "2500k"),
    "1080p": (1080, "6000k"),
    "1440p": (1440, "9000k"),
    "2160p": (2160, "25000k"),
}
