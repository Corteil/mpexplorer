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
