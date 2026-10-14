# Contributing

Use macOS 14 or later with the Xcode Command Line Tools and Python 3.9 or later. The project has no third-party runtime dependencies.

## Check a change

```sh
python3 -m unittest discover -s tests -v
DEVSERVERS_INTEGRATION=1 python3 -m unittest discover -s tests -v
zsh -n build.sh install.sh uninstall.sh
./build.sh
```

The integration test only manages processes it creates. Do not exercise bulk cleanup against your normal development session as a test.

To try the UI with a separate history:

```sh
DEVSERVERS_HOME="$(mktemp -d)/state" \
  DEVSERVERS_SCRIPT="$PWD/devservers.py" \
  "$PWD/build/Dev Servers.app/Contents/MacOS/DevServers"
```

A separate history does not isolate process discovery: this still sees your user's real servers.

## Scope

Keep process handling in `devservers.py` and presentation in `app/DevServers.swift`. Include a regression test when changing process selection, signals, restart recovery or state persistence. Explain what the test protects against.

For bug reports, include macOS and Python versions, the expected behavior, and a minimal command that reproduces the problem. Redact commands and paths as needed. Never attach `stopped.json` or full process environments.

## App icon

The editable source is `scripts/render-icon.swift`. To regenerate the bundled icon:

```sh
xcrun swift scripts/render-icon.swift /tmp/DevServers.iconset
iconutil -c icns /tmp/DevServers.iconset -o app/Assets/AppIcon.icns
cp /tmp/DevServers.iconset/icon_256x256@2x.png docs/icon.png
```

## Preparing a release

Run the checks above, test the installed app, update the version fields in `app/Info.plist`, and summarize user-facing changes. Local builds are architecture-specific and ad hoc signed. A distributable download needs an explicit signing and notarization workflow; do not describe a local build as notarized.
