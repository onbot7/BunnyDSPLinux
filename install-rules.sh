#!/usr/bin/env bash
# install-rules.sh - Configures udev rules and PATH for Tanchjim Bunny DSP
# Supports: Ubuntu, Debian, Pop!_OS, Arch, Manjaro, Fedora, openSUSE, Void, Alpine, Gentoo, NixOS

set -euo pipefail

RULE_NAME="99-tanchjim.rules"
DEST_DIR="/etc/udev/rules.d"
DEST_FILE="$DEST_DIR/$RULE_NAME"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_RULE="$SRC_DIR/$RULE_NAME"
BIN_SRC="$SRC_DIR/tanchjim-ctl"

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  -i, --install     Install udev rules for permanent device access (default)
  -p, --path        Symlink tanchjim-ctl into PATH (/usr/local/bin or ~/.local/bin)
  -u, --uninstall   Remove installed udev rules and PATH symlinks
  -h, --help        Show this help message

Supported distributions:
  Ubuntu, Debian, Pop!_OS, Linux Mint
  Arch Linux, Manjaro, EndeavourOS
  Fedora, RHEL, CentOS, Rocky Linux
  openSUSE (Tumbleweed & Leap)
  Void Linux, Alpine Linux, Gentoo
  NixOS
EOF
}

INSTALL_PATH=false
ACTION="install"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--path)
            INSTALL_PATH=true
            shift
            ;;
        -u|--uninstall)
            ACTION="uninstall"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -i|--install)
            ACTION="install"
            shift
            ;;
        *)
            echo "Unknown flag: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

is_nixos() {
    [[ -f /etc/NIXOS ]] || command -v nixos-version >/dev/null 2>&1
}

# Handle uninstall
if [[ "$ACTION" == "uninstall" ]]; then
    echo "Removing Tanchjim Bunny DSP configurations..."
    if [[ -f "$DEST_FILE" ]]; then
        if [[ $EUID -eq 0 ]]; then
            rm -f "$DEST_FILE"
            echo "[OK] Removed $DEST_FILE"
            if command -v udevadm >/dev/null 2>&1; then
                udevadm control --reload-rules && (udevadm trigger --subsystem-match=hidraw || udevadm trigger)
                echo "[OK] Reloaded udev rules."
            fi
        else
            echo "Note: Root privileges required to remove $DEST_FILE. Run with sudo." >&2
        fi
    fi

    # Clean PATH symlinks
    for target in "/usr/local/bin/tanchjim-ctl" "$HOME/.local/bin/tanchjim-ctl"; do
        if [[ -L "$target" ]]; then
            rm -f "$target"
            echo "[OK] Removed symlink $target"
        fi
    done
    echo "Uninstall complete."
    exit 0
fi

# PATH symlink helper
install_bin_to_path() {
    chmod +x "$BIN_SRC"
    if [[ $EUID -eq 0 ]]; then
        mkdir -p "/usr/local/bin"
        ln -sf "$BIN_SRC" "/usr/local/bin/tanchjim-ctl"
        echo "[OK] Installed tanchjim-ctl to PATH (/usr/local/bin/tanchjim-ctl)"
    else
        mkdir -p "$HOME/.local/bin"
        ln -sf "$BIN_SRC" "$HOME/.local/bin/tanchjim-ctl"
        echo "[OK] Installed tanchjim-ctl to user PATH (~/.local/bin/tanchjim-ctl)"
        if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
            echo "Note: Ensure ~/.local/bin is in your PATH."
        fi
    fi
}

# NixOS handling
if is_nixos; then
    if [[ "$INSTALL_PATH" == true ]]; then
        install_bin_to_path
    fi

    cat <<'EOF'

---------------------------------------------------------------------------------
NixOS Configuration (declarative udev rule):
---------------------------------------------------------------------------------
Copy and paste this one-liner into your /etc/nixos/configuration.nix:

  services.udev.extraRules = ''SUBSYSTEM=="hidraw", ATTRS{idVendor}=="31b2", ATTRS{idProduct}=="1112", MODE="0660", GROUP="plugdev", TAG+="uaccess"'';

Then apply:
  sudo nixos-rebuild switch
---------------------------------------------------------------------------------
EOF
    exit 0
fi

# Standard distros (Ubuntu, Arch, Fedora, openSUSE, Void, Alpine, etc.)
if [[ $EUID -ne 0 ]]; then
    if [[ "$INSTALL_PATH" == true ]]; then
        # Can install to user's ~/.local/bin without sudo
        install_bin_to_path
        echo ""
        echo "To also install system udev rules for non-root hardware access, run:"
        echo "  sudo $0"
        exit 0
    else
        echo "Root privileges required to install udev rules. Please run with sudo:" >&2
        echo "  sudo $0 $*" >&2
        exit 1
    fi
fi

if [[ ! -f "$SRC_RULE" ]]; then
    echo "Error: Cannot locate $SRC_RULE" >&2
    exit 1
fi

# 1. Install udev rule
mkdir -p "$DEST_DIR"
cp "$SRC_RULE" "$DEST_FILE"
chmod 644 "$DEST_FILE"
echo "[OK] Installed $DEST_FILE"

if [[ -n "${SUDO_USER:-}" ]] && [[ "$SUDO_USER" != "root" ]] && getent group plugdev >/dev/null 2>&1; then
    if usermod -aG plugdev "$SUDO_USER" 2>/dev/null; then
        echo "[OK] Added $SUDO_USER to plugdev group (log out and back in to apply)."
    fi
fi

# 2. Reload udev daemon across various distros
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules
    udevadm trigger --subsystem-match=hidraw || udevadm trigger
    echo "[OK] Reloaded udev rules and triggered hidraw subsystem."
elif command -v rc-service >/dev/null 2>&1 && rc-service -e udev; then
    rc-service udev restart
    echo "[OK] Restarted udev service via OpenRC."
elif command -v service >/dev/null 2>&1 && service udev status >/dev/null 2>&1; then
    service udev restart
    echo "[OK] Restarted udev service."
fi

# 3. Handle PATH installation if requested
if [[ "$INSTALL_PATH" == true ]]; then
    install_bin_to_path
fi

echo ""
echo "Done. Permanent non-root access configured for Tanchjim Bunny DSP."
if [[ "$INSTALL_PATH" == true ]]; then
    echo "You can now run 'tanchjim-ctl' from any directory."
else
    echo "Tip: Run '$0 --path' to add 'tanchjim-ctl' to your command PATH."
fi
