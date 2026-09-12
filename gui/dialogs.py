import json
import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib


class AutoEqImportDialog(Gtk.Window):
    def __init__(self, parent, on_apply_callback):
        super().__init__(transient_for=parent, modal=True, title="Import Parametric EQ")
        self.set_default_size(520, 420)
        self.on_apply_callback = on_apply_callback

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        self.set_child(content)

        desc = Gtk.Label(
            label="Paste AutoEq, Squiglink, or Peace parametric text below, or choose a file:",
            wrap=True,
            xalign=0.0
        )
        desc.add_css_class("dim-label")
        content.append(desc)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.add_css_class("card")

        self.text_view = Gtk.TextView()
        self.text_view.set_monospace(True)
        self.text_view.set_left_margin(10)
        self.text_view.set_right_margin(10)
        self.text_view.set_top_margin(10)
        self.text_view.set_bottom_margin(10)
        self.text_view.get_buffer().set_text(
            "Preamp: -3.5 dB\n"
            "Filter 1: ON LSQ Fc 45 Hz Gain 4.5 dB Q 0.70\n"
            "Filter 2: ON PK Fc 180 Hz Gain -1.5 dB Q 1.00\n"
            "Filter 3: ON PK Fc 1300 Hz Gain 1.0 dB Q 1.00\n"
            "Filter 4: ON PK Fc 6000 Hz Gain -4.0 dB Q 3.00\n"
            "Filter 5: ON PK Fc 12000 Hz Gain -8.0 dB Q 3.00\n"
        )
        scrolled.set_child(self.text_view)
        content.append(scrolled)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.append(btn_box)

        load_file_btn = Gtk.Button(label="Open File...")
        load_file_btn.connect("clicked", self._on_choose_file)
        btn_box.append(load_file_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btn_box.append(spacer)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.destroy())
        btn_box.append(cancel_btn)

        apply_btn = Gtk.Button(label="Apply to EQ")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self._on_apply)
        btn_box.append(apply_btn)

    def _on_choose_file(self, btn):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Open Preset File")
        filter_eq = Gtk.FileFilter()
        filter_eq.set_name("EQ Presets (*.txt, *.json)")
        filter_eq.add_pattern("*.txt")
        filter_eq.add_pattern("*.json")
        filters = Gio_filters = [filter_eq]

        def on_open_finish(source, res):
            try:
                gfile = source.open_finish(res)
                if gfile:
                    path = gfile.get_path()
                    with open(path, "r") as f:
                        text = f.read()
                    self.text_view.get_buffer().set_text(text)
            except Exception:
                pass

        dialog.open(self, None, on_open_finish)

    def _on_apply(self, btn):
        buf = self.text_view.get_buffer()
        start, end = buf.get_bounds()
        text = buf.get_text(start, end, True)
        if text.strip() and self.on_apply_callback:
            self.on_apply_callback(text)
        self.destroy()


class HardwareDumpDialog(Gtk.Window):
    def __init__(self, parent, raw_bytes, meta):
        super().__init__(transient_for=parent, modal=True, title="KT0210 Hardware Register Dump")
        self.set_default_size(680, 520)
        self.raw_bytes = raw_bytes
        self.meta = meta

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        self.set_child(box)

        meta_card = Adw.PreferencesGroup()
        meta_card.set_title("KT0210 Identity & Firmware Info")
        meta_card.set_description(
            f"Chip: KT0210 | Firmware v{meta.get('firmware_version', '1.0.1')} ({meta.get('commit_hash', '')})\n"
            f"Build: {meta.get('build_date', '')} {meta.get('build_time', '')} | "
            f"Batch: {meta.get('batch_id', '')} | USB: {meta.get('usb_vid_pid', '31b2:1112')}"
        )
        box.append(meta_card)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.add_css_class("card")

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.set_left_margin(8)
        text_view.set_right_margin(8)
        text_view.set_top_margin(8)
        text_view.set_bottom_margin(8)

        lines = []
        for offset in range(0, len(raw_bytes), 16):
            chunk = raw_bytes[offset:offset+16]
            hex_str = " ".join(f"{b:02x}" for b in chunk)
            ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
            reg_start = offset // 4
            lines.append(f"0x{reg_start:02x}-0x{reg_start+3:02x} ({offset:04x}): {hex_str:<48} |{ascii_str}|")

        text_view.get_buffer().set_text("\n".join(lines))
        scrolled.set_child(text_view)
        box.append(scrolled)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(btn_box)

        export_bin_btn = Gtk.Button(label="Export Raw .bin (1KB)")
        export_bin_btn.connect("clicked", self._on_export_bin)
        btn_box.append(export_bin_btn)

        export_json_btn = Gtk.Button(label="Export .json")
        export_json_btn.connect("clicked", self._on_export_json)
        btn_box.append(export_json_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btn_box.append(spacer)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda b: self.destroy())
        btn_box.append(close_btn)

    def _on_export_bin(self, btn):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Save Binary Dump")
        dialog.set_initial_name("kt0210_dump.bin")

        def on_save_finish(source, res):
            try:
                gfile = source.save_finish(res)
                if gfile:
                    path = gfile.get_path()
                    with open(path, "wb") as f:
                        f.write(self.raw_bytes)
            except Exception:
                pass

        dialog.save(self, None, on_save_finish)

    def _on_export_json(self, btn):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Save JSON Dump")
        dialog.set_initial_name("kt0210_dump.json")

        def on_save_finish(source, res):
            try:
                gfile = source.save_finish(res)
                if gfile:
                    path = gfile.get_path()
                    with open(path, "w") as f:
                        json.dump(self.meta, f, indent=2)
            except Exception:
                pass

        dialog.save(self, None, on_save_finish)


def show_about_dialog(parent):
    if hasattr(Adw, "AboutDialog"):
        about = Adw.AboutDialog.new()
    else:
        about = Adw.AboutWindow.new()
        about.set_transient_for(parent)

    about.set_application_name("KT0210")
    about.set_version("1.0.1")
    about.set_developer_name("onbot")
    about.set_issue_url("https://github.com/onbot7/BunnyDSPLinux/issues")
    about.set_website("https://github.com/onbot7/BunnyDSPLinux")
    about.set_license_type(Gtk.License.MIT_X11)
    about.set_comments(
        "Supported: Tanchjim Bunny DSP\n\n"
        "Hardware parametric equalizer and DSP controller on Linux.\n\n"
        "UI layout inspired by not_ayan99's OpenAula interface."
    )

    if hasattr(Adw, "AboutDialog"):
        about.present(parent)
    else:
        about.present()


class SupportedDevicesDialog(Gtk.Window):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Supported Devices")
        self.set_default_size(480, 240)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        self.set_child(box)

        group = Adw.PreferencesGroup()
        group.set_title("Supported Hardware")
        box.append(group)

        row1 = Adw.ActionRow()
        row1.set_title("Tanchjim Bunny DSP")
        row1.set_subtitle("Type-C In-Ear Monitor / DSP Cable (USB ID 31b2:1112)")
        icon1 = Gtk.Image.new_from_icon_name("audio-headphones-symbolic")
        row1.add_prefix(icon1)
        badge1 = Gtk.Label(label="Supported")
        badge1.add_css_class("accent")
        row1.add_suffix(badge1)
        group.add(row1)

        row2 = Adw.ActionRow()
        row2.set_title("KT Micro KT0210 Codec")
        row2.set_subtitle("Generic KT0210 USB DAC / DSP evaluation devices &amp; cables")
        icon2 = Gtk.Image.new_from_icon_name("audio-card-symbolic")
        row2.add_prefix(icon2)
        badge2 = Gtk.Label(label="Compatible")
        badge2.add_css_class("dim-label")
        row2.add_suffix(badge2)
        group.add(row2)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        box.append(spacer)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_box.set_halign(Gtk.Align.END)
        box.append(btn_box)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda b: self.destroy())
        btn_box.append(close_btn)


