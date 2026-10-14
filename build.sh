#!/bin/zsh
# Build a signed local app bundle without installing or launching it.
set -euo pipefail
cd "$(dirname "$0")"

[[ "$(uname -s)" == Darwin ]] || { print -u2 'Dev Servers requires macOS.'; exit 1; }
(( ${$(sw_vers -productVersion)%%.*} >= 14 )) || { print -u2 'Dev Servers requires macOS 14 or later.'; exit 1; }
xcrun --find swiftc >/dev/null
command -v python3 >/dev/null || { print -u2 'Python 3.9 or later is required.'; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'

mkdir -p build
WORK="$(mktemp -d "$PWD/build/.build.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
APP="$WORK/Dev Servers.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
xcrun swiftc -O -parse-as-library -swift-version 5 \
  -target "$(uname -m)-apple-macos14.0" \
  -module-cache-path "$PWD/build/ModuleCache" \
  -o "$APP/Contents/MacOS/DevServers" app/DevServers.swift
cp devservers.py "$APP/Contents/Resources/devservers.py"
cp app/Assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp app/Info.plist "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null
codesign --force --sign - "$APP"
codesign --verify --deep --strict "$APP"
rm -rf 'build/Dev Servers.app'
mv "$APP" 'build/Dev Servers.app'
print 'Built build/Dev Servers.app'
