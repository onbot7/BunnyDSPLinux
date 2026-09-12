import json
import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

from gui.presets_data import PRESETS_CATALOG
from gui.dialogs import AutoEqImportDialog
import tanchjim_bunny


class PresetsView(Gtk.Box):
    def __init__(self, main_window, on_apply_callback, on_flash_callback, scrolled=True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.main_window = main_window
        self.on_apply = on_apply_callback
        self.on_flash = on_flash_callback
        self.rows_map = []  # (row_widget, group_widget, search_text)

        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_bar.set_margin_top(8)
        top_bar.set_margin_start(4)
        top_bar.set_margin_end(4)
        self.append(top_bar)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search presets (Harman, Bass, Vocal, Gaming, Orpheus...)...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        top_bar.append(self.search_entry)

        import_btn = Gtk.Button(label="Import AutoEQ...")
        import_btn.set_tooltip_text("Import AutoEq, Squiglink, or Peace parametric text")
        import_btn.connect("clicked", self._on_import_autoeq)
        top_bar.append(import_btn)

        load_file_btn = Gtk.Button(label="Load File...")
        load_file_btn.set_tooltip_text("Open .json or .txt preset file")
        load_file_btn.connect("clicked", self._on_load_file)
        top_bar.append(load_file_btn)

        save_cur_btn = Gtk.Button(label="Save Current...")
        save_cur_btn.set_tooltip_text("Export active tuning curve to a .json preset file")
        save_cur_btn.connect("clicked", self._on_save_file)
        top_bar.append(save_cur_btn)

        catalog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        catalog_box.set_margin_top(8)
        catalog_box.set_margin_bottom(24)
        catalog_box.set_margin_start(4)
        catalog_box.set_margin_end(4)

        if scrolled:
            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_vexpand(True)
            scrolled_window.set_hexpand(True)
            scrolled_window.set_child(catalog_box)
            self.append(scrolled_window)
        else:
            self.append(catalog_box)

        for cat in PRESETS_CATALOG:
            group = Adw.PreferencesGroup()
            group.set_title(GLib.markup_escape_text(cat["category"]))
            catalog_box.append(group)

            for item in cat["items"]:
                row = Adw.ActionRow()
                row.set_title(GLib.markup_escape_text(item["name"]))
                row.set_subtitle(GLib.markup_escape_text(item["desc"]))
                row.set_activatable(False)
                group.add(row)

                search_text = f"{item['name']} {item['desc']} {cat['category']}".lower()
                self.rows_map.append((row, group, search_text))

                btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                btn_box.set_valign(Gtk.Align.CENTER)
                row.add_suffix(btn_box)

                apply_btn = Gtk.Button(label="Apply")
                apply_btn.set_tooltip_text("Load into equalizer and preview immediately")
                apply_btn.connect("clicked", self._on_apply_clicked, item)
                btn_box.append(apply_btn)

                flash_btn = Gtk.Button(label="Flash")
                flash_btn.set_tooltip_text("Apply and permanently burn into chip EEPROM")
                flash_btn.add_css_class("suggested-action")
                flash_btn.connect("clicked", self._on_flash_clicked, item)
                btn_box.append(flash_btn)

    def _on_search_changed(self, entry):
        query = entry.get_text().strip().lower()
        group_vis = {}
        for row, group, text in self.rows_map:
            matches = (query in text) if query else True
            row.set_visible(matches)
            if matches:
                group_vis[group] = True

        for row, group, text in self.rows_map:
            group.set_visible(group_vis.get(group, False) if query else True)

    def _on_apply_clicked(self, btn, item):
        if self.on_apply:
            self.on_apply(item)

    def _on_flash_clicked(self, btn, item):
        if self.on_flash:
            self.on_flash(item)

    def _on_import_autoeq(self, btn):
        def on_applied(text):
            try:
                pj = json.loads(text)
            except Exception:
                pj = tanchjim_bunny.parse_parametric_eq(text)
            item = {
                "name": pj.get("name", "Imported Curve"),
                "pregain": pj.get("pregain", 0.0),
                "bands": pj.get("bands", [])
            }
            if self.on_apply:
                self.on_apply(item)

        dlg = AutoEqImportDialog(self.main_window, on_applied)
        dlg.present()

    def _on_load_file(self, btn):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Open Preset File")

        def on_open_finish(source, res):
            try:
                gfile = source.open_finish(res)
                if gfile:
                    with open(gfile.get_path(), "r") as f:
                        text = f.read()
                    try:
                        pj = json.loads(text)
                    except Exception:
                        pj = tanchjim_bunny.parse_parametric_eq(text)
                    item = {
                        "name": pj.get("name", os.path.basename(gfile.get_path())),
                        "pregain": pj.get("pregain", 0.0),
                        "bands": pj.get("bands", [])
                    }
                    if self.on_apply:
                        self.on_apply(item)
            except Exception as e:
                self.main_window.show_toast(f"Failed to open file: {e}")

        dialog.open(self.main_window, None, on_open_finish)

    def _on_save_file(self, btn):
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Save Current Tuning (.json)")
        dialog.set_initial_name("my_custom_tuning.json")

        def on_save_finish(source, res):
            try:
                gfile = source.save_finish(res)
                if gfile:
                    profile = {
                        "name": "Custom Profile",
                        "pregain": self.main_window.pregain,
                        "bands": [dict(b) for b in self.main_window.bands]
                    }
                    with open(gfile.get_path(), "w") as f:
                        json.dump(profile, f, indent=2)
                    self.main_window.show_toast(f"Saved to {os.path.basename(gfile.get_path())}")
            except Exception as e:
                self.main_window.show_toast(f"Error saving file: {e}")

        dialog.save(self.main_window, None, on_save_finish)
