#!/usr/bin/env bash
set -euo pipefail

UNIT_NAME="stream247.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="${UNIT_DIR}/${UNIT_NAME}"

if systemctl --user list-unit-files "${UNIT_NAME}" >/dev/null 2>&1; then
  systemctl --user disable --now "${UNIT_NAME}" || true
fi

if [[ -f "${UNIT_PATH}" ]]; then
  rm -f "${UNIT_PATH}"
fi

systemctl --user daemon-reload
systemctl --user reset-failed || true

echo "Uninstalled user service: ${UNIT_NAME}"
echo "Removed unit file: ${UNIT_PATH}"
echo
echo "Optional: disable linger if you no longer need boot-time user services:"
echo "  sudo loginctl disable-linger ${USER}"
