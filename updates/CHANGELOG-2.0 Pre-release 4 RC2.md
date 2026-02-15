# Stream247 v2.0 Pre-release 4 (Release Candidate 2) Changelog

## Compared to v2.0 Pre-release 3 (Release Candidate 1)
This release candidate focuses on multi-source stream selection in the web dashboard, config normalization for source handling, and additional RTMP resiliency behavior refinements.

## Added
- New **Sources** tab in the web dashboard with:
  - Single source input box + **Add** action.
  - Source list UI with per-item **Remove** action.
  - Newest-to-oldest source ordering.
- Multi-source configuration support via `sources` list in settings/config payloads.
- Stream-tab source selector dropdown populated from available sources.

## Changed
- Stream settings now resolve the active source from a normalized source list:
  - If exactly one source exists, it is auto-selected.
  - If multiple sources exist, the selected source must be one of the configured entries.
  - Legacy single `playlist_url` configs are auto-migrated in-memory to a one-item source list for compatibility.
- Dashboard source input UX has been reworked:
  - Replaced free-text source field on Stream tab with a dropdown selector that always shows the active source.
  - Selector is disabled when there are 0-1 sources, enabled when there are 2+ sources.
  - Added contextual hint text for no-source, single-source, and multi-source states.
  - Source updates auto-save with the same debounce behavior as other settings.
- Web settings payload normalization now returns both `sources` and resolved `playlist_url` values.
- Runtime stream-config building now uses resolved source selection logic so start behavior matches dashboard selection rules.
- Dashboard styling was refreshed for a more modern and cohesive look:
  - Unified spacing, radius, and surface tokens.
  - Improved hover/focus states for tabs, buttons, and form controls.
  - Cohesive styling for source-list rows and actions.

## Fixed
- Avoided inconsistent start behavior when `playlist_url` did not match available configured sources by forcing a deterministic fallback to a valid source.
- Reduced duplicate/invalid source entries through trim + de-dup normalization before save/use.
- Fixed Stream-tab source selector visibility/interaction edge cases when only one source is configured.

## Release Candidate Notes
- This is **Release Candidate 2** for **v2.0 Pre-release 4**.
- Primary validation focus: source-management UX, selection persistence, and start-stream behavior across zero/one/many source configurations.
