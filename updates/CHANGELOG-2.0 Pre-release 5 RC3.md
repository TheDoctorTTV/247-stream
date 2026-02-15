# Stream247 v2.0 Pre-release 5 (Release Candidate 3) Changelog

## Compared to v2.0 Pre-release 4 (Release Candidate 2)
This release candidate focuses on source-management UX improvements, especially for mobile readability and clearer source identification.

## Added
- Optional per-source display names in the **Sources** tab.
- New `source_names` settings mapping (URL -> display name) in web settings payload/config.
- Source-name compatibility handling for object-style `sources` entries containing `{ url, name }`.
- Per-source **Copy URL** action in the Sources list.
- Header source meta now resolves configured source names (not just raw URLs).

## Changed
- Sources UI now shows each configured source as:
  - Source name (or `Unnamed Source` when omitted).
  - URL on a secondary line with truncation.
- Stream-tab source dropdown labels now show the source name when set; unnamed entries show truncated URLs.
- URL truncation behavior is responsive:
  - Shorter truncation on small/mobile screens.
  - Longer truncation on larger screens.
- Source labels are re-rendered on window resize so truncation remains appropriate for the current viewport width.
- App update check behavior in the dashboard was reworked:
  - Automatic check cadence changed to every 10 minutes.
  - Automatic checks are now explicitly tied to the `check_updates_startup` setting.
  - Toggle label changed to **Check for updates automatically**.
  - Toggle moved from Stream settings to the About tab near update controls.

## Fixed
- Prevented long source URLs from overflowing mobile layouts in source list and selector views.
- Preserved backward compatibility for older configs using URL-only `sources` arrays and legacy single `playlist_url`.
- Fixed empty header source text while idle by falling back to selected/configured source when runtime metadata has no active source.

## Release Candidate Notes
- This is **Release Candidate 3** for **v2.0 Pre-release 5**.
- Primary validation focus: source naming persistence, source selection correctness, mobile dashboard rendering, and update-check scheduling behavior.
