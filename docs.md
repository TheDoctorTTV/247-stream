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
| `stream_url_services`, `rtmp_base` | Saved platforms and selected RTMP/RTMPS ingest; `local://output` selects [streaming software output](#streaming-software-output) |
| `local_output_address` | Server address used in the streaming software URL; empty selects the first private address |
| `resolution` | `480p`, `720p`, `1080p`, `1440p`, or `2160p` |
| `framerate` | `30` or `60` FPS |
| `video_bitrate` | 1500–25000 kbps, in steps of 500 |
| `buffer_mode` | `Low`, `Medium`, `High`, or `Ultra` |
| `encoder_preference` | `auto`, `libx264`, `h264_nvenc`, `h264_qsv`, `h264_amf`, or `h264_vaapi` |
| `overlay_titles`, `shuffle` | Title overlays and playlist shuffle |
| `remember_current_video` | Restart the last playing YouTube video from the beginning on the next stream start (default: off) |

Recovery, transition and standby media are uploaded through Settings; see
[Recovery, transition and standby media](#recovery-transition-and-standby-media).

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

**Changelog** on the About tab opens the release notes from GitHub. When the
selected version is newer than the running one, it lists every release in the
channel between them, newest first; otherwise it shows the selected release. Notes
are loaded by the update check, so run **Check** first if the changelog is empty.

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

### Streaming software output

Instead of publishing to a platform, Stream247 can send its output to streaming
software such as OBS Studio or Streamlabs Desktop, for example to rebroadcast VODs
from a server while your own PC stays live. On the Stream tab, set **Platform** to
**Streaming software (OBS, etc.)** and start the stream. No stream key is needed.

Stream247 serves the output as HLS from the dashboard:

```text
http://<server-address>:7788/api/output/index.m3u8
```

The Stream tab shows this URL in **Streaming Software URL** while the platform is
selected; click the field or **Copy** to copy it. Choose which address it uses in
**Settings → Playback & preferences → Streaming software endpoint**, which lists the
server's private addresses (local network, Tailscale or other overlay networks, and
this machine). The choice is saved as `local_output_address`; it only changes the
displayed URL, not which networks can connect.

In OBS, add a **Media Source**, untick **Local File**, paste the URL into **Input**,
and set a **Reconnect Delay** so playback resumes after a restart. Expect several seconds of latency; the output keeps
running through transitions and source recovery, and the endpoint answers only while
the stream runs.

#### Network access

The endpoint is only served to clients on private networks:

- the same machine (`127.0.0.0/8`, `::1`)
- the local network (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, link-local)
- private overlay networks such as Tailscale, Headscale, NetBird, ZeroTier, or WireGuard
  (`100.64.0.0/10`, `fc00::/7`, or the private ranges above)

Requests arriving directly from a public address, such as through a router port
forward, are refused with `403 Forbidden`. There is no public mode and no
authentication: anyone who can reach the endpoint from a private address can watch
the stream.

> **Warning:** Stream247 does not block reverse proxies or similar setups. A proxy
> connects from a private address, so anything it forwards is allowed, including
> traffic from the internet. This is supported, but not recommended. Prefer a private
> overlay network such as Tailscale to reach the server from another location.

### Background service

To start the installed user service at boot without logging in:
`sudo loginctl enable-linger "$USER"`.

## Pausing a broadcast

**Pause** on the Stream tab holds the broadcast without ending it. The current
source is released, the output stays connected and shows **Standby media**, and the
status reads **Paused**. Without standby media, pausing shows black video with
silent audio. **Resume** reopens the
current video a fraction of a second before where it paused; Twitch and HLS relays
rejoin the live stream instead. Skip while paused moves to the next item, which
starts when you resume. Stop ends the broadcast as usual.

Pause needs a persistent output, which all RTMP destinations and streaming software
output use. It is unavailable for YouTube when `youtube_persistent_output` is off,
and for local Debug tests.

## Recovery, transition and standby media

Upload separate **Recovery media**, **Transition media** and **Standby media** in
Settings: PNG/JPG/WebP images, GIF, MP4 or WebM, up to 10 MiB each. Media fits the output
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

Standby media is shown while the broadcast is [paused](#pausing-a-broadcast).
Videos and GIFs loop and images stay on screen. A video's own audio plays and loops;
media without audio plays silence. Standby never falls back to recovery media: with
no standby media, pausing shows black video. Replacing or removing it applies
immediately, even during a pause. On resume, standby stays on screen until the
video is ready to continue.

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
