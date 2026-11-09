#!/usr/bin/env bash
# Download a packaged release into the current directory and run its installer.
set -euo pipefail
umask 077

CHANNEL="${1:-release}"
case "$CHANNEL" in
  release|prerelease) ;;
  *) echo "Usage: bash install.sh [release|prerelease]" >&2; exit 1 ;;
esac
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo "This package requires Linux x86_64." >&2
  exit 1
fi
for tool in curl tar awk mktemp systemctl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Required command missing: $tool" >&2; exit 1; }
done

REPO="TheDoctorTTV/247-stream"
ASSET="stream247-linux-x86_64.tar.gz"
INSTALL_DIR="$PWD"
WORK_DIR="$(mktemp -d)"
trap 'rm -r -- "$WORK_DIR"' EXIT

if [[ "$CHANNEL" == release ]]; then
  DOWNLOAD_URL="https://github.com/${REPO}/releases/latest/download/${ASSET}"
else
  # Read only release flags and the matching asset URL. Track JSON nesting and
  # strings, rather than grepping across releases or assuming pretty-printing.
  PAGE=1
  while :; do
    curl -fsSL --retry 2 "https://api.github.com/repos/${REPO}/releases?per_page=100&page=${PAGE}" \
      -o "$WORK_DIR/releases.json"
    if DOWNLOAD_URL="$(awk -v wanted="$ASSET" '
      function literal() {
        if (depth == 2 && want[depth]) {
          if (key[depth] == "prerelease") pre = word
          if (key[depth] == "draft") draft = word
        }
        if (word != "") want[depth] = 0
        word = ""
      }
      {
        for (i = 1; i <= length($0); i++) {
          c = substr($0, i, 1)
          if (quoted) {
            if (escaped) {
              token = token c
              escaped = 0
            } else if (c == "\\") escaped = 1
            else if (c == "\"") {
              quoted = 0
              if (want[depth]) {
                if (depth == 4 && assets) {
                  if (key[depth] == "name") name = token
                  if (key[depth] == "browser_download_url") url = token
                }
                want[depth] = 0
              }
            } else token = token c
            continue
          }
          if (c ~ /[a-zA-Z0-9_.+-]/) { word = word c; continue }
          literal()
          if (c == "\"") { quoted = 1; token = "" }
          else if (c == ":") { key[depth] = token; want[depth] = 1 }
          else if (c == "{" || c == "[") {
            if (!started) { started = 1; if (c != "[") invalid = 1 }
            if (depth == 2 && c == "[" && key[depth] == "assets") assets = 1
            want[depth] = 0
            depth++
            key[depth] = ""; want[depth] = 0
            if (depth == 2 && c == "{") { pre = ""; draft = ""; candidate = ""; count++ }
            if (depth == 4 && assets && c == "{") { name = ""; url = "" }
          } else if (c == "}" || c == "]") {
            if (depth == 4 && assets && name == wanted) candidate = url
            if (depth == 3 && c == "]") assets = 0
            if (depth == 2 && c == "}" && pre == "true" && draft == "false" && candidate != "") {
              found = candidate
              exit
            }
            depth--
            if (depth < 0) invalid = 1
          } else if (c == ",") { key[depth] = ""; want[depth] = 0 }
        }
        # A literal may end at a newline in formatted JSON.
        literal()
      }
      END {
        if (found != "") print found
        else if (!started || invalid || depth != 0 || quoted) exit 2
        else if (count == 0) exit 3
      }
    ' "$WORK_DIR/releases.json")"; then
      [[ -n "$DOWNLOAD_URL" ]] && break
      PAGE=$((PAGE + 1))
    else
      RESULT=$?
      if [[ "$RESULT" == 3 ]]; then
        echo "No published pre-release with ${ASSET} was found." >&2
      else
        echo "Could not read GitHub pre-release metadata." >&2
      fi
      exit 1
    fi
  done
fi

case "$DOWNLOAD_URL" in
  "https://github.com/${REPO}/releases/"*) ;;
  *) echo "Unexpected release download URL." >&2; exit 1 ;;
esac

echo "Downloading latest ${CHANNEL}..."
curl -fL --retry 2 "$DOWNLOAD_URL" -o "$WORK_DIR/package.tar.gz"
# Stage extraction first; the included installer must exist before copying files.
tar -xzf "$WORK_DIR/package.tar.gz" -C "$WORK_DIR" --strip-components=1
for file in stream247-server install_systemd_user_service.sh uninstall_systemd_user_service.sh; do
  [[ -f "$WORK_DIR/$file" && ! -L "$WORK_DIR/$file" ]] || {
    echo "Package is missing $file." >&2; exit 1;
  }
done
# Current stream247/ and older releases/ or stream247-linux/ packages have one top directory.
tar -xzf "$WORK_DIR/package.tar.gz" -C "$INSTALL_DIR" --strip-components=1
cd "$INSTALL_DIR"
chmod +x stream247-server install_systemd_user_service.sh uninstall_systemd_user_service.sh
if [[ ! -e config.json ]]; then
  printf '{"app_update_channel":"%s"}\n' "$CHANNEL" > config.json
fi
./install_systemd_user_service.sh
