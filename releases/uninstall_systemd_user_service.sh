#!/usr/bin/env bash
set -euo pipefail
UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/stream247.service"
systemctl --user disable --now stream247.service
rm -f "${UNIT_PATH}"
systemctl --user daemon-reload
echo "Removed stream247.service. Application files and configuration were preserved."
