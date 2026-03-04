# Stream247 v2.1 Pre-release 4 Changelog

## Summary
This pre-release focuses on post-migration stability checks and fixes for update-related runtime regressions.

## Added
- Additional modular-migration sanity checks across runtime, worker, and updater flows.
- In-process smoke validation for binary update and app update state transitions.

## Changed
- Migration audit confirmed and documented the current terminal-first modular architecture as the active runtime path.
- Maintained existing update/dashboard behavior while hardening import boundaries in update execution paths.

## Fixed
- Fixed binary update runtime regression: `name '_app_dir' is not defined`.
- Fixed hidden import gap in worker update flow by explicitly importing private helper dependencies used after modular split.