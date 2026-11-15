"""Application settings backed by the AppDeb database."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import APP_NAME, REPOSITORY, db  # noqa: E402

SCOPE_KEY = "flatpak_scope"
HIDDEN_KEY = "show_hidden"
INTERVAL_KEY = "refresh_interval"
DEFAULT_INTERVAL = 2


def flatpak_scope() -> str:
    value = db.setting(SCOPE_KEY, "user") or "user"
    return value if value in ("user", "system") else "user"


def show_hidden() -> bool:
    return (db.setting(HIDDEN_KEY, "0") or "0") == "1"


def refresh_interval() -> int:
    try:
        value = int(db.setting(INTERVAL_KEY, str(DEFAULT_INTERVAL)) or DEFAULT_INTERVAL)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return value if value in (2, 5, 10) else DEFAULT_INTERVAL


class SettingsPage(Adw.NavigationPage):
    def __init__(self, on_changed=None) -> None:
        super().__init__()
        self.set_title("Settings")
        self._on_changed = on_changed
        self._loading = True

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Settings"))

        page = Adw.PreferencesPage()

        flatpak_group = Adw.PreferencesGroup(
            title="Flatpak",
            description="Scope used for Flatpak installs and updates.",
        )
        self._scope_row = Adw.ComboRow(
            title="Installation scope",
            model=Gtk.StringList.new(["User", "System"]),
        )
        self._scope_row.set_selected(0 if flatpak_scope() == "user" else 1)
        self._scope_row.connect("notify::selected", self._on_scope_changed)
        flatpak_group.add(self._scope_row)
        page.add(flatpak_group)

        installed_group = Adw.PreferencesGroup(title="Installed applications")
        self._hidden_row = Adw.SwitchRow(
            title="Show hidden desktop entries",
            subtitle="Include entries marked NoDisplay or Hidden",
        )
        self._hidden_row.set_active(show_hidden())
        self._hidden_row.connect("notify::active", self._on_hidden_changed)
        installed_group.add(self._hidden_row)
        page.add(installed_group)

        monitoring_group = Adw.PreferencesGroup(title="Monitoring")
        interval = refresh_interval()
        choices = [2, 5, 10]
        self._interval_row = Adw.ComboRow(
            title="Refresh interval",
            subtitle="How often resource usage is sampled",
            model=Gtk.StringList.new(["2 seconds", "5 seconds", "10 seconds"]),
        )
        self._interval_row.set_selected(choices.index(interval))
        self._interval_row.connect("notify::selected", self._on_interval_changed)
        monitoring_group.add(self._interval_row)
        page.add(monitoring_group)

        about_group = Adw.PreferencesGroup(title="About")
        about_row = Adw.ActionRow(
            title=APP_NAME,
            subtitle=REPOSITORY,
            activatable=True,
        )
        about_row.set_icon_name("help-about-symbolic")
        about_row.connect("activated", self._on_about)
        about_group.add(about_row)
        page.add(about_group)

        self._loading = False

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)

    def _on_scope_changed(self, row: Adw.ComboRow, _param) -> None:
        if self._loading:
            return
        db.set_setting(SCOPE_KEY, "user" if row.get_selected() == 0 else "system")

    def _on_hidden_changed(self, row: Adw.SwitchRow, _param) -> None:
        if self._loading:
            return
        db.set_setting(HIDDEN_KEY, "1" if row.get_active() else "0")
        if self._on_changed is not None:
            self._on_changed()

    def _on_interval_changed(self, row: Adw.ComboRow, _param) -> None:
        if self._loading:
            return
        value = [2, 5, 10][max(0, min(row.get_selected(), 2))]
        db.set_setting(INTERVAL_KEY, str(value))

    def _on_about(self, _row: Adw.ActionRow) -> None:
        action = self.get_root().get_application().lookup_action("about")
        if action is not None:
            action.activate(None)
