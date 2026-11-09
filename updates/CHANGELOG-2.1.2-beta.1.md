# Stream247 v2.1.2-beta.1 Changelog

## Fixed
- Avoid the persistent MPEG-TS bridge for YouTube playlists and looping single videos. Each item now uses its own direct RTMP connection, preventing timestamp corrections from carrying between independent item timelines (issue #38).
- Import `tarfile` explicitly so Linux FFmpeg updates can extract downloaded archives.

## Beta behavior
- This is a mitigation for transition-related audio desync and silence; it does not implement seamless timestamp stitching.
- RTMP reconnects at each item boundary, including skips and single-video loops. Viewers may see buffering or a brief interruption, and ingest services may split sessions depending on their disconnect settings.
- Saved `youtube_persistent_output` settings do not re-enable the bridge for looping inputs.
- Twitch and direct HLS relay behavior is unchanged.
- No tests or builds were run for this beta change. Live audio recovery remains to be verified.
