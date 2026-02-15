# Stream247 v2.0 Changelog

## Summary
v2.0 Stream247's shift to a web-dashboard-first, Linux-focused runtime with stronger stream resiliency, multi-source management, and clearer logging/update controls.

## Added
- Local web dashboard server and API surface for runtime state, settings, controls, and update flows.
- Headless runtime as the primary execution model.
- Runtime state channels for dashboard consoles (`logs`, `logs_other`, `logs_ffmpeg`).
- App updater flow in dashboard:
  - App update check endpoint and UI action.
  - Release channel selection (`release` / `prerelease`).
  - Startup auto-check support tied to settings.
  - Packaged-build self-install/update-and-restart flow.
- Binary update progress reporting in runtime state and dashboard UI.
- Static dashboard assets under `web/` (`index.html`, `style.css`, `app.js`).
- Multi-source stream configuration:
  - Sources tab for add/remove management.
  - Source selector in Stream tab.
  - Optional per-source display names.
  - Per-source Copy URL action.
- RTMP resiliency for non-YouTube destinations:
  - Persistent ingest bridge mode across playlist item transitions.
  - Keepalive-slate injection on item-level failures.
- Logging controls:
  - `App log` toggle for non-FFmpeg file logging.
  - `Save FFmpeg log` toggle for FFmpeg file logging.

## Changed
- Main entrypoint renamed to `Stream247.py` and Linux build script updated accordingly.
- Build outputs now include `icon.ico` (when present) and `web/` assets.
- Dashboard settings behavior improved:
  - Debounced auto-save with duplicate-save suppression.
  - Bitrate normalization (bounded range + step sizing).
  - `bufsize` derived from buffer mode + bitrate.
- Source handling is normalized and backward-compatible:
  - Legacy single `playlist_url` is migrated in-memory to source-list behavior.
  - Resolved source selection is deterministic across zero/one/many-source setups.
  - Legacy URL-only and object-style source formats remain compatible.
- Stream transition and handoff behavior improved to reduce inter-item delays and fallback fetches.
- RTMP live protocol options are now automatic (enable by default, disable for session on startup failure); manual dashboard toggle removed.
- Non-YouTube RTMP preflight probing is skipped to avoid extra session churn.
- Dashboard About tab update/status UX simplified and status indicators standardized.
- Console retention in dashboard is now time-based (last 30 minutes for app/other/FFmpeg views).
- File logging behavior is split by category (non-FFmpeg vs FFmpeg) instead of a single combined toggle.
- README and operational docs updated for the v2.0 web/headless workflow.

## Removed
- Windows support for the v2.0 cycle.
- AppImage-specific build tooling/assets and related ignore entries.
- Legacy desktop GUI code paths in favor of the maintained web-dashboard runtime.
- Manual RTMP live protocol dashboard toggle.

## Fixed
- Headless `latest.log` creation/rotation reliability.
- Reduced terminal log noise and improved packaged/headless Ctrl+C shutdown behavior.
- Added terminal-state restore on exit (best effort).
- Reduced source-list duplication/invalid-entry issues through trim + de-dup normalization.
- Fixed source selector edge cases for single-source configurations.
- Fixed mobile overflow/readability issues for long source URLs.
- Fixed idle header source fallback when runtime metadata has no active source.
- Prevented dashboard console state from accumulating unbounded historical entries.
- Fixed embedded dashboard script escaping issue that caused Python `SyntaxWarning`.

## Notes
- Dashboard console tabs display only the most recent 30 minutes; file logs retain full-session data for enabled categories.
- For Linux hosts where bundled FFmpeg is unstable, runtime can fall back to system FFmpeg and re-select encoder.
- FLV "Failed to update header" messages on stop are expected for interrupted live outputs and are not encode-failure indicators.
