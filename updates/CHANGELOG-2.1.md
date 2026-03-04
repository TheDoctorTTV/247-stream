# Stream247 v2.1 Changelog

## Summary
v2.1 delivers the modular terminal-first runtime migration, major web dashboard UX and settings improvements, and more reliable update/version handling.

## Added
- New modular package layout under `stream247/`:
  - `constants.py`
  - `config.py`
  - `models.py`
  - `utils.py`
  - `updates.py`
  - `web.py`
  - `worker.py`
  - `runtime.py`
  - `qtcompat.py` (headless signal shim)
- Package entrypoints:
  - `stream247/__init__.py`
  - `stream247/__main__.py`
- Backward-compatibility re-export module:
  - `stream247/shared.py`
- App update progress tracking in runtime state:
  - `progress_percent`
  - `progress_message`
- App update progress UI in the About tab:
  - Visual progress bar
  - Progress text updates during download/install flow
- Dashboard tab persistence:
  - Remembers last selected main tab
  - Remembers last selected console subtab
  - Restores both on page reload
- Stream platform preset selector (`YouTube`, `Twitch`, `Kick`, `Facebook Live`, `TikTok Live`, `Trovo`) with `Custom` URL support.
- Dedicated `Settings` tab for advanced stream/runtime options.
- Theme selector with built-in themes: `Blue`, `Purple`, `Red`, and `Mono`.
- Config-backed theme persistence via settings API (`theme` saved in `config.json`).
- Stream Keys tab for managing multiple saved stream keys.
- Stream key name support in settings payload/config (`stream_key_names`) for friendly labels.
- Stream Platforms tab for managing saved custom RTMP platform services.

## Changed
- Legacy top-level `Stream247.py` is now a thin compatibility launcher to the modular runtime entrypoint.
- Core runtime responsibilities are now separated by domain (config, updates/versioning, worker lifecycle, web control surface).
- Build flow is aligned with the modular architecture while preserving Linux terminal binary behavior.
- App update backend now reports staged progress through release check, download, and install/restart.
- App update frontend now refreshes the dashboard automatically after successful update completion, with fallback auto-refresh during restart windows.
- App update release matching now uses canonical version normalization (including `pre-release` / `prerelease` equivalence and token-aware suffix comparison).
- Dashboard-side version comparison and version dropdown selection now use canonical matching to avoid false update availability.
- Stream tab layout redesigned to a two-column structure with setup fields on the left and controls on the right.
- Top dashboard navigation updated to behave like traditional tabs.
- Advanced stream options moved from Stream tab to Settings tab:
  - Stream Buffer
  - Encoder
  - YouTube auth settings
  - Toggle options (overlay/shuffle/logging/remember)
- Video bitrate input changed from dropdown to free-text input labeled in `kbps`.
- Update download cap UI removed; cap is now hardcoded to `50 Mbps`.
- Dashboard asset caching for `/assets/app.js` and `/assets/style.css` changed to `no-store`.
- Console/log output formatting improved with normalized timestamped lines in dashboard and `latest.log`.
- Console tab retention changed from time-based display to the last `100` lines per console subtab (`App / Other Output`, `FFmpeg Output`).
- Stream settings now use a stream key dropdown that mirrors source selection behavior.
- With exactly one configured stream key, it is auto-selected; with multiple keys, users choose from dropdown.
- Stream Platform selector now includes built-in presets plus saved custom platforms.
- Custom platform add/remove controls moved from Stream settings into the Stream Platforms tab.
- Stop streaming button styling updated to red for clearer destructive-action signaling.

## Removed
- Monolithic single-file application structure as the primary implementation model.
- AppImage-specific runtime directory behavior.
- Desktop-GUI framework dependency fallback path (PySide/Qt import chain), replaced by internal headless signal shim.
- Desktop-target self-install update artifacts from supported in-app install targets (for example `.appimage`, `.exe`).

## Fixed
- Fixed PyInstaller entrypoint regression related to package-relative imports and `__main__.py` execution context.
- Fixed app update runtime errors from missing imports (`name 'os' is not defined`).
- Fixed binary update runtime regression (`name '_app_dir' is not defined`).
- Fixed hidden import gap in worker update flow after modular split.
- Fixed progress bar behavior where percentage text changed but visual fill appeared static.
- Fixed false "Install available" states caused by non-canonical version-string comparison.
- Fixed selected-version mismatch behavior from exact-string tag matching.
- Fixed theme application inconsistency by replacing hardcoded tab/subtab colors with theme variables.
- Fixed theme reset behavior by persisting theme in app config instead of browser-local storage.
- Fixed stale-asset cases where older JS/CSS could override newer dashboard updates.
- Fixed Console tab rendering that showed literal `\n` instead of actual line breaks.
- Fixed `name 'random' is not defined` runtime error.
- Preserved backward compatibility for existing single-key configs by auto-populating `stream_keys` from `stream_key`.
- Preserved backward compatibility for existing custom `rtmp_base` values by auto-importing unknown URLs into saved Stream Platforms.

## Notes
- v2.1 is intentionally terminal-first for Linux server/headless deployment.
- Existing launch workflows remain compatible (`python3 Stream247.py` and built `stream247-server` binary).
- Dashboard console tabs now show the most recent 100 lines per subtab; enabled file logging still retains full-session output.
