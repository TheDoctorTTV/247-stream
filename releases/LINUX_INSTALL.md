# Stream247 Linux installation

The README provides one-command release and prerelease setup. That helper
extracts the package into the current directory and runs the included installer.

For manual installation, extract `stream247-linux-x86_64.tar.gz`, then enter `stream247`.
Copy your existing `config.json` into this directory if migrating an installation.

```bash
chmod +x stream247-server install_systemd_user_service.sh uninstall_systemd_user_service.sh
./install_systemd_user_service.sh
```

The dashboard is at http://127.0.0.1:7788. Streaming starts from the dashboard.
The application uses FFmpeg and yt-dlp beside the executable, with PATH fallback
and automatic Linux downloads when needed. Python is not required by Stream247.

```bash
systemctl --user status stream247.service
journalctl --user -u stream247.service -f
```

For startup without logging in: `sudo loginctl enable-linger "$USER"`.
Run `./uninstall_systemd_user_service.sh` to remove the service.
