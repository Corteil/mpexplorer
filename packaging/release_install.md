## Install (Raspberry Pi OS)
```
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 pipx
sudo usermod -aG dialout $USER      # serial access; log out and in again
pipx install --system-site-packages git+https://github.com/Corteil/mpexplorer@{tag}
mpexplorer --install-desktop        # optional: menu entry, desktop icon and taskbar icon
mpexplorer
```
Already installed? Upgrade with `pipx install --force --system-site-packages git+https://github.com/Corteil/mpexplorer@{tag}`.
Firmware tools for a badge or other ESP32-S3 also need `pipx install esptool`. `mpexplorer --uninstall-desktop` removes the
shortcuts. See the [README](https://github.com/Corteil/mpexplorer/blob/{tag}/README.md) for details.

## Install (Windows 10 and 11)
Download `mpexplorer-{version}-windows.zip` below, unzip it anywhere and run `mpexplorer\mpexplorer.exe`. Nothing needs
installing: GTK, Python, pyserial, miniterm and esptool are inside. The build is **not signed yet**, so SmartScreen says
"Windows protected your PC" the first time: choose *More info*, then *Run anyway*. Settings go to `%APPDATA%\mpexplorer`;
rename `portable.txt.example` beside the exe to `portable.txt` to keep them in a `data` folder there instead (a USB stick).
To update, unzip the new version over the old folder or beside it.
