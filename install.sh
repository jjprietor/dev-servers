#!/bin/zsh
# Compila Dev Servers.app y la instala en ~/Applications.
#   ./install.sh          compila, instala y abre la app
#   ./install.sh --login  además la añade a los ítems de inicio de sesión
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="Dev Servers"
DEST="$HOME/Applications/$APP_NAME.app"
BUILD="build/$APP_NAME.app"

command -v swiftc >/dev/null || { echo "Falta swiftc: instala Xcode o ejecuta 'xcode-select --install'." >&2; exit 1; }
command -v python3 >/dev/null || { echo "Falta python3." >&2; exit 1; }

echo "→ Compilando…"
rm -rf build && mkdir -p "$BUILD/Contents/MacOS" "$BUILD/Contents/Resources"
swiftc -O -parse-as-library -swift-version 5 -target "$(uname -m)-apple-macos14.0" \
  -o "$BUILD/Contents/MacOS/DevServers" app/DevServers.swift
cp devservers.py "$BUILD/Contents/Resources/devservers.py"
cp app/Info.plist "$BUILD/Contents/Info.plist"
codesign --force --sign - "$BUILD" >/dev/null 2>&1

echo "→ Instalando en $DEST"
pkill -x DevServers 2>/dev/null || true
mkdir -p "$HOME/Applications"
rm -rf "$DEST" && cp -R "$BUILD" "$DEST"
rm -rf build

if [[ "${1:-}" == "--login" ]]; then
  osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$DEST\", hidden:false}" >/dev/null
  echo "→ Añadida a los ítems de inicio de sesión"
fi

open "$DEST"
echo "✓ Listo. Busca el icono de servidor en la barra de menús."
