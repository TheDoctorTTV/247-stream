#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
if ! command -v cargo >/dev/null 2>&1; then
  echo "ERROR: Cargo is required. Install the stable Rust toolchain first." >&2
  exit 1
fi
BUILD_ARGS=(build --release --locked --bin stream247-server)
ARTIFACT_ROOT="${CARGO_TARGET_DIR:-${ROOT_DIR}/target}"
if [[ "${ARTIFACT_ROOT}" != /* ]]; then
  ARTIFACT_ROOT="${ROOT_DIR}/${ARTIFACT_ROOT}"
fi
if [[ -n "${BUILD_TARGET:-}" ]]; then
  BUILD_ARGS+=(--target "${BUILD_TARGET}")
  ARTIFACT_ROOT="${ARTIFACT_ROOT}/${BUILD_TARGET}"
fi
cargo "${BUILD_ARGS[@]}"
mkdir -p "${ROOT_DIR}/releases"
install -m 755 "${ARTIFACT_ROOT}/release/stream247-server" "${ROOT_DIR}/releases/stream247-server"
echo "Build complete: releases/stream247-server"
