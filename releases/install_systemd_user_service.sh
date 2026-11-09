#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}"
BUILD_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BINARY_PATH="${ROOT_DIR}/stream247-server"
if [[ ! -x "${BINARY_PATH}" ]]; then
  BINARY_PATH="${BUILD_ROOT}/releases/stream247-server"
fi
UNIT_NAME="stream247.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="${UNIT_DIR}/${UNIT_NAME}"

if [[ ! -x "${BINARY_PATH}" ]]; then
  echo "ERROR: Binary not found or not executable." >&2
  echo "Looked for:" >&2
  echo "  ${ROOT_DIR}/stream247-server" >&2
  echo "  ${BUILD_ROOT}/releases/stream247-server" >&2
  exit 1
fi

mkdir -p "${UNIT_DIR}"

cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=Stream247 Headless Runtime
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
ExecStart=${BINARY_PATH}
Restart=always
RestartSec=2
Environment=STREAM247_SYSTEMD=1
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "${UNIT_NAME}"

echo "Installed and started user service: ${UNIT_NAME}"
echo "Unit file: ${UNIT_PATH}"
echo
echo "Useful commands:"
echo "  systemctl --user status ${UNIT_NAME}"
echo "  journalctl --user -u ${UNIT_NAME} -f"
echo "  systemctl --user restart ${UNIT_NAME}"
echo
echo "For true boot-time start without interactive login, enable linger once:"
echo "  sudo loginctl enable-linger ${USER}"
