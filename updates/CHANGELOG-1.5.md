# Update V1.5 Changelog

### Added
- AppImage build tooling (`appimage/` with `AppRun`, desktop entry, build script) and icon generation.
- Linux AppImage instructions in README.
- Auto‑download for `yt-dlp` and `ffmpeg` on Linux.
- Linux encoder detection for VAAPI/QSV.
- Fallback to system `ffmpeg` on Linux if the bundled binary crashes.
- AppImage runtime config path under `~/.config/Stream247`.
- Bundling of `libtiff.so.5` and `libxcb-cursor.so.0` into AppImage builds.

### Changed
- Version bumped to `1.5`.
- RTMP preflight uses software `libx264` and has improved error reporting.
- Console UI switched to `QPlainTextEdit` with a bounded buffer for stability.
- AppImage forces `QT_QPA_PLATFORM=xcb` at runtime.

### Fixed
- AppImage read‑only runtime issues for config/log file writes.
- Preflight errors now surface meaningful messages on failure.

### Notes
- Tested on Linux
