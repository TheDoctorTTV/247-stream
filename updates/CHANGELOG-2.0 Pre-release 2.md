# Stream247 v2.0 Pre-release 2 Changelog

## Compared to v2.0 Pre-release 1
This release includes app/runtime updates plus repo/build cleanup.

## Added
- App update check endpoint and flow in the web dashboard (`/api/app-update/check` + UI action).
- App update channel selection (`release` or `prerelease`) and startup auto-update option for packaged builds.
- Self-install/update-and-restart flow for packaged builds (manual install guidance remains for source runs).
- Binary update progress reporting in runtime state (`progress_percent`, `progress_message`) and dashboard progress UI.
- Favicon/icon serving from the local web dashboard (`/favicon.ico`, `/icon.ico`).

## Changed
- Standardized the main app entrypoint filename from `Stream247_GUI.py` to `Stream247.py`.
- Updated build script to target the new entrypoint by default (`build_linux.sh`).
- Build scripts now include `icon.ico` in PyInstaller builds when present.
- Build scripts no longer copy `config.json` to `dist/config.json.example`.
- Web settings now normalize bitrate using bounded steps (1500 to 25000 kbps, 500-step increments).
- `bufsize` is now derived from selected buffer mode and bitrate instead of being manually entered.
- Dashboard stream settings now auto-save with debounce and duplicate-save suppression.
- App/binary update status messaging in the dashboard was expanded for clearer state and availability reporting.
- Headless file logging now follows `log_to_file` config rather than always writing logs.
- Default update download cap handling was raised to 25 Mbps in normalized settings.
- README was rewritten to match the current headless/web-dashboard workflow and current runtime/config fields.

## Removed
- Removed AppImage-specific build tooling and related repo assets (`appimage/AppRun`, `appimage/Stream247.desktop`, `appimage/build_appimage.sh`).
- Removed AppImage-specific ignore entries from `.gitignore`.
- Removed legacy desktop GUI code paths from the main app file, leaving web-dashboard runtime as the primary maintained path.

## Notes
- Runtime remains web-dashboard first: start the app, then control streaming from the browser dashboard.
- This release continues the 2.0 direction toward headless/web-first operation and updater reliability.
