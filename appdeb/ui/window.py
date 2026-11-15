"""Main application window: sidebar sections and navigation stack."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import apps  # noqa: E402
from .details import DetailsPage  # noqa: E402
from .install import InstallPage  # noqa: E402
from .installed import InstalledPage  # noqa: E402
from .settings import SettingsPage  # noqa: E402
from .system import SystemPage  # noqa: E402
from .tools import ToolsPage  # noqa: E402

SECTIONS = [
    ("installed", "Installed", "view-grid-symbolic"),
    ("install", "Install", "folder-download-symbolic"),
    ("system", "System", "utilities-system-monitor-symbolic"),
    ("tools", "Tools", "emblem-system-symbolic"),
    ("settings", "Settings", "preferences-system-symbolic"),
]


class AppDebWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application | None = None) -> None:
        super().__init__(application=application, title="AppDeb")
        self.set_default_size(1100, 760)
        self.set_size_request(800, 560)
        self._pages: dict[str, Adw.NavigationPage] = {}
        self._current = "installed"
        self._sidebar_rows: dict[str, Gtk.ListBoxRow] = {}

        self._nav = Adw.NavigationView()
        self._nav.set_pop_on_escape(True)

        content_page = Adw.NavigationPage()
        content_page.set_title("AppDeb")
        content_page.set_child(self._nav)

        split = Adw.NavigationSplitView()
        split.set_sidebar(self._build_sidebar())
        split.set_content(content_page)
        if hasattr(split, "set_min_sidebar_width"):
            split.set_min_sidebar_width(240)
        if hasattr(split, "set_max_sidebar_width"):
            split.set_max_sidebar_width(320)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(split)
        self.set_content(self.toast_overlay)

        installed = self._page_for("installed")
        self._nav.push(installed)
        self._select_row("installed")

    # --------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="AppDeb", subtitle="Application manager"))

        menu = Gio.Menu()
        menu.append("About AppDeb", "app.about")
        menu.append("Quit", "app.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(menu)
        menu_button.set_tooltip_text("Main menu")
        header.pack_end(menu_button)

        self._sidebar_list = Gtk.ListBox()
        self._sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_list.add_css_class("navigation-sidebar")
        self._sidebar_list.set_valign(Gtk.Align.START)
        self._sidebar_list.connect("row-selected", self._on_row_selected)

        for name, title, icon_name in SECTIONS:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_margin_top(10)
            box.set_margin_bottom(10)
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.append(Gtk.Image.new_from_icon_name(icon_name))
            label = Gtk.Label(label=title, xalign=0.0)
            label.set_hexpand(True)
            box.append(label)
            row.set_child(box)
            row._section = name  # type: ignore[attr-defined]
            self._sidebar_list.append(row)
            self._sidebar_rows[name] = row

        sidebar_scroller = Gtk.ScrolledWindow()
        sidebar_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroller.set_vexpand(True)
        sidebar_scroller.set_child(self._sidebar_list)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(sidebar_scroller)

        page = Adw.NavigationPage()
        page.set_title("AppDeb")
        page.set_child(toolbar)
        return page

    def _select_row(self, name: str) -> None:
        row = self._sidebar_rows.get(name)
        if row is not None:
            self._sidebar_list.select_row(row)

    def _on_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        name = getattr(row, "_section", None)
        if name:
            self._show_section(name)

    def _show_section(self, name: str) -> None:
        page = self._page_for(name)
        if self._nav.get_visible_page() is not page:
            self._nav.replace([page])
        self._current = name
        refresh = getattr(page, "refresh", None)
        if callable(refresh) and name == "installed":
            refresh()

    # ----------------------------------------------------------------- pages
    def _page_for(self, name: str) -> Adw.NavigationPage:
        page = self._pages.get(name)
        if page is not None:
            return page
        if name == "installed":
            page = InstalledPage(self.open_details)
        elif name == "install":
            page = InstallPage(self.open_details, on_changed=self._reload_installed)
        elif name == "system":
            page = SystemPage()
        elif name == "tools":
            page = ToolsPage()
        elif name == "settings":
            page = SettingsPage(on_changed=self._reload_installed)
        else:
            raise ValueError(f"unknown section: {name}")
        self._pages[name] = page
        return page

    def _reload_installed(self) -> None:
        installed = self._pages.get("installed")
        if installed is not None:
            installed.refresh()

    def open_details(self, app: apps.InstalledApp) -> None:
        page = DetailsPage(app, on_changed=self._reload_installed)
        self._nav.push(page)

    def open_install_files(self, files) -> None:
        paths: list[Path] = []
        for item in files:
            if isinstance(item, Path):
                paths.append(item)
                continue
            path = None
            get_path = getattr(item, "get_path", None)
            if callable(get_path):
                path = get_path()
            if path:
                paths.append(Path(path))
        if not paths:
            return
        self._select_row("install")
        install_page = self._page_for("install")
        for path in paths:
            install_page.open_file(path)

    def refresh(self) -> None:
        page = self._nav.get_visible_page()
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
        self._reload_installed()
