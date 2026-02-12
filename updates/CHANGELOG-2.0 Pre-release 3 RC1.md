# Stream247 v2.0 Pre-release 3 (Release Candidate 1) Changelog

## Compared to v2.0 Pre-release 2
This release candidate focuses on transition stability (especially Owncast), dashboard UX cleanup, and maintainability improvements.

## Added
- Persistent RTMP bridge mode for non-YouTube RTMP/RTMPS targets to keep a continuous ingest session across playlist item changes.
- Keepalive-slate injection on item-level failures (for example yt-dlp fetch failures) to reduce ingest idle drops while skipping to the next item.
- Destination-aware runtime helpers for RTMP behavior tuning (YouTube-safe vs low-latency non-YouTube behavior).
- Static web asset structure under `web/`:
  - `web/index.html`
  - `web/style.css`
  - `web/app.js`

## Changed
- Playlist transition handoff was optimized:
  - Better prefetch handoff and consumption of in-flight prefetch results.
  - Reduced unnecessary inter-item delays.
  - Retry timing now adapts better for non-YouTube ingest destinations.
- RTMP live protocol options are now automatic (enabled by default, then disabled for the session if connection startup fails), and the dashboard toggle was removed.
- Non-YouTube RTMP preflight probe is skipped to avoid extra start/end session churn on platforms like Owncast.
- About tab update status UI now uses compact dot indicators instead of full text color switching.
  - App + binaries statuses now expose clear state signals (updated, outdated, just-updated, downgrading, running, error, unknown).
- About tab update controls and status layout were simplified to reduce visual crowding and improve readability.
- Linux build script now bundles the `web/` asset directory into PyInstaller output.

## Removed
- Dropped Windows support for this release cycle.
- Distribution/build targets are now Linux-focused.

## Fixed
- Removed Python `SyntaxWarning` caused by unescaped JS regex escapes in embedded dashboard script content.
- Improved queue transition reliability where prefetch completion timing previously caused avoidable fallback fetches.

## Operational Notes
- For non-YouTube RTMP targets (for example Owncast), stream continuity is now prioritized via the persistent bridge path.
- On item fetch/stream errors, runtime injects a short keepalive slate before continuing to the next item.

## Release Candidate Notes
- This is **Release Candidate 1** for **v2.0 Pre-release 3**.
- Primary validation focus: long-running queue transitions, skip behavior, and failure recovery on Owncast-style ingest servers.
