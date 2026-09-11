#!/usr/bin/env python3
# Driver and control interface for Tanchjim Bunny DSP (KT Micro KT0210 codec).
#
# Hardware notes from USB packet reverse-engineering:
# - Device enumerates as USB HID device (VID: 0x31b2, PID: 0x1112).
# - Vendor configuration is exposed via HID report ID 0x4B (10-byte payload).
# - Register map:
#     0x24: active profile slot (0x03 = custom hardware PEQ, 0x02 = bypass)
#     0x26..0x35: 8 biquad filter bands (2 registers per band):
#       Even reg (0x26, 0x28, ...): Gain (int16_t, scale * 10) + Freq (uint16_t, Hz)
#       Odd reg  (0x27, 0x29, ...): Q (uint16_t, scale * 1000) + Filter Type (uint8_t)
#     0x66: digital preamp gain in dB (signed int8_t, range -12 to +12 dB)
# - Command opcodes:
#     0x52: Read register
#     0x57: Write register
#     0x53: Commit working registers to EEPROM / flash
#     0x43: Clear / reset chip state
#
# Inter-packet timings:
# - The KT0210 microcontroller requires ~10ms between HID reports to avoid dropped
#   writes over the internal I2C bus bridge.
# - Flash commit requires ~400ms to complete the page write cycle.

import argparse
import glob
import json
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler

VENDOR_ID = "31b2"
PRODUCT_ID = "1112"

REPORT_ID = 0x4B
CMD_READ = 0x52
CMD_WRITE = 0x57
CMD_COMMIT = 0x53
CMD_CLEAR = 0x43

REG_SLOT = 0x24
REG_BASE_BAND = 0x26
REG_PREGAIN = 0x66
NUM_BANDS = 8

FILTER_TYPE_MAP = {
    0: "PK",    # Peak / Bell biquad
    3: "LSQ",   # Low Shelf
    4: "HSQ",   # High Shelf
}
FILTER_TYPE_REV = {v: k for k, v in FILTER_TYPE_MAP.items()}
FILTER_TYPE_REV.update({"LSC": 3, "HSC": 4, "PEAK": 0, "LOW_SHELF": 3, "HIGH_SHELF": 4})

BASE_DIR = os.path.dirname(os.path.realpath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
PRESETS_DIR = os.path.join(BASE_DIR, "presets")


def find_device():
    # Scan sysfs directly instead of probing /dev/hidraw* nodes. Opening every node
    # causes EACCES on input devices (keyboards/mice) before reaching our audio device.
    for p in glob.glob("/sys/class/hidraw/hidraw*"):
        uevent = os.path.join(p, "device", "uevent")
        if not os.path.isfile(uevent):
            continue
        try:
            with open(uevent, "r") as f:
                content = f.read().lower()
                if "31b2:1112" in content or (VENDOR_ID in content and PRODUCT_ID in content):
                    return f"/dev/{os.path.basename(p)}"
        except (OSError, PermissionError):
            continue

    # Fallback: some kernels do not link hidraw under class, inspect hid bus directly
    for dev in glob.glob("/sys/bus/hid/devices/*31B2:1112*"):
        raw_dir = os.path.join(dev, "hidraw")
        if os.path.isdir(raw_dir):
            nodes = os.listdir(raw_dir)
            if nodes:
                return f"/dev/{nodes[0]}"
    return None


def is_nixos_system():
    return os.path.exists("/etc/NIXOS") or shutil.which("nixos-version") is not None


def check_device_access(dev_path):
    if not os.path.exists(dev_path):
        return False, f"Device node {dev_path} does not exist."
    if not os.access(dev_path, os.R_OK | os.W_OK):
        if is_nixos_system():
            return False, (
                f"Permission denied on {dev_path}.\n\n"
                f"NixOS declarative fix (add to /etc/nixos/configuration.nix):\n"
                f"  services.udev.extraRules = ''\n"
                f"    SUBSYSTEM==\"hidraw\", ATTRS{{idVendor}}==\"31b2\", ATTRS{{idProduct}}==\"1112\", MODE=\"0666\", TAG+=\"uaccess\"\n"
                f"    KERNEL==\"hidraw*\", ATTRS{{idVendor}}==\"31b2\", ATTRS{{idProduct}}==\"1112\", MODE=\"0666\", TAG+=\"uaccess\"\n"
                f"  '';\n"
                f"  Then apply: sudo nixos-rebuild switch\n\n"
                f"Temporary session override:\n"
                f"  sudo chmod 666 {dev_path}\n"
            )
        return False, (
            f"Permission denied on {dev_path}.\n\n"
            f"Permanent fix (Ubuntu, Debian, Arch, Fedora, openSUSE, etc.):\n"
            f"  sudo ./install-rules.sh\n"
            f"  or:\n"
            f"  sudo cp 99-tanchjim.rules /etc/udev/rules.d/\n"
            f"  sudo udevadm control --reload-rules && sudo udevadm trigger\n\n"
            f"Temporary session override:\n"
            f"  sudo chmod 666 {dev_path}\n"
        )
    return True, "OK"


def setup_rules(install_path=False):
    bin_path = os.path.join(BASE_DIR, "tanchjim-ctl")

    if install_path:
        if os.geteuid() == 0:
            target = "/usr/local/bin/tanchjim-ctl"
        else:
            local_bin = os.path.expanduser("~/.local/bin")
            os.makedirs(local_bin, exist_ok=True)
            target = os.path.join(local_bin, "tanchjim-ctl")
        try:
            if os.path.islink(target) or os.path.exists(target):
                os.remove(target)
            os.symlink(bin_path, target)
            print(f"[OK] Installed tanchjim-ctl to PATH ({target})")
        except OSError as e:
            print(f"Could not symlink to PATH: {e}", file=sys.stderr)

    if is_nixos_system():
        print("\nNixOS detected.")
        print("On NixOS, /etc/udev/rules.d is managed declaratively by the Nix store.")
        print("Add this one-liner to /etc/nixos/configuration.nix:\n")
        print("  services.udev.extraRules = ''SUBSYSTEM==\"hidraw\", ATTRS{idVendor}==\"31b2\", ATTRS{idProduct}==\"1112\", MODE=\"0666\", TAG+=\"uaccess\"'';\n")
        print("Then apply:")
        print("  sudo nixos-rebuild switch\n")
        return

    rule_path = "/etc/udev/rules.d/99-tanchjim.rules"
    rule_content = (
        "# Udev rule for Tanchjim Bunny DSP (KT Micro 31b2:1112)\n"
        "SUBSYSTEM==\"hidraw\", ATTRS{idVendor}==\"31b2\", ATTRS{idProduct}==\"1112\", MODE=\"0666\", TAG+=\"uaccess\"\n"
        "KERNEL==\"hidraw*\", ATTRS{idVendor}==\"31b2\", ATTRS{idProduct}==\"1112\", MODE=\"0666\", TAG+=\"uaccess\"\n"
    )

    if os.geteuid() == 0:
        try:
            os.makedirs("/etc/udev/rules.d", exist_ok=True)
            with open(rule_path, "w") as f:
                f.write(rule_content)
            os.chmod(rule_path, 0o644)
            if shutil.which("udevadm"):
                subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
                subprocess.run(["udevadm", "trigger", "--subsystem-match=hidraw"], check=False)
            print(f"[OK] Udev rules installed to {rule_path}")
            print("[OK] Reloaded rules. Non-root access active.")
        except OSError as e:
            print(f"Error installing udev rules: {e}", file=sys.stderr)
    else:
        print("\nPermanent Non-Root Access Setup (Ubuntu, Arch, Fedora, Debian, openSUSE, Void, Alpine):")
        print("  sudo ./install-rules.sh --path")
        print("  or run as root: sudo tanchjim-ctl setup-rules --path\n")


def get_mic_gain():
    # 1. PipeWire wpctl (standard on modern Ubuntu, Fedora, Arch, NixOS)
    if shutil.which("wpctl"):
        try:
            out = subprocess.check_output(['wpctl', 'status'], text=True, stderr=subprocess.DEVNULL)
            src_id = None
            in_sources = False
            for line in out.splitlines():
                if 'Sources:' in line:
                    in_sources = True
                    continue
                if in_sources:
                    if line.strip().startswith(('├─', '└─', 'Audio', 'Video')):
                        break
                    if 'TANCHJIM BUNNY' in line.upper():
                        m = re.search(r'(\d+)\.', line)
                        if m:
                            src_id = m.group(1)
                            break
            target = src_id if src_id else '@DEFAULT_AUDIO_SOURCE@'
            vol_out = subprocess.check_output(['wpctl', 'get-volume', str(target)], text=True, stderr=subprocess.DEVNULL)
            m = re.search(r'Volume:\s*(\d+(?:\.\d+)?)', vol_out)
            if m:
                return float(m.group(1))
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

    # 2. PulseAudio pactl
    if shutil.which("pactl"):
        try:
            out = subprocess.check_output(['pactl', 'get-source-volume', '@DEFAULT_SOURCE@'], text=True, stderr=subprocess.DEVNULL)
            m = re.search(r'/\s*(\d+)%\s*/', out)
            if m:
                return int(m.group(1)) / 100.0
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

    # 3. ALSA amixer
    if shutil.which("amixer"):
        try:
            out = subprocess.check_output(['amixer', 'sget', 'Capture'], text=True, stderr=subprocess.DEVNULL)
            m = re.search(r'\[(\d+)%\]', out)
            if m:
                return int(m.group(1)) / 100.0
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

    return 1.0


def set_mic_gain(vol):
    vol = max(0.0, min(1.5, float(vol)))

    if shutil.which("wpctl"):
        try:
            out = subprocess.check_output(['wpctl', 'status'], text=True, stderr=subprocess.DEVNULL)
            src_id = None
            in_sources = False
            for line in out.splitlines():
                if 'Sources:' in line:
                    in_sources = True
                    continue
                if in_sources:
                    if line.strip().startswith(('├─', '└─', 'Audio', 'Video')):
                        break
                    if 'TANCHJIM BUNNY' in line.upper():
                        m = re.search(r'(\d+)\.', line)
                        if m:
                            src_id = m.group(1)
                            break
            target = src_id if src_id else '@DEFAULT_AUDIO_SOURCE@'
            subprocess.check_call(['wpctl', 'set-volume', str(target), f"{vol:.2f}"], stderr=subprocess.DEVNULL)
            return True
        except (subprocess.SubprocessError, OSError):
            pass

    if shutil.which("pactl"):
        try:
            pct = int(round(vol * 100))
            subprocess.check_call(['pactl', 'set-source-volume', '@DEFAULT_SOURCE@', f"{pct}%"], stderr=subprocess.DEVNULL)
            return True
        except (subprocess.SubprocessError, OSError):
            pass

    if shutil.which("amixer"):
        try:
            pct = int(round(vol * 100))
            subprocess.check_call(['amixer', 'sset', 'Capture', f"{pct}%"], stderr=subprocess.DEVNULL)
            return True
        except (subprocess.SubprocessError, OSError):
            pass

    return False


def play_test_noise(duration=5.0, level_db=-22.0):
    fs = 48000
    n_samples = int(fs * max(0.5, min(60.0, float(duration))))
    clamped_db = max(-60.0, min(-6.0, float(level_db)))
    amplitude = int(32767 * (10 ** (clamped_db / 20)))
    fade_len = int(fs * 0.05)

    import tempfile, wave, math, random

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with wave.open(tmp_path, "w") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(fs)
            frames = bytearray()
            for i in range(n_samples):
                fade = 1.0
                if i < fade_len:
                    fade = 0.5 * (1 - math.cos(math.pi * i / fade_len))
                elif i > n_samples - fade_len:
                    fade = 0.5 * (1 - math.cos(math.pi * (n_samples - i) / fade_len))
                sample = int(random.uniform(-1.0, 1.0) * amplitude * fade)
                frames.extend(struct.pack("<hh", sample, sample))
            wf.writeframes(frames)

        target_sink = None
        if shutil.which("wpctl"):
            try:
                out = subprocess.check_output(['wpctl', 'status'], text=True, stderr=subprocess.DEVNULL)
                in_sinks = False
                for line in out.splitlines():
                    if 'Sinks:' in line:
                        in_sinks = True
                        continue
                    if in_sinks:
                        if line.strip().startswith(('├─', '└─', 'Sources:', 'Filters:')):
                            break
                        if 'TANCHJIM BUNNY' in line.upper():
                            m = re.search(r'(\d+)\.', line)
                            if m:
                                target_sink = m.group(1)
                                break
            except Exception:
                pass

        print(f"Playing {duration:.1f}s white noise test signal ({clamped_db:.1f} dBFS) to DSP...")
        if shutil.which("pw-play"):
            cmd = ["pw-play"]
            if target_sink:
                cmd.extend(["--target", str(target_sink)])
            cmd.append(tmp_path)
            subprocess.run(cmd, check=True)
        elif shutil.which("paplay"):
            subprocess.run(["paplay", tmp_path], check=True)
        elif shutil.which("aplay"):
            subprocess.run(["aplay", "-q", tmp_path], check=True)
        else:
            print(f"No audio player utility found. Generated WAV saved at: {tmp_path}")
            return
        print("[OK] Test signal playback complete.")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


class BunnyDSP:
    def __init__(self, dev_path=None):
        if not dev_path:
            dev_path = find_device()
        if not dev_path:
            raise FileNotFoundError("Tanchjim Bunny DSP not detected. Check USB connection.")

        ok, msg = check_device_access(dev_path)
        if not ok:
            raise PermissionError(msg)

        self.dev_path = dev_path
        self.fd = None
        try:
            self.fd = os.open(dev_path, os.O_RDWR)
        except OSError as e:
            raise PermissionError(f"Failed to open {dev_path}: {e}")

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _drain_input(self):
        # Discard stale input reports sitting in the kernel hidraw buffer
        # before issuing a new command.
        while True:
            r, _, _ = select.select([self.fd], [], [], 0.0)
            if not r:
                break
            try:
                os.read(self.fd, 64)
            except OSError:
                break

    def _send_raw_report(self, packet):
        buf = bytes([REPORT_ID]) + bytes(packet)
        os.write(self.fd, buf)

    def _wait_response(self, expected_reg, expected_cmd, timeout=1.0):
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            rem = max(0.0, timeout - elapsed)
            if rem <= 0.0:
                break
            try:
                r, _, _ = select.select([self.fd], [], [], rem)
            except (select.error, InterruptedError):
                continue
            if not r:
                break
            try:
                data = os.read(self.fd, 64)
            except OSError:
                break
            if len(data) >= 11 and data[0] == REPORT_ID:
                payload = data[1:]
                if payload[0] == expected_reg and payload[4] == expected_cmd:
                    return payload
        raise TimeoutError(f"No reply from DSP for reg 0x{expected_reg:02x} cmd 0x{expected_cmd:02x}")

    def read_register(self, reg):
        self._drain_input()
        packet = [reg, 0x00, 0x00, 0x00, CMD_READ, 0x00, 0x00, 0x00, 0x00, 0x00]
        self._send_raw_report(packet)
        return self._wait_response(reg, CMD_READ)

    def read_slot(self):
        self._drain_input()
        packet = [REG_SLOT, 0x00, 0x00, 0x00, CMD_READ, 0x00, 0x03, 0x00, 0x00, 0x00]
        self._send_raw_report(packet)
        resp = self._wait_response(REG_SLOT, CMD_READ)
        return resp[6]

    def read_pregain(self):
        resp = self.read_register(REG_PREGAIN)
        raw = resp[6]
        return raw - 256 if raw > 127 else raw

    def read_band(self, band_idx):
        gf_reg = REG_BASE_BAND + band_idx * 2
        qt_reg = gf_reg + 1

        gf_resp = self.read_register(gf_reg)
        raw_gain = gf_resp[6] | (gf_resp[7] << 8)
        if raw_gain > 0x7FFF:
            raw_gain -= 0x10000
        gain = round(raw_gain / 10.0, 1)
        freq = gf_resp[8] | (gf_resp[9] << 8)

        qt_resp = self.read_register(qt_reg)
        raw_q = qt_resp[6] | (qt_resp[7] << 8)
        q = round(raw_q / 1000.0, 3)
        type_code = qt_resp[8]
        filter_type = FILTER_TYPE_MAP.get(type_code, "PK")

        return {
            "band": band_idx,
            "freq": freq,
            "gain": gain,
            "q": q,
            "type": filter_type,
        }

    def get_all(self):
        slot = self.read_slot()
        pregain = self.read_pregain()
        bands = [self.read_band(i) for i in range(NUM_BANDS)]
        return {
            "slot": slot,
            "pregain": pregain,
            "bands": bands,
        }

    def write_gain_freq(self, band_idx, freq, gain):
        reg = REG_BASE_BAND + band_idx * 2
        clamped_gain = max(-12.0, min(12.0, float(gain)))
        clamped_freq = max(20.0, min(20000.0, float(freq)))

        raw_gain = int(round(clamped_gain * 10))
        gain_bytes = struct.pack("<h", raw_gain)
        freq_bytes = struct.pack("<H", int(round(clamped_freq)))
        packet = [
            reg, 0x00, 0x00, 0x00, CMD_WRITE, 0x00,
            gain_bytes[0], gain_bytes[1],
            freq_bytes[0], freq_bytes[1]
        ]
        self._send_raw_report(packet)
        time.sleep(0.01)

    def write_q_type(self, band_idx, q, filter_type):
        reg = REG_BASE_BAND + band_idx * 2 + 1
        clamped_q = max(0.1, min(10.0, float(q)))
        raw_q = int(round(clamped_q * 1000))
        q_bytes = struct.pack("<H", raw_q)

        type_code = FILTER_TYPE_REV.get(str(filter_type).strip().upper(), 0)
        packet = [
            reg, 0x00, 0x00, 0x00, CMD_WRITE, 0x00,
            q_bytes[0], q_bytes[1],
            type_code, 0x00
        ]
        self._send_raw_report(packet)
        time.sleep(0.01)

    def write_pregain(self, gain):
        clamped = max(-12.0, min(12.0, float(gain)))
        val = int(round(clamped)) & 0xFF
        packet = [REG_PREGAIN, 0x00, 0x00, 0x00, CMD_WRITE, 0x00, val, 0x00, 0x00, 0x00]
        self._send_raw_report(packet)
        time.sleep(0.01)

    def enable_eq(self, slot=0x03):
        packet = [REG_SLOT, 0x00, 0x00, 0x00, CMD_WRITE, 0x00, slot, 0x00, 0x00, 0x00]
        self._send_raw_report(packet)
        time.sleep(0.02)

    def commit(self):
        # Flush working SRAM to onboard EEPROM. Requires 350-400ms for internal cycle.
        packet = [0x00, 0x00, 0x00, 0x00, CMD_COMMIT, 0x00, 0x00, 0x00, 0x00, 0x00]
        self._send_raw_report(packet)
        time.sleep(0.4)

    def reset_clear(self):
        packet = [0x00, 0x00, 0x00, 0x00, CMD_CLEAR, 0x00, 0x00, 0x00, 0x00, 0x00]
        self._send_raw_report(packet)
        time.sleep(0.2)

    def apply_profile(self, profile):
        # Force hardware DSP to custom profile mode
        self.enable_eq(0x03)

        raw_bands = profile.get("bands") or []
        for i in range(min(NUM_BANDS, len(raw_bands))):
            b = raw_bands[i]
            if not isinstance(b, dict):
                continue
            freq = float(b.get("freq", 1000))
            gain = float(b.get("gain", 0.0))
            q = float(b.get("q", 1.0))
            ftype = str(b.get("type", "PK"))
            self.write_gain_freq(i, freq, gain)
            self.write_q_type(i, q, ftype)

        # Zero out any remaining bands up to 8 so old curves don't bleed through
        for i in range(len(raw_bands), NUM_BANDS):
            self.write_gain_freq(i, 1000, 0.0)
            self.write_q_type(i, 1.0, "PK")

        pregain = float(profile.get("pregain", 0.0))
        self.write_pregain(pregain)
        self.commit()

    def dump_all_registers(self):
        raw_bytes = bytearray()
        regs = {}
        for r in range(256):
            resp = self.read_register(r)
            chunk = resp[6:10]
            raw_bytes.extend(chunk)
            regs[f"0x{r:02x}"] = [f"0x{b:02x}" for b in chunk]

        def get_str(start_reg, count):
            b = bytearray()
            for r in range(start_reg, start_reg + count):
                hex_str = "".join(x.replace("0x", "") for x in regs[f"0x{r:02x}"])
                b.extend(bytes.fromhex(hex_str))
            return b.split(b"\x00")[0].decode("utf-8", errors="replace").strip()

        meta = {
            "device": "Tanchjim Bunny DSP",
            "chip": "KTMicro KT0210",
            "firmware_version": get_str(0x04, 2),
            "build_date": get_str(0x08, 3),
            "build_time": get_str(0x0C, 2),
            "commit_hash": get_str(0x10, 2),
            "vendor": get_str(0x40, 2),
            "product": get_str(0x48, 5),
            "batch_id": get_str(0x50, 3),
            "usb_vid_pid": "31b2:1112",
            "active_slot": int(regs.get("0x24", ["0x03"])[0], 16),
            "raw_registers": regs
        }
        return bytes(raw_bytes), meta



def parse_parametric_eq(text):
    # Parses standard AutoEQ / Squiglink parametric text files:
    #   Preamp: -3.5 dB
    #   Filter 1: ON PK Fc 1300 Hz Gain 1.0 dB Q 1.00
    #   Filter 2: ON LSC Fc 105 Hz Gain 4.5 dB Q 0.70
    pregain = 0.0
    bands = []

    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue

        pre_m = re.search(r"Preamp:\s*([+-]?\d+(?:\.\d+)?)\s*(?:dB)?", line, re.I)
        if pre_m:
            pregain = float(pre_m.group(1))
            continue

        filt_m = re.search(
            r"Filter\s+\d+:\s*(?:ON\s+)?([A-Z_]+)?\s*(?:Fc\s+)?(\d+(?:\.\d+)?)\s*(?:Hz)?\s*(?:Gain\s+)?([+-]?\d+(?:\.\d+)?)\s*(?:dB)?\s*(?:Q\s+)?(\d+(?:\.\d+)?)?",
            line,
            re.I
        )
        if filt_m:
            raw_type = (filt_m.group(1) or "PK").upper()
            if raw_type in ("LSQ", "LSC", "LOW_SHELF", "LS"):
                ftype = "LSQ"
            elif raw_type in ("HSQ", "HSC", "HIGH_SHELF", "HS"):
                ftype = "HSQ"
            else:
                ftype = "PK"

            freq = float(filt_m.group(2))
            gain = float(filt_m.group(3))
            q = float(filt_m.group(4)) if filt_m.group(4) else 1.41

            bands.append({
                "band": len(bands),
                "freq": round(freq),
                "gain": gain,
                "q": q,
                "type": ftype
            })

    return {
        "pregain": pregain,
        "bands": bands[:NUM_BANDS]
    }


def print_status_table(status):
    print("-------------------------------------------------")
    print("       TANCHJIM BUNNY DSP - Current Status       ")
    print("-------------------------------------------------")
    mode_str = "Custom Active" if status.get("slot") == 3 else "Bypass/Disabled"
    print(f" Mode / Slot : {status.get('slot')} ({mode_str})")
    print(f" Pregain     : {status.get('pregain', 0.0):+.1f} dB")
    vol = get_mic_gain()
    print(f" Mic Gain    : {int(round(vol * 100))}% ({vol:.2f})")
    print("-------------------------------------------------")
    print(" Band |   Frequency   |    Gain    |   Q   |  Type ")
    print("-------------------------------------------------")
    for b in status.get("bands", []):
        print(f"  {b['band']}   |  {b['freq']:>5} Hz    |  {b['gain']:>+5.1f} dB | {b['q']:>5.3f} |  {b['type']}")
    print("-------------------------------------------------")


class DSPRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, format, *args):
        # Suppress noisy HTTP request polling logs
        pass

    def _send_json(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_err(self, message, code=500):
        self._send_json({"ok": False, "error": str(message)}, code=code)

    def do_GET(self):
        if self.path in ("", "/"):
            self.path = "/index.html"

        if self.path == "/api/status":
            try:
                with BunnyDSP() as dsp:
                    self._send_json({"ok": True, "data": dsp.get_all()})
            except (OSError, TimeoutError, PermissionError) as e:
                self._send_err(e, 500)
            return

        if self.path == "/api/mic-gain":
            vol = get_mic_gain()
            self._send_json({"ok": True, "volume": vol})
            return

        if self.path == "/api/presets":
            presets = []
            if os.path.isdir(PRESETS_DIR):
                for f in sorted(os.listdir(PRESETS_DIR)):
                    if not f.endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(PRESETS_DIR, f), "r") as pf:
                            presets.append(json.load(pf))
                    except (OSError, json.JSONDecodeError):
                        continue
            self._send_json({"ok": True, "presets": presets})
            return

        super().do_GET()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 1024 * 1024:  # 1MB sanity limit
            self._send_err("Payload too large", 413)
            return

        body = self.rfile.read(content_len).decode("utf-8", errors="replace")

        if self.path == "/api/apply":
            try:
                profile = json.loads(body)
                with BunnyDSP() as dsp:
                    dsp.apply_profile(profile)
                self._send_json({"ok": True, "message": "Applied and saved to hardware."})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_err(f"Malformed profile: {e}", 400)
            except (OSError, TimeoutError, PermissionError) as e:
                self._send_err(e, 500)
            return

        if self.path == "/api/mic-gain":
            try:
                pdata = json.loads(body)
                vol = float(pdata.get("volume", 1.0))
                ok = set_mic_gain(vol)
                self._send_json({"ok": ok, "volume": vol})
            except (json.JSONDecodeError, ValueError) as e:
                self._send_err(f"Invalid volume payload: {e}", 400)
            return

        if self.path == "/api/reset":
            try:
                with BunnyDSP() as dsp:
                    dsp.reset_clear()
                    dsp.apply_profile({
                        "pregain": 0.0,
                        "bands": [
                            {"freq": 32, "gain": 0.0, "q": 1.0, "type": "PK"},
                            {"freq": 64, "gain": 0.0, "q": 1.0, "type": "PK"},
                            {"freq": 125, "gain": 0.0, "q": 1.0, "type": "PK"},
                            {"freq": 250, "gain": 0.0, "q": 1.0, "type": "PK"},
                            {"freq": 500, "gain": 0.0, "q": 1.0, "type": "PK"},
                            {"freq": 1000, "gain": 0.0, "q": 1.0, "type": "PK"},
                            {"freq": 4000, "gain": 0.0, "q": 1.0, "type": "PK"},
                            {"freq": 10000, "gain": 0.0, "q": 1.0, "type": "PK"},
                        ]
                    })
                self._send_json({"ok": True, "message": "Reset to flat"})
            except (OSError, TimeoutError, PermissionError) as e:
                self._send_err(e, 500)
            return

        self._send_err("Not found", 404)


def run_web_server(port=8844, open_browser=True):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, DSPRequestHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"Web interface running at: {url}")
    print("Supports Helium / Chrome WebHID and local backend mode.")
    print("Press Ctrl+C to stop.\n")

    if open_browser:
        def _launch():
            time.sleep(0.4)
            # 1. Helium browser
            helium = shutil.which("helium")
            if not helium and os.path.exists("/run/current-system/sw/bin/helium"):
                helium = "/run/current-system/sw/bin/helium"
            if helium:
                os.system(f"{helium} {url} >/dev/null 2>&1 &")
                return

            # 2. Chromium-based browsers (WebHID support)
            for b in ("google-chrome-stable", "google-chrome", "chromium-browser", "chromium", "brave-browser", "brave", "microsoft-edge-stable"):
                cmd = shutil.which(b)
                if cmd:
                    os.system(f"{cmd} {url} >/dev/null 2>&1 &")
                    return

            # 3. Default browser fallback
            webbrowser.open(url)

        threading.Thread(target=_launch, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="Tanchjim Bunny DSP Controller & Equalizer for Linux"
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    p_setup = sub.add_parser("setup-rules", help="Configure permanent access and optionally add to PATH")
    p_setup.add_argument("-p", "--path", action="store_true", help="Symlink tanchjim-ctl into system or user PATH")
    sub.add_parser("status", help="Show current EQ settings and status from hardware")

    p_get = sub.add_parser("get", help="Export current EQ profile to JSON or AutoEQ text")
    p_get.add_argument("--format", choices=["json", "autoeq"], default="json")
    p_get.add_argument("-o", "--output", help="Output file path")

    p_band = sub.add_parser("set-band", help="Configure an individual EQ band (0-7)")
    p_band.add_argument("band", type=int, choices=range(8), help="Band index (0 to 7)")
    p_band.add_argument("--freq", type=float, required=True, help="Frequency in Hz (20 - 20000)")
    p_band.add_argument("--gain", type=float, required=True, help="Gain in dB (-12.0 to +12.0)")
    p_band.add_argument("--q", type=float, default=1.0, help="Q factor (0.1 to 10.0)")
    p_band.add_argument("--type", choices=["PK", "LSQ", "HSQ"], default="PK", help="Filter type")

    p_pre = sub.add_parser("set-pregain", help="Set digital preamp gain in dB")
    p_pre.add_argument("gain", type=float, help="Pregain in dB (-12 to +12)")

    p_mic = sub.add_parser("mic-gain", help="Get or set microphone capture gain")
    p_mic.add_argument("volume", nargs="?", help="Microphone volume (e.g. 80, 80%%, or 0.8)")

    sub.add_parser("presets", help="List all available presets")

    p_app = sub.add_parser("apply", help="Apply a preset (by name, local file, or URL)")
    p_app.add_argument("target", help="Preset name, file path (.json / .txt), or URL")

    sub.add_parser("reset", help="Reset all EQ bands to flat 0 dB")
    sub.add_parser("save", help="Commit current settings to chip's non-volatile flash")

    p_dump = sub.add_parser("dump", help="Dump all 256 hardware registers (1KB binary, JSON, or hex)")
    p_dump.add_argument("-o", "--output", help="Output file path (.bin or .json)")
    p_dump.add_argument("--format", choices=["hex", "bin", "json"], default="hex", help="Output format (default: hex)")

    p_noise = sub.add_parser("test-noise", help="Play a white noise test signal through the DSP")
    p_noise.add_argument("duration", type=float, nargs="?", default=5.0, help="Duration in seconds (default: 5.0)")
    p_noise.add_argument("--level", type=float, default=-22.0, help="Signal level in dBFS (default: -22.0 dBFS)")

    p_web = sub.add_parser("web", help="Launch interactive graphical web interface")
    p_web.add_argument("--port", type=int, default=8844, help="HTTP server port (default: 8844)")
    p_web.add_argument("--no-browser", action="store_true", help="Do not automatically open browser")

    args = parser.parse_args()

    if not args.command or args.command == "web":
        run_web_server(
            port=getattr(args, "port", 8844) or 8844,
            open_browser=not getattr(args, "no_browser", False)
        )
        return

    if args.command == "setup-rules":
        setup_rules(install_path=args.path)
        return

    try:
        if args.command == "status":
            with BunnyDSP() as dsp:
                print_status_table(dsp.get_all())

        elif args.command == "get":
            with BunnyDSP() as dsp:
                data = dsp.get_all()
                if args.format == "json":
                    out_text = json.dumps(data, indent=2)
                else:
                    lines = [f"Preamp: {data['pregain']:+.1f} dB"]
                    for b in data.get("bands", []):
                        lines.append(f"Filter {b['band']+1}: ON {b['type']} Fc {b['freq']} Hz Gain {b['gain']:+.1f} dB Q {b['q']:.2f}")
                    out_text = "\n".join(lines)
                if args.output:
                    with open(args.output, "w") as f:
                        f.write(out_text + "\n")
                    print(f"Saved to {args.output}")
                else:
                    print(out_text)

        elif args.command == "set-band":
            with BunnyDSP() as dsp:
                current = dsp.get_all()
                current["bands"][args.band] = {
                    "band": args.band,
                    "freq": args.freq,
                    "gain": args.gain,
                    "q": args.q,
                    "type": args.type
                }
                dsp.apply_profile(current)
                print(f"[OK] Band {args.band} set to {args.freq}Hz, {args.gain:+.1f}dB, Q={args.q}, {args.type} and saved.")

        elif args.command == "set-pregain":
            with BunnyDSP() as dsp:
                current = dsp.get_all()
                current["pregain"] = args.gain
                dsp.apply_profile(current)
                print(f"[OK] Pregain set to {args.gain:+.1f} dB and saved.")

        elif args.command == "mic-gain":
            if args.volume is None:
                vol = get_mic_gain()
                print(f"Current Microphone Gain: {int(round(vol * 100))}% ({vol:.2f})")
            else:
                raw_v = args.volume.rstrip('%')
                try:
                    v_val = float(raw_v)
                    if v_val > 1.5:
                        v_val = v_val / 100.0
                    ok = set_mic_gain(v_val)
                    if ok:
                        print(f"[OK] Microphone gain set to {int(round(v_val * 100))}% ({v_val:.2f}).")
                    else:
                        print("Failed to set microphone gain.", file=sys.stderr)
                except ValueError:
                    print(f"Invalid volume value: {args.volume}. Specify e.g. 80% or 0.8", file=sys.stderr)

        elif args.command == "presets":
            print("\nAvailable Presets:")
            print("-------------------------------------------------")
            if os.path.isdir(PRESETS_DIR):
                for f in sorted(os.listdir(PRESETS_DIR)):
                    if not f.endswith(".json"):
                        continue
                    p_path = os.path.join(PRESETS_DIR, f)
                    try:
                        with open(p_path, "r") as pf:
                            pj = json.load(pf)
                            name = pj.get("name", f)
                            desc = pj.get("description", "")
                            p_id = f[:-5]
                            print(f" - {p_id:<16} : {name}")
                            if desc:
                                print(f"   {' ':16}   ({desc})")
                    except (OSError, json.JSONDecodeError):
                        pass
            print("-------------------------------------------------")
            print("Apply any preset with: tanchjim-ctl apply <preset_id>\n")

        elif args.command == "apply":
            target = args.target
            content = None

            if target.startswith(("http://", "https://")):
                import urllib.request
                print(f"Fetching preset from {target}...")
                req = urllib.request.Request(target, headers={"User-Agent": "tanchjim-ctl/1.0"})
                with urllib.request.urlopen(req) as resp:
                    content = resp.read().decode("utf-8")
            elif os.path.exists(target):
                with open(target, "r") as f:
                    content = f.read()
            else:
                for candidate in (
                    os.path.join(PRESETS_DIR, f"{target}.json"),
                    os.path.join(PRESETS_DIR, f"{target}.txt"),
                    os.path.join(PRESETS_DIR, target),
                ):
                    if os.path.exists(candidate):
                        with open(candidate, "r") as f:
                            content = f.read()
                        break

            if content is None:
                print(f"Error: Preset or file '{target}' not found.", file=sys.stderr)
                print("Run 'tanchjim-ctl presets' to see available presets.", file=sys.stderr)
                sys.exit(1)

            try:
                profile = json.loads(content)
            except json.JSONDecodeError:
                profile = parse_parametric_eq(content)

            with BunnyDSP() as dsp:
                dsp.apply_profile(profile)
                print(f"[OK] Applied profile '{target}' to hardware flash.")

        elif args.command == "reset":
            with BunnyDSP() as dsp:
                dsp.reset_clear()
                dsp.apply_profile({
                    "pregain": 0.0,
                    "bands": [
                        {"freq": 32, "gain": 0.0, "q": 1.0, "type": "PK"},
                        {"freq": 64, "gain": 0.0, "q": 1.0, "type": "PK"},
                        {"freq": 125, "gain": 0.0, "q": 1.0, "type": "PK"},
                        {"freq": 250, "gain": 0.0, "q": 1.0, "type": "PK"},
                        {"freq": 500, "gain": 0.0, "q": 1.0, "type": "PK"},
                        {"freq": 1000, "gain": 0.0, "q": 1.0, "type": "PK"},
                        {"freq": 4000, "gain": 0.0, "q": 1.0, "type": "PK"},
                        {"freq": 10000, "gain": 0.0, "q": 1.0, "type": "PK"},
                    ]
                })
                print("[OK] Reset to flat response.")

        elif args.command == "save":
            with BunnyDSP() as dsp:
                dsp.commit()
                print("[OK] Flash updated.")

        elif args.command == "dump":
            with BunnyDSP() as dsp:
                raw_bytes, meta = dsp.dump_all_registers()
                fmt = args.format
                if args.output:
                    if args.output.endswith(".bin"):
                        fmt = "bin"
                    elif args.output.endswith(".json"):
                        fmt = "json"

                if fmt == "bin":
                    out_path = args.output or "bunny_dsp_dump.bin"
                    with open(out_path, "wb") as f:
                        f.write(raw_bytes)
                    print(f"[OK] Dumped {len(raw_bytes)} bytes raw memory to {out_path}")
                elif fmt == "json":
                    out_text = json.dumps(meta, indent=2)
                    if args.output:
                        with open(args.output, "w") as f:
                            f.write(out_text + "\n")
                        print(f"[OK] Dumped JSON registers and metadata to {args.output}")
                    else:
                        print(out_text)
                else:
                    print("\n-------------------------------------------------")
                    print("         TANCHJIM BUNNY DSP - Hardware Dump      ")
                    print("-------------------------------------------------")
                    print(f" Device    : {meta['device']} ({meta['chip']})")
                    print(f" Firmware  : v{meta['firmware_version']} ({meta['commit_hash']})")
                    print(f" Build     : {meta['build_date']} {meta['build_time']}")
                    print(f" Vendor    : {meta['vendor']}")
                    print(f" Product   : {meta['product']}")
                    print(f" Batch ID  : {meta['batch_id']}")
                    print("-------------------------------------------------")
                    for offset in range(0, len(raw_bytes), 16):
                        chunk = raw_bytes[offset:offset+16]
                        hex_str = " ".join(f"{b:02x}" for b in chunk)
                        ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
                        reg_start = offset // 4
                        print(f"0x{reg_start:02x}-0x{reg_start+3:02x}  ({offset:04x}):  {hex_str:<48}  |{ascii_str}|")
                    print("-------------------------------------------------\n")
                    if args.output:
                        with open(args.output, "wb") as f:
                            f.write(raw_bytes)
                        print(f"[OK] Saved binary dump to {args.output}")

        elif args.command == "test-noise":
            play_test_noise(duration=args.duration, level_db=args.level)



    except (PermissionError, TimeoutError, FileNotFoundError) as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"\nI/O error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
