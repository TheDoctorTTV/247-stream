#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="${ROOT_DIR}/releases"
ARCHIVE_PATH="${ROOT_DIR}/stream247-linux-x86_64.tar.gz"
if [[ ! -x "${RELEASE_DIR}/stream247-server" ]]; then
  echo "ERROR: Missing executable releases/stream247-server. Run ./build_linux.sh first." >&2
  exit 1
fi
# Generate installation files alongside the compiled executable.
cat > "${RELEASE_DIR}/install_systemd_user_service.sh" <<'STREAM247_SERVICE_INSTALL'
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
STREAM247_SERVICE_INSTALL

cat > "${RELEASE_DIR}/uninstall_systemd_user_service.sh" <<'STREAM247_SERVICE_UNINSTALL'
#!/usr/bin/env bash
set -euo pipefail
UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/stream247.service"
systemctl --user disable --now stream247.service
rm -f "${UNIT_PATH}"
systemctl --user daemon-reload
echo "Removed stream247.service. Application files and configuration were preserved."
STREAM247_SERVICE_UNINSTALL

chmod +x "${RELEASE_DIR}/install_systemd_user_service.sh" "${RELEASE_DIR}/uninstall_systemd_user_service.sh"

# Keep build output in releases/; expose stream247/ as the archive directory.
# Package only distributable files; runtime keys, logs and downloaded tools stay local.
tar -czf "${ARCHIVE_PATH}.tmp" -C "${ROOT_DIR}" \
  --transform='s,^releases/,stream247/,' \
  releases/stream247-server \
  releases/install_systemd_user_service.sh \
  releases/uninstall_systemd_user_service.sh
mv -f "${ARCHIVE_PATH}.tmp" "${ARCHIVE_PATH}"
echo "Release package ready: ${ARCHIVE_PATH}"
