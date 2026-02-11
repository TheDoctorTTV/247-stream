# Stream247

Stream247 is a headless 24/7 streamer/relay with a built-in web dashboard.

It can:
- Loop a YouTube playlist or single YouTube video
- Relay a Twitch channel URL
- Relay a direct HLS `.m3u8` URL
- Push output to any RTMP/RTMPS ingest (YouTube Live, Twitch, Owncast, Restream, custom RTMP servers)

## What the app includes now

- Web dashboard to start/stop/skip streams and live-edit settings
- Source support: YouTube playlist/video, Twitch URL, direct HLS URL
- Output controls: resolution (480p to 2160p), 30/60 FPS, bitrate, stream buffer mode
- Encoder selection: auto or explicit (`libx264`, NVENC, QSV, AMF, VAAPI, VideoToolbox)
- Optional title overlay and playlist shuffle
- YouTube auth options (`yt-dlp` browser cookie import + optional profile path)
- In-app binary updates for `yt-dlp` and `ffmpeg`
- In-app app update check/install (release or prerelease channel)
- Optional config persistence for source + stream key
- Web console logs (app/other output + ffmpeg output)

## Run

### From source

```bash
python3 Stream247.py
```

### From built binary

- Linux: `dist/stream247-server`
- Windows: `dist/stream247-server.exe`

When running, open:

- `http://127.0.0.1:7788`

If `config.json` exists, host/port are read from:
- `web_server_host` (default `127.0.0.1`)
- `web_server_port` (default `7788`)

## Configuration

`config.json` is read from the app runtime directory.

Common fields:
- `playlist_url`
- `rtmp_base`
- `stream_key`
- `resolution`
- `framerate`
- `video_bitrate`
- `buffer_mode`
- `encoder_preference`
- `overlay_titles`
- `shuffle`
- `log_to_file`
- `rtmp_live`
- `remember`
- `yt_auth_enabled`
- `yt_auth_browser`
- `yt_auth_profile`
- `check_updates_startup`
- `auto_app_updates`
- `app_update_channel`
- `update_download_cap_mbps`

A current example is available at `dist/config.json` after building/running.

## Build

### Linux

```bash
./build_linux.sh
```

### Windows (PowerShell)

```powershell
.\build_windows.ps1
```

Build output:
- Linux: `dist/stream247-server`
- Windows: `dist/stream247-server.exe`

## Notes

- Stream247 is web-dashboard first; streaming starts only after clicking **Start Stream**.
- For remote dashboard access, set `web_server_host` to `0.0.0.0` and secure network access yourself.
- YouTube/Twitch source retrieval depends on `yt-dlp`.
- Keep platform bitrate/resolution limits in mind for your destination ingest.

## Creator

**TheDoctorTTV**
