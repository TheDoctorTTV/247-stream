# Stream247 Linux Install

This guide is for the packaged Linux binary release (not source mode).

## Files expected in the release folder

- `stream247-server`
- `install_systemd_user_service.sh`
- `uninstall_systemd_user_service.sh`

## Install

1. Extract the archive:

```bash
tar -xzf stream247-linux-x86_64.tar.gz
cd releases
```

2. Ensure scripts/binary are executable:

```bash
chmod +x stream247-server install_systemd_user_service.sh uninstall_systemd_user_service.sh
```

3. Install and start user service:

```bash
./install_systemd_user_service.sh
```

4. Open dashboard:

```text
http://127.0.0.1:7788
```

## Logs and service control

```bash
systemctl --user status stream247.service
journalctl --user -u stream247.service -f
systemctl --user restart stream247.service
```

## Boot without logging in (optional)

```bash
sudo loginctl enable-linger "$USER"
```

## App updates with systemd

When app update is triggered from the dashboard:
- New binary is downloaded into `updates/`
- Binary is swapped in place
- Process exits
- `systemd` restarts service automatically

## Uninstall service

```bash
./uninstall_systemd_user_service.sh
```

Optional:

```bash
sudo loginctl disable-linger "$USER"
```
