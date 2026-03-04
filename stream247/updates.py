"""Binary and application update helpers."""

import json
import platform
import re
import shutil
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import _app_dir
from .constants import (
    APP_NAME,
    APP_UPDATE_MIN_VERSION,
    APP_VERSION,
    GITHUB_REPO,
    WEB_ALLOWED_UPDATE_CHANNELS,
    WEB_UPDATE_RELEASES_LIMIT,
)
from .utils import run_hidden


def _binary_names_for_platform() -> Tuple[str, str]:
    return ("yt-dlp", "ffmpeg")


def _preferred_binary_paths() -> Dict[str, Optional[str]]:
    app_dir = _app_dir()
    ytdlp_name, ffmpeg_name = _binary_names_for_platform()
    local_ytdlp = app_dir / ytdlp_name
    local_ffmpeg = app_dir / ffmpeg_name
    ytdlp_path = str(local_ytdlp) if local_ytdlp.exists() else shutil.which("yt-dlp")
    ffmpeg_path = str(local_ffmpeg) if local_ffmpeg.exists() else shutil.which("ffmpeg")
    return {"yt-dlp": ytdlp_path, "ffmpeg": ffmpeg_path}


def _read_tool_version(binary_path: Optional[str], tool: str) -> Optional[str]:
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
    try:
        headers = {}
        if user_agent:
            headers["User-Agent"] = user_agent
        if platform.system().lower() == "linux":
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


def _canonicalize_suffix(suffix: str) -> str:
    raw = str(suffix or "").strip().lower()
    if not raw:
        return ""
    tokens = re.findall(r"[a-z]+|\d+", raw)
    if not tokens:
        return ""
    out: List[str] = []
    i = 0
    while i < len(tokens):
        cur = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if cur == "pre" and nxt == "release":
            out.append("prerelease")
            i += 2
            continue
        out.append(cur)
        i += 1
    return "-".join(out)


def _split_canonical_suffix_tokens(suffix: str) -> List[object]:
    text = _canonicalize_suffix(suffix)
    if not text:
        return []
    out: List[object] = []
    for tok in text.split("-"):
        if tok.isdigit():
            out.append(int(tok))
        else:
            out.append(tok)
    return out


def _normalize_app_version_string(version: str) -> str:
    parsed = _parse_app_version(version)
    if not parsed:
        return str(version or "").strip().lstrip("vV").lower()
    core, suffix = parsed
    core_text = ".".join(str(x) for x in core)
    return core_text if not suffix else f"{core_text}-{suffix}"


def _parse_app_version(version: str) -> Optional[Tuple[List[int], str]]:
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
    suffix = _canonicalize_suffix(suffix)
    return core, suffix


def _compare_app_versions(target: str, current: str) -> Optional[int]:
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
    if not target_suffix and current_suffix:
        return 1
    if target_suffix and not current_suffix:
        return -1
    if target_suffix == current_suffix:
        return 0
    t_tokens = _split_canonical_suffix_tokens(target_suffix)
    c_tokens = _split_canonical_suffix_tokens(current_suffix)
    n2 = max(len(t_tokens), len(c_tokens))
    for i in range(n2):
        if i >= len(t_tokens):
            return -1
        if i >= len(c_tokens):
            return 1
        tv = t_tokens[i]
        cv = c_tokens[i]
        if isinstance(tv, int) and isinstance(cv, int):
            if tv > cv:
                return 1
            if tv < cv:
                return -1
            continue
        if isinstance(tv, int) and isinstance(cv, str):
            return -1
        if isinstance(tv, str) and isinstance(cv, int):
            return 1
        if str(tv) > str(cv):
            return 1
        if str(tv) < str(cv):
            return -1
    return 0


def _is_version_newer(latest: str, current: str) -> bool:
    cmp = _compare_app_versions(latest, current)
    if cmp is not None:
        return cmp > 0
    latest_n = _normalize_app_version_string(latest)
    current_n = _normalize_app_version_string(current)
    return latest_n != current_n and latest_n > current_n


def _should_install_selected_release(latest: str, current: str) -> bool:
    cmp = _compare_app_versions(latest, current)
    if cmp is not None:
        return cmp != 0
    return _normalize_app_version_string(latest) != _normalize_app_version_string(current)


def _is_supported_update_version(version: str) -> bool:
    cmp = _compare_app_versions(version, APP_UPDATE_MIN_VERSION)
    if cmp is not None:
        return cmp >= 0
    return str(version or "").strip().lstrip("vV") >= APP_UPDATE_MIN_VERSION


def _pick_release_asset(assets: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    if not assets:
        return None
    sys_name = platform.system().lower()

    def score(asset: Dict[str, object]) -> Tuple[int, int, int, int, int]:
        name = str(asset.get("name", "")).lower()
        pri_binary_ext = 0 if (("." not in name.split("/")[-1]) or name.endswith(".bin")) else 1
        if name.endswith(".appimage"):
            pri_binary_ext = 2
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
    name = str(asset_name or "").strip().lower()
    if not name:
        return False
    if name.endswith(
        (".sh", ".ps1", ".bat", ".cmd", ".msi", ".exe", ".appimage", ".zip", ".tar.gz", ".tgz")
    ):
        return False
    return True


def _release_version_string(release: Dict[str, object]) -> str:
    return str(release.get("tag_name", "")).strip().lstrip("vV")


def _filter_releases_for_channel(releases: List[Dict[str, object]], update_channel: str) -> List[Dict[str, object]]:
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


def _pick_release_by_version(releases: List[Dict[str, object]], selected_version: Optional[str] = None) -> Optional[Dict[str, object]]:
    if not releases:
        return None
    wanted = _normalize_app_version_string(str(selected_version or "").strip().lstrip("vV"))
    if wanted:
        for rel in releases:
            if _normalize_app_version_string(_release_version_string(rel)) == wanted:
                return rel
    return releases[0]


def fetch_latest_app_release_info(update_channel: str = "release", selected_version: Optional[str] = None) -> Dict[str, object]:
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
    channel_releases = [rel for rel in channel_releases if _is_supported_update_version(_release_version_string(rel))]
    wanted = str(selected_version or "").strip().lstrip("vV")
    if wanted and not _is_supported_update_version(wanted):
        raise RuntimeError(
            f"Selected version {wanted} is below the minimum supported in-app update target ({APP_UPDATE_MIN_VERSION})."
        )
    if not channel_releases:
        if channel == "prerelease":
            raise RuntimeError(f"No supported pre-release found (minimum in-app target is {APP_UPDATE_MIN_VERSION}).")
        raise RuntimeError(f"No supported release found (minimum in-app target is {APP_UPDATE_MIN_VERSION}).")

    latest_rel = channel_releases[0]
    selected_rel = _pick_release_by_version(channel_releases, selected_version)
    if not isinstance(selected_rel, dict):
        raise RuntimeError("Unable to select app release version.")

    latest_for_channel = _release_version_string(latest_rel)
    selected_release_version = _release_version_string(selected_rel)
    release_url = str(selected_rel.get("html_url", ""))
    assets = selected_rel.get("assets", []) or []
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
