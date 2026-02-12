# Stream247 v2.0 Changelog

## Summary
v2.0 is a major architecture update focused on web-first/headless operation, stronger runtime resilience, cleaner operational logs, and better hardware encoder handling across NVIDIA + Intel Linux hosts.

## Added
- Local web dashboard server with API endpoints for state, settings, control, binary update checks, and app update download flow.
- Headless runtime (`HeadlessRuntime`) as the default execution path.
- Runtime state store with separated log channels (`logs`, `logs_other`, `logs_ffmpeg`) for web dashboard consoles.
- Web settings validation/normalization helpers for API-safe config updates.
- Binary build helper scripts:
  - `build_linux.sh`
- Optional Qt fallback shims for environments without PySide6, improving non-GUI/headless robustness.

## Changed
- App version bumped from `1.5` to `2.0`.
- Primary usage model moved to web dashboard control (start/stop/skip/settings from browser UI).
- README updated for web-based workflow and server build scripts.
- Encoder auto-selection/probing hardened:
  - Better encoder capability detection flow.
  - Linux auto path prioritizes hardware with stronger fallback behavior.
  - Reduced fragile forced encoder flags for wider ffmpeg/driver compatibility.
- ffmpeg command generation updated for safer headless behavior (`-nostdin`) and hardware initialization handling.

## Fixed
- Headless `latest.log` creation/rotation reliability.
- Terminal log noise reduced: runtime no longer streams all log lines to terminal.
- Added one-time terminal startup banner with dashboard URL.
- Improved Ctrl+C shutdown handling to reduce crash traces in packaged/headless runs.
- Added terminal-state restore on exit (best effort) to avoid no-echo shell state after shutdown.
- Log hygiene improvements:
  - `latest.log` now excludes ffmpeg stream/stat output.
  - Dashboard still keeps ffmpeg output in FFmpeg console channel.

## Operational Notes
- On Linux hosts where bundled ffmpeg is unstable, runtime can fall back to system ffmpeg and re-select encoder.
- FLV "Failed to update header" messages on stop are expected for interrupted live outputs and are not encode-failure indicators.

## Files changed vs main (local build)
- Modified: `Stream247_GUI.py`
- Modified: `README.md`
- Added: `build_linux.sh`
