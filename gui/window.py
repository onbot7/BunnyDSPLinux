import json
import os
import sys
import threading
import time
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio

from gui.graph import FrequencyResponseGraph
from gui.channel_strips import ChannelsGrid
from gui.presets_view import PresetsView
from gui.dialogs import AutoEqImportDialog, HardwareDumpDialog, SupportedDevicesDialog, show_about_dialog
import tanchjim_bunny


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="KT0210")
        self.set_default_size(980, 820)

        self.dsp = None
        self.hardware_lock = threading.Lock()
        self.pending_updates = {}
        self.debounce_timer_id = None

        # Data model (8 bands)
        self.pregain = 0.0
        self.bands = [
            {"freq": 80, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 150, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 500, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 1000, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 2800, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 4500, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 5500, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 12000, "gain": 0.0, "q": 1.0, "type": "HSQ"},
        ]

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.toast_overlay.set_child(main_box)

        self.header_bar = Adw.HeaderBar()
        main_box.append(self.header_bar)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Read hardware state")
        refresh_btn.connect("clicked", lambda b: self._read_hardware_async())
        self.header_bar.pack_start(refresh_btn)

        self.app_title_lbl = Gtk.Label(label="KT0210")
        self.app_title_lbl.set_halign(Gtk.Align.START)
        self.app_title_lbl.add_css_class("heading")
        self.app_title_lbl.set_margin_start(4)
        self.header_bar.pack_start(self.app_title_lbl)

        self.center_status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.center_status_box.set_valign(Gtk.Align.CENTER)
        self.center_status_box.set_halign(Gtk.Align.CENTER)

        self.status_icon = Gtk.Image()
        self.status_icon.set_from_icon_name("network-offline-symbolic")
        self.center_status_box.append(self.status_icon)

        self.status_lbl = Gtk.Label(label="Disconnected")
        self.status_lbl.add_css_class("dim-label")
        self.center_status_box.append(self.status_lbl)

        self.header_bar.set_title_widget(self.center_status_box)

        self.save_btn = Gtk.Button(label="Save to Hardware")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.set_tooltip_text("Burn active EQ profile into KT0210 EEPROM flash")
        self.save_btn.connect("clicked", self._on_save_hardware)
        self.header_bar.pack_end(self.save_btn)

        menu = Gio.Menu()
        menu.append("Supported Devices", "app.supported")
        menu.append("Report an Issue", "app.issue")
        menu.append("Hardware Register Dump", "app.dump")
        menu.append("White Noise Test (5s)", "app.noise")
        menu.append("Reset Flat", "app.reset")
        menu.append("About KT0210", "app.about")

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        self.header_bar.pack_end(menu_btn)

        pinned_graph_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        pinned_graph_box.set_margin_top(8)
        pinned_graph_box.set_margin_start(16)
        pinned_graph_box.set_margin_end(16)
        pinned_graph_box.set_margin_bottom(6)

        graph_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        graph_card.add_css_class("card")
        self.graph = FrequencyResponseGraph(on_band_changed=self._on_graph_band_changed)
        graph_card.append(self.graph)
        pinned_graph_box.append(graph_card)
        main_box.append(pinned_graph_box)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_vexpand(True)
        scrolled_window.set_hexpand(True)
        main_box.append(scrolled_window)

        scroll_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        scroll_content.set_margin_top(4)
        scroll_content.set_margin_bottom(24)
        scroll_content.set_margin_start(16)
        scroll_content.set_margin_end(16)
        scrolled_window.set_child(scroll_content)

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        scroll_content.append(ctrl_box)

        pre_group = Adw.PreferencesGroup()
        pre_group.set_hexpand(True)
        ctrl_box.append(pre_group)

        self.preamp_row = Adw.ActionRow(title="Preamp Gain", subtitle="Attenuate digital volume to prevent clipping")
        pre_group.add(self.preamp_row)

        self.preamp_adj = Gtk.Adjustment(value=0.0, lower=-12.0, upper=6.0, step_increment=0.5, page_increment=1.0)
        self.preamp_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.preamp_adj)
        self.preamp_scale.set_size_request(160, -1)
        self.preamp_scale.set_draw_value(False)
        self.preamp_scale.connect("value-changed", self._on_preamp_changed)
        self.preamp_row.add_suffix(self.preamp_scale)

        self.preamp_lbl = Gtk.Label(label="0.0 dB")
        self.preamp_lbl.set_size_request(60, -1)
        self.preamp_lbl.set_xalign(1.0)
        self.preamp_row.add_suffix(self.preamp_lbl)

        mic_group = Adw.PreferencesGroup()
        mic_group.set_hexpand(True)
        ctrl_box.append(mic_group)

        self.mic_row = Adw.ActionRow(title="Microphone Gain", subtitle="Inline microphone capture volume")
        mic_group.add(self.mic_row)

        self.mic_adj = Gtk.Adjustment(value=100.0, lower=0.0, upper=100.0, step_increment=1.0, page_increment=5.0)
        self.mic_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.mic_adj)
        self.mic_scale.set_size_request(160, -1)
        self.mic_scale.set_draw_value(False)
        self.mic_scale.connect("value-changed", self._on_mic_changed)
        self.mic_row.add_suffix(self.mic_scale)

        self.mic_lbl = Gtk.Label(label="100%")
        self.mic_lbl.set_size_request(50, -1)
        self.mic_lbl.set_xalign(1.0)
        self.mic_row.add_suffix(self.mic_lbl)

        bands_header = Gtk.Label(label="<b>Hardware Filter Bands (8-Band PEQ)</b>", use_markup=True, xalign=0.0)
        scroll_content.append(bands_header)

        self.channels_grid = ChannelsGrid(on_band_changed_callback=self._on_channel_band_changed)
        scroll_content.append(self.channels_grid)

        self.presets_view = PresetsView(
            main_window=self,
            on_apply_callback=self._on_apply_preset,
            on_flash_callback=self._on_flash_preset,
            scrolled=False
        )
        scroll_content.append(self.presets_view)

        # Initial hardware read
        GLib.idle_add(self._read_hardware_async)
        GLib.idle_add(self._update_mic_gain_ui)

    def show_toast(self, text):
        toast = Adw.Toast.new(text)
        toast.set_timeout(3)
        self.toast_overlay.add_toast(toast)

    def _update_mic_gain_ui(self):
        try:
            vol = tanchjim_bunny.get_mic_gain()
            pct = int(round(vol * 100))
            self.mic_adj.set_value(pct)
            self.mic_lbl.set_text(f"{pct}%")
        except Exception:
            pass

    def _read_hardware_async(self):
        def worker():
            try:
                with self.hardware_lock:
                    with tanchjim_bunny.BunnyDSP() as dsp:
                        data = dsp.get_all()
                GLib.idle_add(self._apply_hardware_read, data, None)
            except Exception as e:
                GLib.idle_add(self._apply_hardware_read, None, str(e))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _apply_hardware_read(self, data, error):
        if error or not data:
            self.status_icon.set_from_icon_name("network-offline-symbolic")
            self.status_lbl.set_text("Disconnected")
            self.status_lbl.remove_css_class("accent")
            self.status_lbl.add_css_class("dim-label")
            self.center_status_box.set_tooltip_text(f"Device error: {error or 'Not detected'}\nCheck USB connection")
            self.show_toast("Device not detected. Check USB connection.")
            return

        slot = data.get("slot", 3)
        self.status_icon.set_from_icon_name("emblem-ok-symbolic")
        self.status_lbl.set_text(f"Connected (Slot {slot})")
        self.status_lbl.remove_css_class("dim-label")
        self.status_lbl.add_css_class("accent")
        self.center_status_box.set_tooltip_text(f"KT0210 active (Slot {slot})\nClick refresh button to re-sync")
        self.pregain = data.get("pregain", 0.0)
        self.bands = data.get("bands", self.bands)

        self.preamp_adj.set_value(self.pregain)
        self.preamp_lbl.set_text(f"{self.pregain:+.1f} dB")
        self.graph.set_data(self.bands, self.pregain)
        self.channels_grid.update_all(self.bands)
        self.show_toast("Hardware profile loaded.")

    def _on_graph_band_changed(self, band_idx, band_data, commit=False):
        self.bands[band_idx] = dict(band_data)
        self.channels_grid.update_band(band_idx, band_data)
        self._schedule_hardware_sync()

    def _on_channel_band_changed(self, band_idx, band_data):
        self.bands[band_idx] = dict(band_data)
        self.graph.set_data(self.bands, self.pregain)
        self._schedule_hardware_sync()

    def _on_preamp_changed(self, widget):
        val = round(self.preamp_adj.get_value() * 2) / 2.0
        self.pregain = val
        self.preamp_lbl.set_text(f"{val:+.1f} dB")
        self.graph.set_data(self.bands, self.pregain)
        self._schedule_hardware_sync()

    def _on_mic_changed(self, widget):
        val = int(round(self.mic_adj.get_value()))
        self.mic_lbl.set_text(f"{val}%")
        try:
            tanchjim_bunny.set_mic_gain(val / 100.0)
        except Exception:
            pass

    def _schedule_hardware_sync(self):
        # Debounce hardware writes by 120ms so dragging sliders is 60 FPS fluid
        if self.debounce_timer_id is not None:
            GLib.source_remove(self.debounce_timer_id)

        def sync_worker():
            profile = {
                "pregain": self.pregain,
                "bands": [dict(b) for b in self.bands]
            }
            try:
                with self.hardware_lock:
                    with tanchjim_bunny.BunnyDSP() as dsp:
                        # Write SRAM registers live without flash burn
                        dsp.enable_eq(0x03)
                        for i, b in enumerate(profile["bands"]):
                            dsp.write_gain_freq(i, b["freq"], b["gain"])
                            dsp.write_q_type(i, b["q"], b["type"])
                        dsp.write_pregain(profile["pregain"])
            except Exception:
                pass
            return False

        def on_timeout():
            self.debounce_timer_id = None
            t = threading.Thread(target=sync_worker, daemon=True)
            t.start()
            return False

        self.debounce_timer_id = GLib.timeout_add(120, on_timeout)

    def _on_save_hardware(self, btn):
        self.save_btn.set_sensitive(False)
        self.show_toast("Burning active curve to KT0210 EEPROM...")

        def worker():
            err = None
            try:
                with self.hardware_lock:
                    with tanchjim_bunny.BunnyDSP() as dsp:
                        dsp.commit()
            except Exception as e:
                err = str(e)

            def done():
                self.save_btn.set_sensitive(True)
                if err:
                    self.show_toast(f"Save failed: {err}")
                else:
                    self.show_toast("Profile burned to KT0210 EEPROM.")
            GLib.idle_add(done)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _on_apply_preset(self, item):
        name = item.get("name", "Preset")
        self.pregain = item.get("pregain", 0.0)
        self.preamp_adj.set_value(self.pregain)
        self.preamp_lbl.set_text(f"{self.pregain:+.1f} dB")
        raw_bands = item.get("bands", [])
        for i in range(min(8, len(raw_bands))):
            self.bands[i] = dict(raw_bands[i])
        self.graph.set_data(self.bands, self.pregain)
        self.channels_grid.update_all(self.bands)
        self._schedule_hardware_sync()
        self.show_toast(f"Applied '{name}' to Equalizer.")

    def _on_flash_preset(self, item):
        name = item.get("name", "Preset")
        self._on_apply_preset(item)
        self.save_btn.set_sensitive(False)
        self.show_toast(f"Burning '{name}' to KT0210 EEPROM...")

        def worker():
            err = None
            try:
                with self.hardware_lock:
                    with tanchjim_bunny.BunnyDSP() as dsp:
                        dsp.enable_eq(0x03)
                        for i, b in enumerate(self.bands):
                            dsp.write_gain_freq(i, b["freq"], b["gain"])
                            dsp.write_q_type(i, b["q"], b["type"])
                        dsp.write_pregain(self.pregain)
                        dsp.commit()
            except Exception as e:
                err = str(e)

            def done():
                self.save_btn.set_sensitive(True)
                if err:
                    self.show_toast(f"Burn failed: {err}")
                else:
                    self.show_toast(f"Burned '{name}' to KT0210 EEPROM flash.")
            GLib.idle_add(done)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def do_supported_devices(self):
        dlg = SupportedDevicesDialog(self)
        dlg.present()

    def do_hardware_dump(self):
        self.show_toast("Reading 256 chip registers...")

        def worker():
            raw = None
            meta = None
            err = None
            try:
                with self.hardware_lock:
                    with tanchjim_bunny.BunnyDSP() as dsp:
                        raw, meta = dsp.dump_all_registers()
            except Exception as e:
                err = str(e)

            def done():
                if err or not raw:
                    self.show_toast(f"Dump failed: {err}")
                else:
                    dlg = HardwareDumpDialog(self, raw, meta)
                    dlg.present()
            GLib.idle_add(done)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def do_noise_test(self):
        self.show_toast("Playing 5s white noise test signal (-22 dBFS)...")

        def worker():
            try:
                tanchjim_bunny.play_test_noise(duration=5.0, level_db=-22.0)
            except Exception as e:
                GLib.idle_add(lambda: self.show_toast(f"Audio test error: {e}"))
            else:
                GLib.idle_add(lambda: self.show_toast("White noise test complete."))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def do_reset_flat(self):
        self.pregain = 0.0
        self.preamp_adj.set_value(0.0)
        self.preamp_lbl.set_text("0.0 dB")
        for b in self.bands:
            b["gain"] = 0.0
        self.graph.set_data(self.bands, self.pregain)
        self.channels_grid.update_all(self.bands)
        self._schedule_hardware_sync()
        self.show_toast("Reset all 8 bands to 0 dB flat.")
