import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib


FILTER_TYPES = ["PK", "LSQ", "HSQ"]
FILTER_NAMES = ["Peak", "Low Shelf", "High Shelf"]


class ChannelStripCard(Gtk.Box):
    def __init__(self, band_idx, on_changed_callback):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.band_idx = band_idx
        self.on_changed = on_changed_callback
        self.suppress_signals = False

        self.add_css_class("card")
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(4)
        self.set_margin_end(4)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(8)
        inner.set_margin_bottom(8)
        inner.set_margin_start(10)
        inner.set_margin_end(10)
        self.append(inner)

        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        inner.append(top_box)

        lbl = Gtk.Label(label=f"<b>Band {band_idx + 1}</b>", use_markup=True)
        lbl.set_xalign(0.0)
        top_box.append(lbl)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_box.append(spacer)

        type_list = Gtk.StringList.new(FILTER_NAMES)
        self.type_dropdown = Gtk.DropDown.new(type_list, None)
        self.type_dropdown.connect("notify::selected", self._on_type_changed)
        top_box.append(self.type_dropdown)

        zero_btn = Gtk.Button(label="0dB")
        zero_btn.set_tooltip_text("Reset gain to 0 dB")
        zero_btn.add_css_class("flat")
        zero_btn.connect("clicked", self._on_zero_clicked)
        top_box.append(zero_btn)

        freq_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        inner.append(freq_box)

        freq_lbl = Gtk.Label(label="Freq", xalign=0.0)
        freq_lbl.set_size_request(32, -1)
        freq_lbl.add_css_class("dim-label")
        freq_box.append(freq_lbl)

        self.freq_adj = Gtk.Adjustment(value=1000, lower=20, upper=20000, step_increment=10, page_increment=100)
        self.freq_spin = Gtk.SpinButton(adjustment=self.freq_adj, climb_rate=1.0, digits=0)
        self.freq_spin.set_hexpand(True)
        self.freq_spin.connect("value-changed", self._on_param_changed)
        freq_box.append(self.freq_spin)

        gain_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        inner.append(gain_box)

        gain_lbl = Gtk.Label(label="Gain", xalign=0.0)
        gain_lbl.set_size_request(32, -1)
        gain_lbl.add_css_class("dim-label")
        gain_box.append(gain_lbl)

        self.gain_adj = Gtk.Adjustment(value=0.0, lower=-12.0, upper=12.0, step_increment=0.5, page_increment=1.0)
        self.gain_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.gain_adj)
        self.gain_scale.set_hexpand(True)
        self.gain_scale.set_draw_value(False)
        self.gain_scale.connect("value-changed", self._on_param_changed)
        gain_box.append(self.gain_scale)

        self.gain_val_lbl = Gtk.Label(label="0.0dB")
        self.gain_val_lbl.set_size_request(46, -1)
        self.gain_val_lbl.set_xalign(1.0)
        gain_box.append(self.gain_val_lbl)

        q_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        inner.append(q_box)

        q_lbl = Gtk.Label(label="Q", xalign=0.0)
        q_lbl.set_size_request(32, -1)
        q_lbl.add_css_class("dim-label")
        q_box.append(q_lbl)

        self.q_adj = Gtk.Adjustment(value=1.0, lower=0.1, upper=10.0, step_increment=0.1, page_increment=0.5)
        self.q_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.q_adj)
        self.q_scale.set_hexpand(True)
        self.q_scale.set_draw_value(False)
        self.q_scale.connect("value-changed", self._on_param_changed)
        q_box.append(self.q_scale)

        self.q_val_lbl = Gtk.Label(label="1.00")
        self.q_val_lbl.set_size_request(46, -1)
        self.q_val_lbl.set_xalign(1.0)
        q_box.append(self.q_val_lbl)

    def set_values(self, data):
        self.suppress_signals = True
        try:
            freq = float(data.get("freq", 1000))
            gain = float(data.get("gain", 0.0))
            q = float(data.get("q", 1.0))
            ftype = str(data.get("type", "PK")).upper()

            self.freq_adj.set_value(freq)
            self.gain_adj.set_value(gain)
            self.gain_val_lbl.set_text(f"{gain:+.1f}dB")
            self.q_adj.set_value(q)
            self.q_val_lbl.set_text(f"{q:.2f}")

            if ftype in ("LSQ", "LOW_SHELF"):
                self.type_dropdown.set_selected(1)
            elif ftype in ("HSQ", "HIGH_SHELF"):
                self.type_dropdown.set_selected(2)
            else:
                self.type_dropdown.set_selected(0)
        finally:
            self.suppress_signals = False

    def get_values(self):
        sel = self.type_dropdown.get_selected()
        ftype = FILTER_TYPES[sel] if 0 <= sel < len(FILTER_TYPES) else "PK"
        return {
            "band": self.band_idx,
            "freq": round(self.freq_adj.get_value()),
            "gain": round(self.gain_adj.get_value() * 2) / 2.0,
            "q": round(self.q_adj.get_value() * 100) / 100.0,
            "type": ftype,
        }

    def _on_param_changed(self, widget):
        if self.suppress_signals:
            return
        g = self.gain_adj.get_value()
        self.gain_val_lbl.set_text(f"{g:+.1f}dB")
        q = self.q_adj.get_value()
        self.q_val_lbl.set_text(f"{q:.2f}")
        if self.on_changed:
            self.on_changed(self.band_idx, self.get_values())

    def _on_type_changed(self, dropdown, param):
        if self.suppress_signals:
            return
        if self.on_changed:
            self.on_changed(self.band_idx, self.get_values())

    def _on_zero_clicked(self, btn):
        self.gain_adj.set_value(0.0)


class ChannelsGrid(Gtk.Box):
    def __init__(self, on_band_changed_callback):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.on_band_changed = on_band_changed_callback
        self.cards = []

        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(8)
        grid.set_hexpand(True)
        self.append(grid)

        # 4 columns x 2 rows
        for i in range(8):
            card = ChannelStripCard(i, self._on_card_changed)
            col = i % 4
            row = i // 4
            grid.attach(card, col, row, 1, 1)
            self.cards.append(card)

    def _on_card_changed(self, band_idx, values):
        if self.on_band_changed:
            self.on_band_changed(band_idx, values)

    def update_band(self, band_idx, values):
        if 0 <= band_idx < len(self.cards):
            self.cards[band_idx].set_values(values)

    def update_all(self, bands):
        for idx, b in enumerate(bands):
            if idx < len(self.cards):
                self.cards[idx].set_values(b)
