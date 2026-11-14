# Stream247

A headless streamer and relay with a built-in web dashboard.
Loop YouTube playlists and videos, relay Twitch or HLS streams, and broadcast to
RTMP/RTMPS destinations.

- Start, stop, restart, and skip streams from the dashboard.
- Choose resolution, frame rate, bitrate, and CPU or hardware encoding.
- Enable title overlays, playlist shuffle, and custom transition or recovery media.
- Manage app and binary updates, with scheduled app checks and manual installation.
- View live logs and preview sources in the Debug tab.

## Quick setup (Linux x86_64)

Requires `curl`. Run from the directory where you want Stream247 installed.

**Release**

```bash
curl -fsSL https://raw.githubusercontent.com/TheDoctorTTV/247-stream/HEAD/install.sh | bash
```

**Pre-release**

```bash
curl -fsSL https://raw.githubusercontent.com/TheDoctorTTV/247-stream/HEAD/install.sh | bash -s -- prerelease
```

Open the dashboard at **http://0.0.0.0:7788**.

## Build from source

Requires stable Rust/Cargo, a linker, `pkg-config`, and liblzma development headers.
On Debian: `build-essential pkg-config liblzma-dev`.

```bash
./build_linux.sh
./package_linux_release.sh
```

Creates `releases/stream247-server` and packages it as
`stream247-linux-x86_64.tar.gz`.

To run directly from source:

```bash
cargo run --bin stream247-server
```

## Documentation

See [configuration and logging](docs.md) for settings and detailed guides.

## Uninstall

Run from the installation directory:

```bash
./uninstall_systemd_user_service.sh
```

## Notes
- The dashboard binds to `0.0.0.0` by default; restrict access with a firewall, VPN, or reverse proxy.
- Source retrieval uses `yt-dlp`.
- Follow your destination's bitrate and resolution limits.
