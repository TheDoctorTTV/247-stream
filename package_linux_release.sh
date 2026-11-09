#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="${ROOT_DIR}/releases"
RELEASE_BINARY="${RELEASE_DIR}/stream247-server"
ARCHIVE_PATH="${ROOT_DIR}/stream247-linux-x86_64.tar.gz"

if [[ ! -f "${RELEASE_BINARY}" ]]; then
  echo "ERROR: Missing built binary: ${RELEASE_BINARY}" >&2
  echo "Build first with: ./build_linux.sh" >&2
  exit 1
fi

if [[ ! -d "${RELEASE_DIR}" ]]; then
  echo "ERROR: Missing release folder: ${RELEASE_DIR}" >&2
  exit 1
fi

chmod +x "${RELEASE_BINARY}"

rm -f "${ARCHIVE_PATH}"
(
  cd "${ROOT_DIR}"
  tar -czf "$(basename "${ARCHIVE_PATH}")" "$(basename "${RELEASE_DIR}")"
)

echo "Release package ready:"
echo "  ${ARCHIVE_PATH}"
