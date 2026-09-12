import math
import cairo
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib


def calc_biquad_coeffs(filter_type, f0, gain_db, q, fs=48000):
    f0 = max(20.0, min(20000.0, float(f0)))
    q = max(0.1, min(10.0, float(q)))
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * f0 / fs
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    ftype = str(filter_type).strip().upper()

    if ftype in ("LSQ", "LOW_SHELF", "LOWSHELF"):
        two_sqrt_A_alpha = 2.0 * math.sqrt(max(0.0001, A)) * alpha
        b0 = A * ((A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_A_alpha)
        b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w0)
        b2 = A * ((A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_A_alpha)
        a0 = (A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_A_alpha
        a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cos_w0)
        a2 = (A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_A_alpha
    elif ftype in ("HSQ", "HIGH_SHELF", "HIGHSHELF"):
        two_sqrt_A_alpha = 2.0 * math.sqrt(max(0.0001, A)) * alpha
        b0 = A * ((A + 1.0) + (A - 1.0) * cos_w0 + two_sqrt_A_alpha)
        b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cos_w0)
        b2 = A * ((A + 1.0) + (A - 1.0) * cos_w0 - two_sqrt_A_alpha)
        a0 = (A + 1.0) - (A - 1.0) * cos_w0 + two_sqrt_A_alpha
        a1 = 2.0 * ((A - 1.0) - (A + 1.0) * cos_w0)
        a2 = (A + 1.0) - (A - 1.0) * cos_w0 - two_sqrt_A_alpha
    else:  # PK / PEAK
        b0 = 1.0 + alpha * A
        b1 = -2.0 * cos_w0
        b2 = 1.0 - alpha * A
        a0 = 1.0 + alpha / A
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha / A

    if abs(a0) < 1e-12:
        return [1.0, 0.0, 0.0], [1.0, 0.0, 0.0]

    return [b0 / a0, b1 / a0, b2 / a0], [1.0, a1 / a0, a2 / a0]


def biquad_mag_db(b, a, f, fs=48000):
    w = 2.0 * math.pi * f / fs
    cos_w = math.cos(w)
    sin_w = math.sin(w)
    cos_2w = math.cos(2.0 * w)
    sin_2w = math.sin(2.0 * w)

    num_re = b[0] + b[1] * cos_w + b[2] * cos_2w
    num_im = -b[1] * sin_w - b[2] * sin_2w
    den_re = a[0] + a[1] * cos_w + a[2] * cos_2w
    den_im = -a[1] * sin_w - a[2] * sin_2w

    num_mag2 = num_re * num_re + num_im * num_im
    den_mag2 = den_re * den_re + den_im * den_im
    if den_mag2 < 1e-18:
        return 0.0
    val = num_mag2 / den_mag2
    if val <= 1e-18:
        return -60.0
    return 10.0 * math.log10(val)


class FrequencyResponseGraph(Gtk.DrawingArea):
    def __init__(self, on_band_changed=None):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_content_height(240)
        self.set_content_width(600)

        self.on_band_changed = on_band_changed

        self.bands = [
            {"freq": 80.0, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 150.0, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 500.0, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 1000.0, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 2800.0, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 4500.0, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 5500.0, "gain": 0.0, "q": 1.0, "type": "PK"},
            {"freq": 12000.0, "gain": 0.0, "q": 1.0, "type": "HSQ"},
        ]
        self.pregain = 0.0

        self.hovered_band = None
        self.active_band = None
        self.drag_start_x = 0
        self.drag_start_y = 0

        self.pad_left = 42
        self.pad_right = 16
        self.pad_top = 16
        self.pad_bottom = 26

        self.set_draw_func(self._on_draw)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click_pressed)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def set_data(self, bands, pregain=0.0):
        self.bands = [dict(b) for b in bands]
        self.pregain = float(pregain)
        self.queue_draw()

    def _f_to_x(self, f, width):
        plot_w = width - self.pad_left - self.pad_right
        min_log = math.log10(20.0)
        max_log = math.log10(20000.0)
        cur_log = math.log10(max(20.0, min(20000.0, float(f))))
        return self.pad_left + plot_w * (cur_log - min_log) / (max_log - min_log)

    def _g_to_y(self, g, height):
        plot_h = height - self.pad_top - self.pad_bottom
        clamped_g = max(-12.0, min(12.0, float(g)))
        return self.pad_top + plot_h * (12.0 - clamped_g) / 24.0

    def _x_to_f(self, x, width):
        plot_w = width - self.pad_left - self.pad_right
        norm = max(0.0, min(1.0, (x - self.pad_left) / max(1.0, plot_w)))
        min_log = math.log10(20.0)
        max_log = math.log10(20000.0)
        val = 10.0 ** (min_log + norm * (max_log - min_log))
        return round(val)

    def _y_to_g(self, y, height):
        plot_h = height - self.pad_top - self.pad_bottom
        norm = max(0.0, min(1.0, (y - self.pad_top) / max(1.0, plot_h)))
        val = 12.0 - norm * 24.0
        return round(val * 2.0) / 2.0  # snap to 0.5 dB

    def _find_node_at(self, x, y, width, height):
        for idx, b in enumerate(self.bands):
            nx = self._f_to_x(b["freq"], width)
            ny = self._g_to_y(b["gain"], height)
            dist = math.hypot(x - nx, y - ny)
            if dist <= 14:
                return idx
        return None

    def _on_motion(self, controller, x, y):
        w = self.get_width()
        h = self.get_height()
        node = self._find_node_at(x, y, w, h)
        if node != self.hovered_band:
            self.hovered_band = node
            self.queue_draw()

    def _on_leave(self, controller):
        if self.hovered_band is not None:
            self.hovered_band = None
            self.queue_draw()

    def _on_drag_begin(self, gesture, start_x, start_y):
        w = self.get_width()
        h = self.get_height()
        node = self._find_node_at(start_x, start_y, w, h)
        if node is not None:
            self.active_band = node
            self.drag_start_x = start_x
            self.drag_start_y = start_y
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self.active_band is None:
            return
        w = self.get_width()
        h = self.get_height()
        cur_x = self.drag_start_x + offset_x
        cur_y = self.drag_start_y + offset_y

        f = self._x_to_f(cur_x, w)
        g = self._y_to_g(cur_y, h)

        self.bands[self.active_band]["freq"] = f
        self.bands[self.active_band]["gain"] = g
        self.queue_draw()

        if self.on_band_changed:
            self.on_band_changed(self.active_band, self.bands[self.active_band], commit=False)

    def _on_drag_end(self, gesture, offset_x, offset_y):
        if self.active_band is not None:
            if self.on_band_changed:
                self.on_band_changed(self.active_band, self.bands[self.active_band], commit=True)
            self.active_band = None
            self.queue_draw()

    def _on_click_pressed(self, gesture, n_press, x, y):
        if n_press == 2:
            w = self.get_width()
            h = self.get_height()
            node = self._find_node_at(x, y, w, h)
            if node is not None:
                self.bands[node]["gain"] = 0.0
                self.queue_draw()
                if self.on_band_changed:
                    self.on_band_changed(node, self.bands[node], commit=True)

    def _on_scroll(self, controller, dx, dy):
        if self.hovered_band is None:
            return False
        idx = self.hovered_band
        delta = -0.1 if dy > 0 else 0.1
        cur_q = self.bands[idx].get("q", 1.0)
        new_q = max(0.1, min(10.0, round((cur_q + delta) * 10) / 10.0))
        self.bands[idx]["q"] = new_q
        self.queue_draw()
        if self.on_band_changed:
            self.on_band_changed(idx, self.bands[idx], commit=True)
        return True

    def _on_draw(self, area, cr, width, height):
        cr.set_source_rgb(0.08, 0.08, 0.09)
        cr.paint()

        plot_w = width - self.pad_left - self.pad_right
        plot_h = height - self.pad_top - self.pad_bottom

        # Grid styling
        cr.set_line_width(1.0)
        cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(9.0)

        # Horizontal dB grid lines
        for db in [-12, -6, 0, 6, 12]:
            y = self._g_to_y(db, height)
            if db == 0:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.25)
                cr.set_line_width(1.2)
            else:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.07)
                cr.set_line_width(0.8)

            cr.move_to(self.pad_left, y)
            cr.line_to(width - self.pad_right, y)
            cr.stroke()

            # dB labels on the left
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            label = f"{db:+d}dB" if db != 0 else " 0dB"
            cr.move_to(6, y + 3)
            cr.show_text(label)

        # Vertical Frequency grid lines
        freq_grid = [
            (20, "20"), (50, "50"), (100, "100"), (200, "200"),
            (500, "500"), (1000, "1k"), (2000, "2k"), (5000, "5k"),
            (10000, "10k"), (20000, "20k")
        ]
        for f, label in freq_grid:
            x = self._f_to_x(f, width)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.07)
            cr.set_line_width(0.8)
            cr.move_to(x, self.pad_top)
            cr.line_to(x, height - self.pad_bottom)
            cr.stroke()

            cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            extents = cr.text_extents(label)
            cr.move_to(x - extents.width / 2.0, height - 8)
            cr.show_text(label)

        # Compute combined frequency response curve
        biquads = []
        for b in self.bands:
            bcoeffs, acoeffs = calc_biquad_coeffs(
                b.get("type", "PK"), b.get("freq", 1000), b.get("gain", 0.0), b.get("q", 1.0)
            )
            biquads.append((bcoeffs, acoeffs))

        points = []
        n_steps = max(100, int(plot_w))
        min_log = math.log10(20.0)
        max_log = math.log10(20000.0)

        for i in range(n_steps + 1):
            log_f = min_log + (i / n_steps) * (max_log - min_log)
            f = 10.0 ** log_f
            total_db = self.pregain
            for bc, ac in biquads:
                total_db += biquad_mag_db(bc, ac, f)
            px = self._f_to_x(f, width)
            py = self._g_to_y(total_db, height)
            points.append((px, py))

        # Fill curve area
        if points:
            y_zero = self._g_to_y(0.0, height)
            cr.move_to(points[0][0], y_zero)
            for px, py in points:
                cr.line_to(px, py)
            cr.line_to(points[-1][0], y_zero)
            cr.close_path()

            # Cyan translucent fill
            cr.set_source_rgba(0.22, 0.74, 0.97, 0.12)
            cr.fill()

        # Stroke cyan curve
        if points:
            cr.set_source_rgba(0.22, 0.74, 0.97, 0.95)
            cr.set_line_width(2.0)
            cr.move_to(points[0][0], points[0][1])
            for px, py in points[1:]:
                cr.line_to(px, py)
            cr.stroke()

        # Draw individual band interactive nodes
        for idx, b in enumerate(self.bands):
            nx = self._f_to_x(b["freq"], width)
            ny = self._g_to_y(b["gain"], height)

            is_hovered = (idx == self.hovered_band or idx == self.active_band)
            r = 7.0 if is_hovered else 5.0

            # Outer ring if hovered
            if is_hovered:
                cr.set_source_rgba(0.22, 0.74, 0.97, 0.3)
                cr.arc(nx, ny, r + 4.0, 0, 2.0 * math.pi)
                cr.fill()

            # Node circle
            cr.set_source_rgb(0.05, 0.05, 0.06)
            cr.arc(nx, ny, r, 0, 2.0 * math.pi)
            cr.fill()

            cr.set_source_rgb(0.22, 0.74, 0.97)
            cr.set_line_width(2.0)
            cr.arc(nx, ny, r, 0, 2.0 * math.pi)
            cr.stroke()

            # Label on top of node
            label = str(idx + 1)
            cr.set_font_size(8.0)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
            extents = cr.text_extents(label)
            cr.move_to(nx - extents.width / 2.0, ny - r - 4)
            cr.show_text(label)

        # Tooltip bubble if hovering/dragging
        active_idx = self.active_band if self.active_band is not None else self.hovered_band
        if active_idx is not None and 0 <= active_idx < len(self.bands):
            b = self.bands[active_idx]
            info = f"B{active_idx+1}: {b['freq']}Hz | {b['gain']:+.1f}dB | Q:{b['q']:.2f} ({b['type']})"
            cr.set_font_size(10.0)
            extents = cr.text_extents(info)
            box_w = extents.width + 16
            box_h = 22
            bx = width - self.pad_right - box_w
            by = self.pad_top + 4

            cr.set_source_rgba(0.0, 0.0, 0.0, 0.75)
            cr.rectangle(bx, by, box_w, box_h)
            cr.fill()

            cr.set_source_rgba(0.22, 0.74, 0.97, 0.8)
            cr.set_line_width(1.0)
            cr.rectangle(bx, by, box_w, box_h)
            cr.stroke()

            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.move_to(bx + 8, by + 15)
            cr.show_text(info)
