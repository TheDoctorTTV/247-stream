# Stream247 v2.1.1 Changelog

## Summary
v2.1.1 changes relay-session behavior so incoming live relays stop cleanly when the upstream stream ends, instead of auto-reconnecting.

## Changed
- Relay mode for Twitch URL and direct HLS `.m3u8` sources no longer auto-reconnects after source end.
- Relay mode now stops the worker session when the source stream ends.
- Relay mode now stops the worker session on source stream errors instead of retrying in a reconnect loop.

## Notes
- This behavior applies to relay-style live inputs (`twitch_stream`, `direct_hls`) only.
- YouTube playlist/video loop behavior is unchanged.
