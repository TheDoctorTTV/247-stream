# Rust migration

This branch ports the Python 2.1.1 runtime to native Rust. It does not integrate
pipr or ffplayout, and is not a fix for the known persistent-bridge audio problem.
The version remains 2.1.1 for now; assign a new version in Cargo.toml before
publishing a Rust release.

## Source map

| Previous module | Rust module | Responsibility |
| --- | --- | --- |
| Stream247.py / __main__.py | src/main.rs | CLI, data-directory selection and shutdown |
| config.py / models.py / constants.py | src/config.rs / Cargo.toml | JSON compatibility, settings normalization, stream settings, version |
| runtime.py | src/runtime.rs | Worker ownership and application lifecycle |
| web.py | src/web.rs / src/state.rs | Existing HTTP routes, dashboard state, logging |
| worker.py | src/worker.rs | yt-dlp, playlist refresh/shuffle/prefetch, relay, FFmpeg and bridge |
| updates.py | src/updates.rs | Tool provisioning and GitHub channel updates |
| utils.py | src/process.rs / src/updates.rs / src/state.rs | Process groups, downloads and rotating logs |
| qtcompat.py / shared.py | Removed | Rust uses explicit ownership and module imports |

The existing HTML/CSS/JavaScript is embedded at compile time with include_str!
and include_bytes!. Editing those assets requires rebuilding the binary.
No Python process or Python compatibility wrapper is used by the new runtime.
FFmpeg and yt-dlp remain separate executables.

## Compatibility and intentional differences

- The config.json field names and dashboard routes/response envelopes remain.
  Unknown existing config fields are retained when saving dashboard settings.
- Packaged builds use the executable directory for settings, tools, title text
  and logs. Cargo development builds use the current directory. --data-dir can
  override this. Invalid existing JSON produces a startup error rather than
  silently resetting configuration.
- As in main's Python version, non-YouTube RTMP playlist output uses the persistent
  MPEG-TS bridge; YouTube output obeys youtube_persistent_output. This preserves
  the pre-beta behavior, including its unresolved timestamp/audio limitations.
  Twitch and direct HLS relays stop when their source ends or fails.
- Settings apply to the next stream start. Stop/skip and shutdown use owned
  subprocesses and Unix process groups, with graceful termination then kill.
- Browser-cookie authentication retains browser, profile, Flatpak/Snap and
  Chromium keyring fallbacks. Prefetching still resolves next-item URLs ahead of
  playback; playback-time URL refreshing is future work.
- Console buckets hold 100 lines. FFmpeg progress supports both CR and LF framing.
  Logs use central redaction for saved/active keys, credential labels, headers and
  URL credentials/paths/queries. Independent logs/app.log and logs/ffmpeg.log sinks
  rotate by size/session and compress closed archives with fast gzip. Debug
  logging can be switched live; see LOGGING.md.
- Binary and release status checks run in background jobs rather than blocking
  HTTP handlers. App jobs distinguish checks, downloads, installation and completion;
  status polling never initiates an app operation. Only newer selected releases are
  update candidates, including automatic installation. Older releases are never
  installed; same-version reinstalls remain an explicit manual action. The selected
  version/channel are persisted for startup checks. Dashboard reloads require an
  observed installation or restart, never merely a completed check. Initial status can temporarily report running while checking.
  Tool installation is refused while streaming, to avoid replacing active tools.
- Downloads are staged and atomically renamed. The configured 50 MB/s cap remains;
  HTTP range-parallel downloading was not carried over. A previous working tool
  is kept when its replacement fails. FFmpeg runtime startup/encoder probes remain
  part of provisioning and encoder selection, as in the Python runtime.
- Application updates support release/prerelease selection, same-version reinstall,
  executable permission setting and systemd restart handoff. Cargo development
  builds download updates for manual installation and do not self-replace.
- The build script now uses Cargo and writes releases/stream247-server. Packaging
  keeps the existing archive asset name and includes an explicit file list, never
  runtime configuration, keys, logs, or downloaded tools.

## Build

Use a stable Rust toolchain and Linux build dependencies (a linker, pkg-config,
and liblzma development headers; on Debian: build-essential pkg-config liblzma-dev).
Cargo.lock pins dependencies for the application. The build script uses --locked.

```bash
./build_linux.sh
./package_linux_release.sh
```

For local development:

```bash
cargo run --bin stream247-server
```

Build in your Debian Distrobox for your deployment baseline. Do not set CPU-native
RUSTFLAGS for distributable releases. This branch does not attempt cross-distro
or cross-CPU portability verification.

## Validation status

No cargo check, compilation, application execution, tests, live streaming, or
release packaging has been run for this migration, at the user's request.
Source formatting and manual review do not establish buildability or behavioral
parity. Before releasing, compilation and runtime checks are still required,
including existing-config loading, dashboard operations, subprocess shutdown,
updates, a playlist transition and a relay source ending. Audio continuity work
should follow as a separately reviewable change after migration validation.
