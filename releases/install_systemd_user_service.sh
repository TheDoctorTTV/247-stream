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
# Prefer the source address selected by the default route. This only queries the
# local routing table; it does not contact the destination or an external service.
SERVER_IP=""
if command -v ip >/dev/null 2>&1; then
  SERVER_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<NF; i++) if ($i == "src") {print $(i+1); exit}}' || true)"
fi
if [[ -z "$SERVER_IP" ]] && command -v hostname >/dev/null 2>&1; then
  SERVER_IP="$(hostname -I 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $i !~ /^127\./) {print $i; exit}}' || true)"
fi
echo "Installed stream247.service."
echo "Dashboard (default port): http://${SERVER_IP:-SERVER_IP}:7788"
echo "Existing web_server_host/web_server_port settings in config.json still apply."
