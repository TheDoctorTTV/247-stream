# Stream247

Stream247 is a native Rust headless streamer/relay with a built-in web dashboard.

This branch migrates the existing Python runtime; the known audio/timestamp issue
is not resolved by the language migration. See [Rust migration notes](docs/RUST_MIGRATION.md).
Source retry, audio-stall recovery and persistent fallback have since been added;
see [source recovery behavior](docs/SOURCE_RECOVERY.md).

It can:
- Loop a YouTube playlist or single YouTube video
- Relay a Twitch channel URL
- Relay a direct HLS `.m3u8` URL
- Push output to any RTMP/RTMPS ingest (YouTube Live, Twitch, Owncast, Restream, custom RTMP servers)

## What the app includes now

- Web dashboard to start/stop/skip streams and live-edit settings
- Source support: YouTube playlist/video, Twitch URL, direct HLS URL
- Output controls: resolution (480p to 2160p), 30/60 FPS, bitrate, stream buffer mode
- Encoder selection: auto or explicit (`libx264`, NVENC, QSV, AMF, VAAPI)
- Experimental Twitch Enhanced Broadcasting with Auto or custom track/bandwidth limits (FFmpeg 8+); see [Twitch output notes](docs/TWITCH.md)
- Graceful FFmpeg shutdown and RTMP output drain when stopping a stream
- Debug tab with a local preview of the selected source and source-failure controls; see [debug testing](docs/DEBUG_TESTING.md)
- Optional title overlay and playlist shuffle
- YouTube auth options (`yt-dlp` browser cookie import + optional profile path)
- In-app binary updates for `yt-dlp` and `ffmpeg`
- In-app app update check/install (release or prerelease channel)
- Optional config persistence for source + stream key
- Web console logs (app/other output + ffmpeg output)

## Run

### From source

```bash
cargo run --bin stream247-server
```

### From built binary

- Linux: `releases/stream247-server`

When running, open:

- On the server itself: `http://127.0.0.1:7788`
- From another PC: `http://SERVER_IP:7788` (use the server's reachable IP)

If `config.json` exists, host/port are read from:
- `web_server_host` (default `0.0.0.0`)
- `web_server_port` (default `7788`)

### Quick setup (Linux x86_64)

Run one command from the directory where you want Stream247 installed.
It downloads the latest package, extracts it into that directory, and runs the
included installer. Requires `curl` and standard Linux utilities; no `jq`,
Python, or Rust toolchain.

The helper URL follows the repository default branch automatically (`HEAD`).
Publish `install.sh` on that branch before using these commands. The selected
channel downloads published release assets, regardless of their source branch.

**Release (recommended)**

```bash
curl -fsSL https://raw.githubusercontent.com/TheDoctorTTV/247-stream/HEAD/install.sh | bash
```

**Pre-release**

```bash
curl -fsSL https://raw.githubusercontent.com/TheDoctorTTV/247-stream/HEAD/install.sh | bash -s -- prerelease
```

Open **http://SERVER_IP:7788**. A new installation follows the chosen update
channel; existing configuration is preserved.

To uninstall, run `./uninstall_systemd_user_service.sh` from the same directory.
For startup at boot without logging in, run `sudo loginctl enable-linger "$USER"` once.

## Configuration

`config.json` is read from the executable directory for packaged builds, or the
current directory for `cargo run`. Use `--data-dir PATH` to override it. Existing
Python-version configuration files use the same settings format.

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
- `log_to_file` (application log)
- `ffmpeg_log_to_file` (separate FFmpeg log)
- `debug_logging` (extra diagnostics)
- `remember`
- `yt_auth_enabled`
- `yt_auth_browser`
- `yt_auth_profile`
- `check_updates_startup`
- `auto_app_updates`
- `app_update_channel`
- `update_download_cap_mbps`

A current example is available at `releases/config.json` after building/running.

## Build

### Linux

Requires stable Rust/Cargo, a linker, pkg-config, and liblzma development headers.
On Debian, the system packages are `build-essential pkg-config liblzma-dev`.
Python and PyInstaller are no longer needed.

```bash
./build_linux.sh
./package_linux_release.sh
```

Build output:
- Linux: `releases/stream247-server`

The package `stream247-linux-x86_64.tar.gz` contains a top-level `stream247/` directory.
Local build output remains in `releases/`.

The build bundles web dashboard assets from `web/` (`index.html`, `style.css`, `app.js`) into the binary.

### Linux systemd user service (recommended for 24/7)

After building:

```bash
./releases/install_systemd_user_service.sh
```

This installs and starts `~/.config/systemd/user/stream247.service` with:
- `ExecStart` pointing to `releases/stream247-server`
- `Restart=always`
- `STREAM247_SYSTEMD=1` so in-app updates hand restart control to `systemd`

For startup on boot without logging in, enable linger once:

```bash
sudo loginctl enable-linger "$USER"
```

## Notes

- Stream247 is web-dashboard first; streaming starts only after clicking **Start Stream**.
- Dashboard binds to `0.0.0.0` by default; secure network access yourself (firewall/VPN/reverse proxy).
- YouTube/Twitch source retrieval depends on `yt-dlp`.
- Keep platform bitrate/resolution limits in mind for your destination ingest.

## Logging

The App log and Save FFmpeg log toggles independently enable `logs/app.log` and
`logs/ffmpeg.log` inside the application data directory. Debug logging takes effect
immediately and adds system, source/codec and periodic process/timing diagnostics.
It does not enable saving files by itself. Rotated logs are compressed with fast
gzip; active files stay plain text. See [logging details](docs/LOGGING.md).

## Creator

**TheDoctorTTV**
