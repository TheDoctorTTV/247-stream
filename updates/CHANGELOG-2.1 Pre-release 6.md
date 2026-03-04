# Stream247 v2.1 Pre-release 6 Changelog

## Summary
This pre-release focuses on a major web dashboard UX pass, theme-system reliability, and settings persistence consistency.

## Added
- Stream platform preset selector (`YouTube`, `Twitch`, `Kick`, `Facebook Live`, `TikTok Live`, `Trovo`) with `Custom` URL support.
- New dedicated `Settings` tab in the web dashboard for advanced stream/runtime options.
- Theme selector with built-in themes: `Blue`, `Purple`, `Red`, and `Mono`.
- Config-backed theme persistence via settings API (`theme` saved in `config.json`).

## Changed
- Stream tab layout redesigned to a two-column structure:
  - Stream controls (`Start`, `Stop`, `Skip`, `Refresh`) on the right.
  - Primary stream setup fields on the left.
- Top dashboard navigation updated to look and behave more like traditional tabs.
- Moved advanced options out of Stream tab into Settings tab:
  - Stream Buffer
  - Encoder
  - YouTube auth settings
  - Toggle options (overlay/shuffle/logging/remember)
- Video bitrate input changed from dropdown to free text input labeled in `kbps`.
- Update download cap UI removed; cap is now hardcoded to `50 Mbps` across dashboard payload + runtime update flows.
- Dashboard asset caching for `/assets/app.js` and `/assets/style.css` changed to `no-store` to ensure fresh UI updates are always loaded.
- Console/log output formatting improved for readability:
  - Dashboard Console entries now use normalized, timestamped log lines.
  - `latest.log` uses the same readable formatting for saved entries.
- Console tab retention changed from "last 30 minutes" to the last `100` lines per console subtab (`App / Other Output`, `FFmpeg Output`).
- File logging behavior is unchanged for retention: when enabled by toggle(s), `latest.log` still records full-session output (not capped to 100 lines).

## Fixed
- Fixed theme selection not applying consistently across all UI elements by replacing hardcoded tab/subtab colors with theme-variable-driven styling.
- Fixed theme reset behavior by persisting theme in app config instead of browser-local theme storage.
- Fixed stale-asset behavior that could cause older JS/CSS to override recent dashboard theme/layout updates.
- Fixed Console tab rendering that previously displayed literal `\n` sequences instead of actual line breaks.
