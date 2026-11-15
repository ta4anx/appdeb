"""Installed applications list page."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import apps  # noqa: E402
from . import settings  # noqa: E402
from .dialogs import OperationDialog  # noqa: E402
from .widgets import chevron, escape_markup, icon_image, show_toast  # noqa: E402

FILTER_CHOICES = ["All", "Flatpak", "AppImage", "Tarball", "System"]
FILTER_KINDS = {"Flatpak": "flatpak", "AppImage": "appimage", "Tarball": "tarball"}


class InstalledPage(Adw.NavigationPage):
    def __init__(self, on_open) -> None:
        super().__init__()
        self.set_title("Installed")
        self._on_open = on_open
        self._apps: list[apps.InstalledApp] = []
        self._rows: list[Gtk.ListBoxRow] = []
        self._updates: list = []

        self._title = Adw.WindowTitle(title="Installed")
        header = Adw.HeaderBar()
        header.set_title_widget(self._title)

        self._updates_button = Gtk.Button(label="Check updates")
        self._updates_button.add_css_class("flat")
        self._updates_button.set_visible(False)
        self._updates_button.connect("clicked", self._on_updates_clicked)
        header.pack_start(self._updates_button)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh the application list")
        refresh.connect("clicked", lambda _b: self.refresh())
        header.pack_end(refresh)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search applications")
        self._search.set_hexpand(True)
        self._search.connect("search-changed", lambda _e: self._apply_filter())

        self._filter = Gtk.DropDown(model=Gtk.StringList.new(FILTER_CHOICES))
        self._filter.set_selected(0)
        self._filter.connect("notify::selected", lambda *_a: self._apply_filter())

        filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filter_bar.set_margin_top(12)
        filter_bar.set_margin_bottom(6)
        filter_bar.set_margin_start(12)
        filter_bar.set_margin_end(12)
        filter_bar.append(self._search)
        filter_bar.append(self._filter)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.add_css_class("navigation-sidebar")
        self._listbox.set_valign(Gtk.Align.START)

        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroller.set_vexpand(True)
        list_clamp = Adw.Clamp(maximum_size=900, tightening_threshold=700)
        list_clamp.set_child(self._listbox)
        list_scroller.set_child(list_clamp)

        empty = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title="No applications match",
            description="Adjust the search text or the filter.",
        )
        empty.set_vexpand(True)

        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._stack.add_named(list_scroller, "list")
        self._stack.add_named(empty, "empty")
        self._stack.set_visible_child_name("list")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(filter_bar)
        content.append(self._stack)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(content)
        self.set_child(toolbar)

    def refresh(self) -> None:
        self._apps = apps.scan_installed(include_hidden=settings.show_hidden())
        self._populate()
        self._title.set_subtitle(f"{len(self._apps)} applications")
        self._load_updates_async()

    def _populate(self) -> None:
        self._listbox.remove_all()
        self._rows = []
        for app in self._apps:
            row = Adw.ActionRow(title=app.display_name)
            subtitle_parts = [apps.kind_label(app.kind)]
            if app.version:
                subtitle_parts.append(app.version)
            row.set_subtitle(escape_markup(" · ".join(subtitle_parts)))
            row.set_activatable(True)
            row.add_css_class("app-list-row")
            row.add_prefix(icon_image(app.entry.icon, 40))
            row.add_suffix(chevron())
            row._app = app  # type: ignore[attr-defined]
            row._search = " ".join(
                [
                    app.desktop_id,
                    app.display_name,
                    app.entry.comment or "",
                    app.kind,
                    app.version or "",
                ]
            ).lower()
            row.connect("activated", self._on_row_activated)
            self._listbox.append(row)
            self._rows.append(row)
        self._apply_filter()

    def _on_row_activated(self, row: Gtk.ListBoxRow) -> None:
        app = getattr(row, "_app", None)
        if app is not None:
            self._on_open(app)

    def _apply_filter(self) -> None:
        query = self._search.get_text().strip().lower()
        choice = FILTER_CHOICES[max(0, min(self._filter.get_selected(), len(FILTER_CHOICES) - 1))]
        visible = 0
        for row in self._rows:
            app = getattr(row, "_app", None)
            show = True
            if query and query not in getattr(row, "_search", ""):
                show = False
            if show and choice != "All":
                if choice == "System":
                    show = app is not None and app.kind not in ("flatpak", "appimage", "tarball")
                else:
                    show = app is not None and app.kind == FILTER_KINDS[choice]
            row.set_visible(show)
            if show:
                visible += 1
        self._stack.set_visible_child_name("list" if visible else "empty")

    def _load_updates_async(self) -> None:
        def worker() -> None:
            found: list = []
            try:
                from ..backends import flatpak

                if flatpak.available():
                    found = flatpak.updates()
            except Exception:  # noqa: BLE001 - updates are advisory
                found = []
            GLib.idle_add(self._apply_updates, found)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_updates(self, found: list) -> bool:
        self._updates = found
        if found:
            count = len(found)
            self._updates_button.set_label(
                f"{count} Flatpak update" if count == 1 else f"{count} Flatpak updates"
            )
            self._updates_button.set_visible(True)
        else:
            self._updates_button.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _on_updates_clicked(self, _button: Gtk.Button) -> None:
        if not self._updates:
            return
        updates = list(self._updates)
        root = self.get_root()

        def worker(progress, cancel) -> int:
            from ..backends import flatpak

            scope = settings.flatpak_scope()
            for item in updates:
                progress(f"Updating {item.name or item.application}…")
                flatpak.update(item.application, scope, progress=progress, cancel=cancel)
            return len(updates)

        dialog = OperationDialog(
            "Updating Flatpak applications",
            worker,
            on_success=lambda count: self._on_updates_done(count),
            parent=root,
        )
        dialog.present(root)

    def _on_updates_done(self, count: int) -> None:
        show_toast(self, f"Updated {count} application" + ("" if count == 1 else "s"))
        self.refresh()
