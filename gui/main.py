#!/usr/bin/env python3
import sys
import os

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, Adw
except (ImportError, ValueError) as e:
    print("\nError: Libadwaita / GTK4 PyGObject bindings not found.", file=sys.stderr)
    print(f"Details: {e}\n", file=sys.stderr)
    print("To install required packages for your distribution:", file=sys.stderr)
    print("  - Ubuntu / Debian: sudo apt install python3-gi gir1.2-adw-1 gir1.2-gtk-4.0", file=sys.stderr)
    print("  - Arch Linux:      sudo pacman -S python-gobject libadwaita gtk4", file=sys.stderr)
    print("  - Fedora:          sudo dnf install python3-gobject libadwaita gtk4", file=sys.stderr)
    print("  - openSUSE:        sudo zypper install python3-gobject typelib-1_0-Adw-1 typelib-1_0-Gtk-4_0", file=sys.stderr)
    print("  - NixOS:           nix-shell -p python3Packages.pygobject3 gtk4 libadwaita gobject-introspection cairo\n", file=sys.stderr)
    print("Or run via the transparent launcher: ./KT0210-gui\n", file=sys.stderr)
    sys.exit(1)

from gui.app import run_gui

if __name__ == "__main__":
    sys.exit(run_gui() or 0)
