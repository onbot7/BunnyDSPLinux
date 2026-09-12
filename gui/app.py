import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio

from gui.window import MainWindow
from gui.dialogs import show_about_dialog


class KT0210Application(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.tanchjim_bunny_dsp",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self.window = None

    def do_activate(self):
        if not self.window:
            self._setup_actions()
            self.window = MainWindow(self)
        self.window.present()

    def _setup_actions(self):
        action_dump = Gio.SimpleAction.new("dump", None)
        action_dump.connect("activate", lambda a, p: self.window.do_hardware_dump())
        self.add_action(action_dump)

        action_noise = Gio.SimpleAction.new("noise", None)
        action_noise.connect("activate", lambda a, p: self.window.do_noise_test())
        self.add_action(action_noise)

        action_reset = Gio.SimpleAction.new("reset", None)
        action_reset.connect("activate", lambda a, p: self.window.do_reset_flat())
        self.add_action(action_reset)

        action_supported = Gio.SimpleAction.new("supported", None)
        action_supported.connect("activate", lambda a, p: self.window.do_supported_devices())
        self.add_action(action_supported)

        action_issue = Gio.SimpleAction.new("issue", None)
        action_issue.connect("activate", lambda a, p: Gio.AppInfo.launch_default_for_uri("https://github.com/onbot7/BunnyDSPLinux/issues", None))
        self.add_action(action_issue)

        action_about = Gio.SimpleAction.new("about", None)
        action_about.connect("activate", lambda a, p: show_about_dialog(self.window))
        self.add_action(action_about)


def run_gui():
    app = KT0210Application()
    return app.run(sys.argv[:1])
