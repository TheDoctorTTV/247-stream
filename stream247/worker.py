from .shared import *

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
        text = self._redact_sensitive((line or "").rstrip())
        if not text:
            return
        if text.startswith("frame="):
            now = time.monotonic()
            with self._ffmpeg_stats_lock:
                if now - self._last_ffmpeg_stats_emit < self.FFMPEG_STATS_EMIT_INTERVAL:
                    return
                self._last_ffmpeg_stats_emit = now
        self.log.emit(text)

    def _redact_sensitive(self, text: str) -> str:
        """Redact secrets from logs (primarily stream keys embedded in URLs)."""
        out = str(text or "")
        key = (self.cfg.stream_key or "").strip()
        if key:
            out = out.replace(key, "[REDACTED_STREAM_KEY]")
        return out

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

    def _ffmpeg_smoke_test(self, ffmpeg_path: Optional[str], timeout_sec: float = 8.0) -> Tuple[bool, str]:
        """Run a tiny transcode probe to detect broken/segfaulting ffmpeg binaries."""
        if not ffmpeg_path:
            return (False, "ffmpeg path missing")
        cmd = [
            ffmpeg_path,
            "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=black:s=160x90:r=1",
            "-f", "lavfi", "-i", "anullsrc=cl=stereo:r=44100",
            "-t", "0.40",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
            "-f", "null", "-",
        ]
        try:
            cp = run_hidden(cmd, capture=True, timeout=timeout_sec)
            if cp.returncode == 0:
                return (True, "")
            err = (cp.stderr or cp.stdout or "").strip()
            return (False, err or f"exit code {cp.returncode}")
        except subprocess.TimeoutExpired:
            return (False, "smoke test timed out")
        except Exception as e:
            return (False, f"smoke test exception: {e}")

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

    def _safe_rtmp_url(self) -> str:
        """Return RTMP destination string with stream key redacted."""
        return self._redact_sensitive(self.cfg.rtmp_url())

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
        local_ffmpeg_healthy = False
        if local_ffmpeg.exists():
            ok_local, err_local = self._ffmpeg_smoke_test(str(local_ffmpeg))
            local_ffmpeg_healthy = ok_local
            if not ok_local:
                self.log.emit(f"[WARN] Local ffmpeg failed self-check and will be refreshed: {err_local}")
        need_ffmpeg_download = bool(force_ffmpeg) or (not local_ffmpeg.exists()) or (not local_ffmpeg_healthy)
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
                    ok_dl, err_dl = self._ffmpeg_smoke_test(str(ffmpeg_bin_path))
                    if not ok_dl:
                        raise RuntimeError(f"Downloaded ffmpeg failed self-check: {err_dl}")
                    local_ffmpeg_healthy = True
                    self.ffmpeg_path = str(ffmpeg_bin_path)
                    self.log.emit("[INFO] FFmpeg downloaded and ready")
                    _emit_progress("Installed FFmpeg.", 95)
            except Exception as e:
                self.log.emit(f"[WARN] Could not auto-refresh local ffmpeg: {e}")
                # Repair path: if system ffmpeg is healthy, copy it into the local bundle path.
                if sys_ffmpeg:
                    ok_sys, err_sys = self._ffmpeg_smoke_test(sys_ffmpeg)
                    if ok_sys:
                        try:
                            if (not local_ffmpeg.exists()) or (Path(sys_ffmpeg).resolve() != local_ffmpeg.resolve()):
                                shutil.copy2(sys_ffmpeg, local_ffmpeg)
                            os.chmod(local_ffmpeg, 0o755)
                            local_ffmpeg_healthy = True
                            self.ffmpeg_path = str(local_ffmpeg)
                            self.log.emit(f"[INFO] Repaired local ffmpeg from system binary: {sys_ffmpeg}")
                            _emit_progress("Installed FFmpeg from system fallback.", 95)
                        except Exception as copy_err:
                            self.log.emit(f"[WARN] Failed to copy system ffmpeg into local bundle: {copy_err}")
                    else:
                        self.log.emit(f"[WARN] System ffmpeg failed self-check: {err_sys}")
                if not local_ffmpeg_healthy:
                    self.log.emit(
                        f"[ERROR] Could not provision a healthy local FFmpeg. Please place a working {ffmpeg_local_name} next to the app or install FFmpeg in PATH."
                    )
        else:
            _emit_progress("FFmpeg already present.", 95)
        if local_ffmpeg.exists() and not local_ffmpeg_healthy:
            ok_local, _err_local = self._ffmpeg_smoke_test(str(local_ffmpeg))
            local_ffmpeg_healthy = ok_local
        # Prefer local copy if available
        if local_ffmpeg.exists() and local_ffmpeg_healthy:
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
                    self.log.emit(f"[ERROR] RTMPS preflight failed: {self._redact_sensitive(err2)}")
            except Exception as e2:
                self.log.emit(f"[WARN] RTMPS fallback error: {e2}")

            if rc < 0:
                self.log.emit(f"[WARN] RTMP preflight crashed ({rc}); skipping preflight.")
                return True
            self.log.emit(f"[ERROR] RTMP preflight failed: {self._redact_sensitive(err)}")
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
                        "Browser cookie database is locked by a running browser instance.\n"
                        "Close browser windows (including background processes) and try again.\n\n"
                        "Advanced: Launch the browser with --disable-features=LockProfileCookieDatabase to prevent locking.\n"
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
                self.log.emit("[WARN] Cookies locked by browser; close the browser and retry (see issue #7271)")
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
        if not out_url.startswith(("rtmp://", "rtmps://")):
            return False
        if self._is_youtube_rtmp():
            return bool(self.cfg.youtube_persistent_output)
        return True

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
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "copy",
            # Re-time audio in the bridge to prevent FLV non-monotonic DTS spam.
            "-af", "aresample=async=1:first_pts=0",
            "-c:a", "aac", "-b:a", self.cfg.audio_bitrate, "-ar", "44100", "-ac", "2",
        ]
        used_rtmp_live_opts = self._use_rtmp_live_protocol_opts(out_url)
        if used_rtmp_live_opts:
            cmd += ["-rtmp_live", "live", "-rtmp_tcurl", self.cfg.rtmp_base]
        cmd += ["-f", "flv", out_url]

        read_fd, write_fd = os.pipe()
        self.log.emit(f"[CMD] ffmpeg (bridge): {self._redact_sensitive(' '.join(cmd))}")
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
            try:
                keepalive_height = int(self.cfg.height)
            except Exception:
                keepalive_height = 360
            keepalive_height = max(360, keepalive_height)
            if keepalive_height % 2:
                keepalive_height += 1
            keepalive_width = int(round(keepalive_height * 16 / 9))
            if keepalive_width % 2:
                keepalive_width += 1
            keepalive_size = f"{keepalive_width}x{keepalive_height}"
            keepalive_cmd = [
                self.ffmpeg_path or "ffmpeg",
                "-hide_banner", "-loglevel", "warning", "-nostdin",
                "-re", "-f", "lavfi", "-i", f"color=black:s={keepalive_size}:rate={self.cfg.fps}",
                "-f", "lavfi", "-i", "anullsrc=cl=stereo:r=44100",
                "-t", f"{duration:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
                "-muxdelay", "0", "-muxpreload", "0",
                "-mpegts_flags", "+initial_discontinuity+resend_headers",
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

        # Tighten output for YouTube ingest with stable, CBR-like rate control.
        if self._is_youtube_rtmp():
            cmd += ["-minrate", self.cfg.video_bitrate]
            if self.cfg.encoder == "libx264":
                cmd += ["-x264-params", "nal-hrd=cbr:force-cfr=1"]

        if to_pipe:
            cmd += [
                "-muxdelay", "0",
                "-muxpreload", "0",
                "-mpegts_flags", "+initial_discontinuity+resend_headers",
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
            self.log.emit(f"[CMD] ffmpeg: {self._redact_sensitive(' '.join(ff_cmd))}")
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
            self.log.emit(f"[CMD] ffmpeg: {self._redact_sensitive(' '.join(ff_cmd))}")
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
        self.log.emit(f"[INFO] RTMP:     {self._safe_rtmp_url()}")
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
