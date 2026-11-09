#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="${ROOT_DIR}/releases"
ARCHIVE_PATH="${ROOT_DIR}/stream247-linux-x86_64.tar.gz"
if [[ ! -x "${RELEASE_DIR}/stream247-server" ]]; then
  echo "ERROR: Missing executable releases/stream247-server. Run ./build_linux.sh first." >&2
  exit 1
fi
# Keep build output in releases/; expose stream247/ as the archive directory.
# Package only distributable files; runtime keys, logs and downloaded tools stay local.
tar -czf "${ARCHIVE_PATH}.tmp" -C "${ROOT_DIR}" \
  --transform='s,^releases/,stream247/,' \
  releases/stream247-server \
  releases/install_systemd_user_service.sh \
  releases/uninstall_systemd_user_service.sh \
  releases/LINUX_INSTALL.md
mv -f "${ARCHIVE_PATH}.tmp" "${ARCHIVE_PATH}"
echo "Release package ready: ${ARCHIVE_PATH}"
