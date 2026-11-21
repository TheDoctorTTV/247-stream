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

Recovery and transition media are uploaded through Settings; see
[Recovery and transitions](#recovery-and-transitions).

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

The app checks for updates once on every startup, later checks follow the selected interval; Never
disables recurring checks. Checks report available updates and never install them. Use **Install**,
**Reinstall** or **Downgrade** to explicitly install the version selected in the
menu. These actions are blocked while streaming or running a local preview.

Installation replaces the executable and restarts the application. FFmpeg and yt-dlp have a separate tool updater.
FFmpeg provisioning requires version 8 or newer and checks downloaded binaries
before replacing the existing executable.

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

### Twitch Enhanced Broadcasting

Select Twitch on the Stream tab and enable **Twitch Enhanced Broadcasting**.
Maximum Video Tracks and Maximum Streaming Bandwidth can be Auto or manual caps;
Twitch selects the output configuration within those limits. Configuration keys
are `twitch_enhanced_broadcasting`, `twitch_enhanced_max_tracks`, and
`twitch_enhanced_max_bitrate_kbps` (`0` means Auto for either limit).

Requires FFmpeg 8+ and enough CPU/GPU capacity to encode the negotiated renditions.
Supports up to six H.264 SDR video renditions with one AAC audio track. The output
connection and encoders stay active through playlist transitions and source recovery.

### Background service

To start the installed user service at boot without logging in:
`sudo loginctl enable-linger "$USER"`.

## Recovery and transitions

Upload separate **Recovery media** and **Transition media** in Settings:
PNG/JPG/WebP images, GIF, MP4 or WebM, up to 10 MiB each. Media fits the output
with black padding; 1920 × 1080 is recommended. Recovery media loops silently
during interruptions; without it, fallback is black video with silence. Initial
startup waits for actual source audio and video before opening the output.

Playlist transitions play between videos while the next source prepares.
Images/GIFs display for at least five seconds; videos play at least once with
their audio and loop if preparation takes longer. Without transition media,
recovery fallback fills the handoff. Stream-tab Skip bypasses the remaining
transition. Live relays have no between-item transitions.

Source errors or eight seconds without audio/video progress trigger recovery
while persistent output stays connected. Finite YouTube videos resume near the
last common audio/video checkpoint; live sources reconnect at the live position.
After three failed recovery attempts, playlist playback skips the item; live
relays retry after a cooldown. Recovery and transitions require persistent output.

## Debug

Enable **Settings → Debug** for the Debug tab and extra diagnostic logging.
**Start local test** previews the selected source using the stream settings and
recovery/transition media, without publishing or requiring a destination key.
The HLS preview starts muted, buffers a few seconds, and automatically reconnects
after playback errors or stalls. It uses rolling media segments in
`debug-preview/`, not a complete recording. Stop an active broadcast before
starting a local test; **Stop test** ends the preview.

**Skip to transition** advances a local playlist item through its transition.
Failure controls simulate audio/video loss, read errors, source stalls and source
exits; they can also disrupt the source of a running broadcast. Source/output
PIDs and the recovery count show recovery progress. Turning Debug off hides the
tab and detaches playback; it does not stop an active test or broadcast.
Local preview uses one output encoder and does not connect to Twitch's Enhanced
Broadcasting service or a streaming destination.

## Logging

The Console tab displays application and FFmpeg output. File logging is optional;
paths below are relative to the application data directory.

| Setting | Effect |
| --- | --- |
| `log_to_file` | Save application output to `logs/app.log` |
| `ffmpeg_log_to_file` | Save FFmpeg output to `logs/ffmpeg.log` |
| `debug_logging` | Add system, source, codec, and timing diagnostics |

These settings apply immediately. Debug adds system, codec, process and per-track
timing information to the terminal and live console; it does not enable file
saving. With both save switches off, Stream247 writes no new log entries to disk;
existing logs remain, and external services may separately capture terminal output.
Recovery monitoring remains active with Debug off.

Active logs are plain text and rotate at 4 MiB (app) or 16 MiB (FFmpeg), and at
session boundaries. Archives use gzip, retaining the newest 32 per log type.
Credentials and sensitive URL details are redacted before console or file output.
