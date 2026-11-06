import sys

from . import __version__


def main():
    if "--version" in sys.argv[1:] or "-V" in sys.argv[1:]:      # before GTK is imported: works without PyGObject
        print("mpexplorer %s" % __version__)
        return
    if sys.argv[1:2] == ["--miniterm"]:     # the frozen build's own miniterm, for "Open in external terminal"
        from serial.tools import miniterm
        sys.argv = ["miniterm"] + sys.argv[2:]
        miniterm.main()
        return
    if sys.argv[1:2] == ["--esptool"]:      # the frozen build's bundled esptool, for the firmware tools
        import esptool
        sys.argv = ["esptool"] + sys.argv[2:]
        esptool._main()
        return
    if "--install-desktop" in sys.argv[1:] or "--uninstall-desktop" in sys.argv[1:]:   # also needs no GTK
        from .desktop import main as desktop_main
        desktop_main(sys.argv[1:])
        return
    try:
        from .gtkui.app import run
    except (ImportError, ValueError) as e:       # no PyGObject, or no GTK 3 typelib
        sys.exit("GTK 3 is not available (%s).\n"
                 "Install it with: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91" % e)
    sys.exit(run(self_test="--self-test" in sys.argv[1:]) or 0)


if __name__ == "__main__":
    main()
