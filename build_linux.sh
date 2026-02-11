#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_ENTRY="${APP_ENTRY:-Stream247.py}"
APP_NAME="${APP_NAME:-stream247-server}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: Python not found: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -f "$APP_ENTRY" ]]; then
  echo "ERROR: Entry file not found: $APP_ENTRY" >&2
  exit 1
fi

echo "Installing/updating build dependencies..."
"$PYTHON_BIN" -m pip install --upgrade pip pyinstaller

echo "Building $APP_NAME from $APP_ENTRY ..."
ADD_DATA_ARGS=()
if [[ -f "icon.ico" ]]; then
  ADD_DATA_ARGS+=(--add-data "icon.ico:.")
fi
"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name "$APP_NAME" \
  "${ADD_DATA_ARGS[@]}" \
  "$APP_ENTRY"

echo
echo "Build complete:"
echo "  Binary: dist/$APP_NAME"
