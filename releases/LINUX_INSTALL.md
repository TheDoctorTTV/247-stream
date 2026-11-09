# Stream247 Linux installation

The README provides one-command release and prerelease setup. That helper
extracts the package into the current directory and runs the included installer.

For manual installation, extract `stream247-linux-x86_64.tar.gz`, then enter `stream247`.
Copy your existing `config.json` into this directory if migrating an installation.

```bash
chmod +x stream247-server install_systemd_user_service.sh uninstall_systemd_user_service.sh
./install_systemd_user_service.sh
```

Open **http://SERVER_IP:7788** from your PC, using the server's reachable IP.
The service installer prints a detected server address. `127.0.0.1` is only for
access on the server itself. The application listens on all interfaces by default
(`web_server_host: "0.0.0.0"`); an existing config.json can override the bind host
and port. Streaming starts from the dashboard.
The application uses FFmpeg and yt-dlp beside the executable, with PATH fallback
and automatic Linux downloads when needed. Python is not required by Stream247.

```bash
systemctl --user status stream247.service
journalctl --user -u stream247.service -f
```

For startup without logging in: `sudo loginctl enable-linger "$USER"`.
Run `./uninstall_systemd_user_service.sh` to remove the service.
