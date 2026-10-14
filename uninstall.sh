#!/bin/zsh
# Desinstala Dev Servers. Con --purge borra también ~/.devservers (servers detenidos y logs).
set -uo pipefail
pkill -x DevServers 2>/dev/null
osascript -e 'tell application "System Events" to delete login item "Dev Servers"' >/dev/null 2>&1
rm -rf "$HOME/Applications/Dev Servers.app"
[[ "${1:-}" == "--purge" ]] && rm -rf "${DEVSERVERS_HOME:-$HOME/.devservers}"
echo "✓ Dev Servers desinstalada."
