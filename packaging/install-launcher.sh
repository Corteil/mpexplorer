#!/bin/sh
# Install the desktop launcher and icon for the current user (no root needed).
# Run it from a checkout:  sh packaging/install-launcher.sh
# The launcher runs `mpexplorer`, so install the app first (see the README).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
mkdir -p "$apps" "$icons"
cp "$here/mpexplorer.desktop" "$apps/mpexplorer.desktop"
cp "$here/../mpexplorer/icon.png" "$icons/mpexplorer.png"
echo "Installed $apps/mpexplorer.desktop and $icons/mpexplorer.png"
