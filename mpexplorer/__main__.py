import os
import sys


def main():
    ui = os.environ.get("MPEXPLORER_UI", "tk")
    if "--gtk" in sys.argv:
        ui = "gtk"
    elif "--tk" in sys.argv:
        ui = "tk"
    if ui == "gtk":
        from .gtkui.app import run
    else:
        from .gui.app import run
    run()


if __name__ == "__main__":
    main()
