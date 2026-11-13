# Stream247 documentation

## Configuration

Configure Stream247 through the dashboard. Settings are stored in `config.json`
beside the packaged executable, or in the current directory when using
`cargo run`. Use `--data-dir PATH` to choose another data directory.

### Sources and output

| Setting | Purpose |
| --- | --- |
| `sources`, `source_names`, `playlist_url` | Saved sources, labels, and selected source |
| `stream_keys`, `stream_key_names`, `stream_key` | Saved stream keys, labels, and selected key |
| `stream_url_services`, `rtmp_base` | Saved platforms and selected RTMP/RTMPS ingest |
| `resolution` | `480p`, `720p`, `1080p`, `1440p`, or `2160p` |
| `framerate` | `30` or `60` FPS |
| `video_bitrate` | 1500–25000 kbps, in steps of 500 |
| `buffer_mode` | `Low`, `Medium`, `High`, or `Ultra` |
| `encoder_preference` | `auto`, `libx264`, `h264_nvenc`, `h264_qsv`, `h264_amf`, or `h264_vaapi` |
| `overlay_titles`, `shuffle` | Title overlays and playlist shuffle |
| `remember` | Remember source and stream key selections |

Recovery and transition media are uploaded through Settings. See
[source recovery](docs/SOURCE_RECOVERY.md) for supported formats and behavior.

### Dashboard and updates

| Setting | Purpose |
| --- | --- |
| `web_server_host` | Dashboard bind address; default `0.0.0.0` |
| `web_server_port` | Dashboard port; default `7788` |
| `theme` | Theme identifier from the table below |
| `app_update_channel` | `release` or `prerelease` |
| `app_update_selected_version` | Selected version; empty follows the latest in the channel |
| `app_update_check_minutes` | Automatic check interval: `0` (Never), `30`, `60`, `360`, `720`, `1440`, or `2880` |
| `auto_app_updates` | Automatically install newer app versions |

Automatic checks run on an app timer. Refreshing the dashboard does not start a
check. The old `check_updates_startup` preference maps to hourly checks when
enabled, or Never when disabled. Update downloads are capped at 50 Mbps.

| Theme | Identifier |
| --- | --- |
| Amber | `amber` |
| Amethyst | `purple` |
| Cobalt | `blue` |
| Ember | `orange` |
| Evergreen | `emerald` |
| Graphite | `mono` |
| Lagoon | `teal` |
| Rose Quartz | `rose` |
| Ruby | `red` |

### YouTube authentication

Choose **Connect browser** in **Settings → YouTube authentication**, then enter
the displayed address and pairing code in the **Stream247 YouTube Connect**
Chrome extension. Click its toolbar icon to open the connection page in a new tab.
Sign in to YouTube in that browser and choose **Pair and sync**. Codes
expire after ten minutes and can be used once; one browser is paired at a time.
The dashboard provides the extension download. See the
[extension setup guide](browser-extension/README.md) for installation and HTTPS setup.

**Automatic sync is off by default.** Enable it in the extension to send changed
YouTube cookies while the browser is running. **Sync now** is always available.
Automatic sync retries connection failures and clears saved cookies after browser
sign-out. **Disconnect** revokes the pairing and removes saved server cookies.
Existing lookups and already resolved media may finish using the old credentials.

Cookies may improve access to restricted or private content available to the
signed-in account. Playback is not guaranteed: expired sessions, YouTube checks,
server restrictions, and additional token requirements can still prevent it.
The extension does not provide PO tokens or bypass account permissions.

Cookies grant account access and are stored unencrypted with owner-only permissions
in `youtube-auth/` alongside the server pairing credential. Use a trusted server,
restrict dashboard access, and use HTTPS for connections from another device.
Contents are never served or logged; sensitive values are redacted. Revoke affected
account sessions if exposed—disconnecting alone does not revoke them.

### Additional options

- Twitch Enhanced Broadcasting: `twitch_enhanced_broadcasting`,
  `twitch_enhanced_max_tracks`, and `twitch_enhanced_max_bitrate_kbps`.
  See [Twitch output](docs/TWITCH.md) for requirements and limits.
- To start the installed user service at boot without logging in:
  `sudo loginctl enable-linger "$USER"`.

## Logging

The Console tab displays application and FFmpeg output. File logging is optional;
paths below are relative to the application data directory.

| Setting | Effect |
| --- | --- |
| `log_to_file` | Save application output to `logs/app.log` |
| `ffmpeg_log_to_file` | Save FFmpeg output to `logs/ffmpeg.log` |
| `debug_logging` | Add system, source, codec, and timing diagnostics |

These settings apply immediately. Debug logging does not enable file saving.
Active logs remain plain text; rotated logs use gzip compression.
See [logging details](docs/LOGGING.md) for rotation, retention, and redaction.

## Detailed guides

- [App updates](docs/UPDATER.md)
- [Source recovery and transition media](docs/SOURCE_RECOVERY.md)
- [Twitch output and Enhanced Broadcasting](docs/TWITCH.md)
- [Debug preview and source-failure controls](docs/DEBUG_TESTING.md)
- [Logging and diagnostics](docs/LOGGING.md)
