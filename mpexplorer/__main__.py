import sys

from . import __version__


def main():
    if "--version" in sys.argv[1:] or "-V" in sys.argv[1:]:      # before GTK is imported: works without PyGObject
        print("mpexplorer %s" % __version__)
        return
    try:
        from .gtkui.app import run
    except (ImportError, ValueError) as e:       # no PyGObject, or no GTK 3 typelib
        sys.exit("GTK 3 is not available (%s).\n"
                 "Install it with: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91" % e)
    run()


if __name__ == "__main__":
    main()
