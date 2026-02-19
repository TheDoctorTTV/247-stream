# Stream247 v2.1 Pre-release 3 Changelog

## Summary
This pre-release hardens the app-update and dashboard behavior introduced in pre-release 2.

## Added
- Stronger visual progress behavior for dashboard progress bars:
  - Non-zero progress now always shows a visible fill in the bar.
  - Added progress-bar glow styling for clearer movement/feedback.

## Changed
- App update and binary update progress rendering now enforces a minimum visible fill for non-zero values.
- Dashboard update UX now better reflects in-progress and completion states with clearer progress visuals.

## Fixed
- Fixed remaining `name 'os' is not defined` runtime path by restoring missing `os` import in config/runtime environment detection logic.
- Fixed cases where progress percentage text advanced but the progress bar appeared visually static.