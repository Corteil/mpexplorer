import os
import sys


def main():
    ui = os.environ.get("MPEXPLORER_UI", "gtk")
    if "--gtk" in sys.argv:
        ui = "gtk"
    elif "--tk" in sys.argv:
        ui = "tk"
    if ui == "gtk":
        try:
            from .gtkui.app import run
        except (ImportError, ValueError) as e:       # no PyGObject, or no GTK 3 typelib
            print("GTK 3 is not available (%s); using the Tk interface.\n"
                  "Install it with: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91" % e, file=sys.stderr)
            from .gui.app import run
    else:
        from .gui.app import run
    run()


if __name__ == "__main__":
    main()
