# Stream247 v2.0 Pre-release 6 Changelog

## Compared to v2.0 Pre-release 5 (Release Candidate 3)
This pre-release focuses on logging controls and console usability in the web dashboard.

## Added
- New **Save FFmpeg log** toggle in Stream settings.
- New `ffmpeg_log_to_file` config setting for FFmpeg log-file persistence control.
- Console tab helper note: **Showing the last 30 minutes of console logs.**

## Changed
- Renamed **Log to file** toggle to **App log** for clearer behavior.
- Console log retention is now time-based for dashboard tabs:
  - App/Other Output and FFmpeg Output show only the last 30 minutes.
- File logging behavior is now split by toggle:
  - **App log** controls non-FFmpeg log lines.
  - **Save FFmpeg log** controls FFmpeg log lines.
  - If either is enabled, the log file remains active and captures enabled categories.

## Fixed
- Prevented older console entries from accumulating indefinitely in dashboard runtime state.
- Aligned dashboard wording with actual logging behavior to reduce setup confusion.

## Notes
- Console retention limit affects dashboard display only.
- When file logging is enabled, log persistence remains full-session for the selected categories.
