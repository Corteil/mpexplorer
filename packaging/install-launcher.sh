#!/bin/sh
# Install the desktop launcher, its icon and a desktop icon for the current user (no root needed).
# Run it from a checkout:  sh packaging/install-launcher.sh
# An installed app does the same with `mpexplorer --install-desktop`.
set -eu
cd "$(dirname "$0")/.."
exec python3 -m mpexplorer --install-desktop
