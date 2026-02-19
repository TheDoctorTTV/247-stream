# Stream247 v2.0.1 Changelog

## Summary
v2.0.1 focuses on YouTube ingest reliability, safer logging, and smoother playback during persistent RTMP bridge transitions.

## Added
- YouTube persistent output control via `youtube_persistent_output` (enabled by default).
- Stream-key redaction helpers for runtime logs and command output.
- FFmpeg health smoke test for local/system binary validation.
- Automatic local FFmpeg self-repair path:
  - Refresh unhealthy local bundled FFmpeg.
  - Validate downloaded FFmpeg before use.
  - Copy in a healthy system FFmpeg as local fallback when refresh fails.

## Changed
- YouTube output tuning now adds stronger CBR-style rate control:
  - `-minrate` follows selected video bitrate.
  - `libx264` path adds `nal-hrd=cbr:force-cfr=1`.
- YouTube ingest can now use the persistent RTMP bridge when enabled.
- FFmpeg/RTMP logging now redacts stream keys in:
  - RTMP destination status line.
  - FFmpeg command logs.
  - FFmpeg output/error lines passed through runtime logger.
- Persistent bridge remuxing behavior updated for smoother playback:
  - Audio is normalized in bridge output using async resampling.
  - Bridge output keeps explicit video/audio mapping and stable AAC output.

## Fixed
- Reduced repeated timestamp/DTS instability that caused rough playback after source/item transitions.
- Reduced risk of accidental stream-key exposure in console/file logs.
- Improved startup resilience when bundled FFmpeg is unstable by auto-falling back to healthy binaries.

## Notes
- On some hosts, first-run local FFmpeg can still fail before fallback; runtime now self-heals and switches to a healthy binary.
- `Immediate exit requested` and FLV header warnings at manual stop are expected for interrupted live outputs.
