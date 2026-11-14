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
| `remember_current_video` | Restart the last playing YouTube video from the beginning on the next stream start (default: off) |

Recovery and transition media are uploaded through Settings. See
[source recovery](docs/SOURCE_RECOVERY.md) for supported formats and behavior.

### Remember current video

Enable **Settings → Playback & preferences → Remember current video** to save the playing
YouTube video per source in `config.json`. Stopping the stream or restarting the
application then replays that video from the beginning. Playlist playback continues
from that item; with shuffle enabled, the saved video plays first and the remaining
items follow in shuffled order. The saved starting point is applied only at stream
startup, not on every playlist loop. If the video is no longer listed, playback
starts normally; unavailable videos follow the usual error/skip behavior.

Playlist and stream key selections are always saved. Local Debug previews
neither use nor overwrite the saved video. Twitch and HLS relays are unaffected.
Disabling the option ignores saved videos and stops updating them; enabling it
again reuses the last saved video for that source. No playback timestamp is saved.

### Dashboard and updates

| Setting | Purpose |
| --- | --- |
| `web_server_host` | Dashboard bind address; default `0.0.0.0` |
| `web_server_port` | Dashboard port; default `7788` |
| `theme` | Theme identifier from the table below |
| `app_update_channel` | `release` or `prerelease` |
| `app_update_selected_version` | Concrete version selected by the last successful check |
| `app_update_check_minutes` | Check interval after startup: `0` (Never), `30`, `60`, `360`, `720`, `1440`, or `2880` |

The app checks for updates once on every startup, including when **Checks after
startup** is set to **Never**. Later checks follow the selected interval; Never
disables recurring checks. Refreshing the dashboard does not start a check.
Checks report available updates and never install them. Use **Install**,
**Reinstall** or **Downgrade** to explicitly install the version selected in the
menu. These actions are blocked while streaming or running a local preview.
The update status always compares the running version with the highest supported
version in the selected channel, regardless of the version selected for installation.

The old `check_updates_startup` preference maps to hourly recurring checks when
enabled, or no recurring checks when disabled. App and tool downloads have no
application-imposed speed cap. Local Cargo builds and unpublished versions can
check for updates and manually install a supported published release. The removed
`auto_app_updates` setting is ignored even if it remains in an older config.

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

### YouTube playback

YouTube playback uses yt-dlp without authentication. When YouTube reports that a
video requires sign-in or account access (such as private, age-restricted, or
members-only content), Stream247 logs the reason, skips that video, and moves to
the next playlist item without retrying other formats. If every item requires
authentication, it waits 30 seconds before refreshing the source.

Browser pairing and cookie uploads are no longer supported. Previously saved
files in `youtube-auth/` are unused and can be deleted from the server data directory.

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
