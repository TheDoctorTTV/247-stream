#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY_PATH="${ROOT_DIR}/stream247-server"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="${UNIT_DIR}/stream247.service"
if [[ ! -x "${BINARY_PATH}" ]]; then
  echo "ERROR: Missing executable: ${BINARY_PATH}" >&2
  exit 1
fi
mkdir -p "${UNIT_DIR}"
cat > "${UNIT_PATH}" <<UNIT
[Unit]
Description=Stream247 Headless Runtime
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
ExecStart="${BINARY_PATH}"
Restart=always
RestartSec=2
Environment=STREAM247_SYSTEMD=1

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now stream247.service
echo "Installed stream247.service. Dashboard: http://127.0.0.1:7788"
