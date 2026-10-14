#!/bin/zsh
set -euo pipefail
case "${1:-}" in
  ''|--purge) ;;
  *) print -u2 'Usage: ./uninstall.sh [--purge]'; exit 1 ;;
esac
(( $# <= 1 )) || { print -u2 'Usage: ./uninstall.sh [--purge]'; exit 1; }
STATE_DIR="${DEVSERVERS_HOME:-$HOME/.devservers}"
if [[ "${1:-}" == --purge ]]; then
  # Never recursively remove an arbitrary DEVSERVERS_HOME directory.
  [[ "$STATE_DIR" == /* && "$STATE_DIR" != / && "$STATE_DIR" != "$HOME" ]] || {
    print -u2 'Refusing to purge an unsafe state path.'; exit 1
  }
  [[ ! -L "$STATE_DIR" ]] || { print -u2 'Refusing to purge a symbolic link.'; exit 1; }
fi
pkill -x DevServers 2>/dev/null || true
osascript -e 'tell application "System Events" to delete login item "Dev Servers"' >/dev/null 2>&1 || true
rm -rf "$HOME/Applications/Dev Servers.app"
if [[ "${1:-}" == --purge && -d "$STATE_DIR" ]]; then
  rm -f "$STATE_DIR/stopped.json" "$STATE_DIR/state.lock"
  if [[ -d "$STATE_DIR/logs" && ! -L "$STATE_DIR/logs" ]]; then
    for log in "$STATE_DIR"/logs/[a-f0-9][a-f0-9][a-f0-9][a-f0-9][a-f0-9][a-f0-9][a-f0-9][a-f0-9].log(N); do
      rm -f "$log"
    done
    rmdir "$STATE_DIR/logs" 2>/dev/null || true
  fi
  rmdir "$STATE_DIR" 2>/dev/null || true
fi
print 'Dev Servers uninstalled.'
