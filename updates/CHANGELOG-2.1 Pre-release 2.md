# Stream247 v2.1 Pre-release 2 Changelog

## Summary
This pre-release focuses on app-update reliability and dashboard UX improvements.

## Added
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

## Changed
- App update backend now reports staged progress through:
  - release check
  - download
  - install/restart
- App update frontend now refreshes the dashboard automatically after successful update completion.
- Added fallback auto-refresh behavior if update was running and status polling repeatedly fails during restart windows.

## Fixed
- Fixed app update error: `name 'os' is not defined`.
- Improved app update button/status behavior while update jobs are running/completing.
- Reduced stale state issues after update completion by forcing a clean dashboard reload.

## Notes
- Version updated to `2.1-pre-release-2`.
- This is a pre-release build intended for verification before stable `2.1`.
