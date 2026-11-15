"""GTK application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, APP_NAME, REPOSITORY, __version__, config, db  # noqa: E402
from ..i18n import setup as setup_i18n  # noqa: E402
from . import onboarding  # noqa: E402
from .window import AppDebWindow  # noqa: E402


def _stylesheet_candidates() -> list[Path]:
    package = Path(__file__).resolve().parent.parent / "style.css"
    return [package, Path("/usr/share/appdeb/style.css")]


class AppDebApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        config.ensure_dirs()
        db.init()
        self._load_stylesheet()
        self._install_actions()

    def _load_stylesheet(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        for path in _stylesheet_candidates():
            if not path.is_file():
                continue
            provider = Gtk.CssProvider()
            try:
                provider.load_from_path(str(path))
            except Exception:  # noqa: BLE001 - styling is optional
                continue
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            return

    def _install_actions(self) -> None:
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about)
        self.add_action(about)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_a: self.quit())
        self.add_action(quit_action)
        refresh = Gio.SimpleAction.new("refresh", None)
        refresh.connect("activate", self._on_refresh)
        self.add_action(refresh)
        self.set_accels_for_action("app.quit", ["<primary>q"])
        self.set_accels_for_action("app.refresh", ["F5"])

    def _on_about(self, *_args) -> None:
        dialog = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            developers=["Kaan Çakmak"],
            license_type=Gtk.License.GPL_3_0,
            website=REPOSITORY,
            issue_url=f"{REPOSITORY}/issues",
        )
        dialog.present(self.get_active_window())

    def _on_refresh(self, *_args) -> None:
        window = self.get_active_window()
        if isinstance(window, AppDebWindow):
            window.refresh()

    def _ensure_window(self) -> AppDebWindow:
        window = self.get_active_window()
        if isinstance(window, AppDebWindow):
            return window
        return AppDebWindow(application=self)

    def do_activate(self) -> None:
        window = self._ensure_window()
        window.present()
        GLib.idle_add(self._show_welcome_once, window)

    def _show_welcome_once(self, window) -> bool:
        onboarding.show_welcome(window)
        return GLib.SOURCE_REMOVE

    def do_open(self, files, n_files, hint) -> None:
        window = self._ensure_window()
        window.present()
        window.open_install_files(list(files))


def main(files: list[str] | None = None) -> int:
    config.ensure_dirs()
    db.init()
    setup_i18n(db.setting("language"))
    app = AppDebApplication()
    if files:
        argv = [sys.argv[0], *[str(item) for item in files]]
    else:
        argv = sys.argv
    return app.run(argv)
