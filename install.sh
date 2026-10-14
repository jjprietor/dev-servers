#!/bin/zsh
# Compile, install, and open Dev Servers. --login enables launch at login.
set -euo pipefail
cd "$(dirname "$0")"
case "${1:-}" in
  ''|--login) ;;
  *) print -u2 'Usage: ./install.sh [--login]'; exit 1 ;;
esac
(( $# <= 1 )) || { print -u2 'Usage: ./install.sh [--login]'; exit 1; }

./build.sh
DEST="$HOME/Applications/Dev Servers.app"
mkdir -p "$HOME/Applications"
STAGE="$(mktemp -d "$HOME/Applications/.devservers-install.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp -R 'build/Dev Servers.app' "$STAGE/new.app"
codesign --verify --deep --strict "$STAGE/new.app"
pkill -x DevServers 2>/dev/null || true
if [[ -d "$DEST" ]]; then
  mv "$DEST" "$STAGE/previous.app"
fi
if ! mv "$STAGE/new.app" "$DEST"; then
  [[ ! -d "$STAGE/previous.app" ]] || mv "$STAGE/previous.app" "$DEST"
  exit 1
fi

if [[ "${1:-}" == --login ]]; then
  osascript - "$DEST" <<'APPLESCRIPT'
on run argv
  tell application "System Events"
    if exists login item "Dev Servers" then delete login item "Dev Servers"
    make login item at end with properties {name:"Dev Servers", path:item 1 of argv, hidden:false}
  end tell
end run
APPLESCRIPT
fi
open "$DEST"
print 'Dev Servers installed. Look for the server icon in the menu bar.'
