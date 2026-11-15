"""Installed applications list page."""

from __future__ import annotations

import re
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import apps, db  # noqa: E402
from ..i18n import N_, _, ngettext  # noqa: E402
from . import settings  # noqa: E402
from .dialogs import OperationDialog  # noqa: E402
from .widgets import chevron, escape_markup, icon_image, show_toast  # noqa: E402

FILTER_KEYS = ["all", "flatpak", "appimage", "tarball", "system"]
FILTER_LABELS = [N_("All"), N_("Flatpak"), N_("AppImage"), N_("Tarball"), N_("System")]
FILTER_KINDS = {"flatpak": "flatpak", "appimage": "appimage", "tarball": "tarball"}

SORT_KEY = "sort"
DEFAULT_SORT = "name"
SORT_OPTIONS = [
    ("name", N_("Name (A–Z)")),
    ("name_desc", N_("Name (Z–A)")),
    ("kind", N_("Kind")),
    ("size", N_("Size (largest first)")),
    ("version", N_("Version")),
    ("recent", N_("Recently installed (newest first)")),
]
SORT_KEYS = [key for key, _label in SORT_OPTIONS]


class _Descending:
    """Wrap a value so tuple ordering compares it in reverse."""

    __slots__ = ("value",)

    def __init__(self, value) -> None:
        self.value = value

    def __eq__(self, other) -> bool:
        return isinstance(other, _Descending) and self.value == other.value

    def __lt__(self, other) -> bool:
        return self.value > other.value

    def __hash__(self) -> int:
        return hash(self.value)


def _natural_key(text: str) -> tuple:
    """Split text into digit and non-digit runs so 1.10 sorts after 1.9."""
    key: list[tuple[int, object]] = []
    for part in re.split(r"(\d+)", (text or "").casefold()):
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def _sort_key(app: apps.InstalledApp, key: str):
    name = app.display_name.casefold()
    desktop_id = app.desktop_id.casefold()
    if key == "name_desc":
        return _Descending((name, desktop_id))
    if key == "kind":
        return (apps.kind_label(app.kind).casefold(), name, desktop_id)
    if key == "size":
        return (-int(app.size_bytes or 0), name, desktop_id)
    if key == "version":
        if not app.version:
            return (1, name, desktop_id)
        return (0, _natural_key(app.version), name, desktop_id)
    if key == "recent":
        row = app.db or {}
        stamp = row.get("install_date") or row.get("last_launched") or ""
        if not stamp:
            return (1, name, desktop_id)
        return (0, _Descending(stamp), name, desktop_id)
    return (name, desktop_id)


def sort_apps(items, key: str = DEFAULT_SORT) -> list:
    """Return ``items`` sorted by one of the Installed page sort keys."""
    if key not in SORT_KEYS:
        key = DEFAULT_SORT
    return sorted(items, key=lambda app: _sort_key(app, key))


class InstalledPage(Adw.NavigationPage):
    def __init__(self, on_open) -> None:
        super().__init__()
        self.set_title(_("Installed"))
        self._on_open = on_open
        self._apps: list[apps.InstalledApp] = []
        self._rows: list[Gtk.ListBoxRow] = []
        self._updates: list = []
        self._sort = DEFAULT_SORT

        self._title = Adw.WindowTitle(title=_("Installed"))
        header = Adw.HeaderBar()
        header.set_title_widget(self._title)

        self._updates_button = Gtk.Button(label=_("Check updates"))
        self._updates_button.add_css_class("flat")
        self._updates_button.set_visible(False)
        self._updates_button.connect("clicked", self._on_updates_clicked)
        header.pack_start(self._updates_button)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text(_("Refresh the application list"))
        refresh.connect("clicked", lambda _b: self.refresh())
        header.pack_end(refresh)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Search applications"))
        self._search.set_hexpand(True)
        self._search.connect("search-changed", lambda _e: self._apply_filter())

        self._filter = Gtk.DropDown(
            model=Gtk.StringList.new([_(label) for label in FILTER_LABELS])
        )
        self._filter.set_selected(0)
        self._filter.connect("notify::selected", lambda *_a: self._apply_filter())

        saved_sort = db.setting(SORT_KEY, DEFAULT_SORT) or DEFAULT_SORT
        if saved_sort not in SORT_KEYS:
            saved_sort = DEFAULT_SORT
        self._sort = saved_sort
        self._sort_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([_(label) for _key, label in SORT_OPTIONS])
        )
        self._sort_dropdown.set_selected(SORT_KEYS.index(saved_sort))
        self._sort_dropdown.set_tooltip_text(_("Sort the application list"))
        self._sort_dropdown.connect("notify::selected", self._on_sort_changed)
        sort_label = Gtk.Label(label=_("Sort by"))
        sort_label.add_css_class("dim-label")

        filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filter_bar.set_margin_top(12)
        filter_bar.set_margin_bottom(6)
        filter_bar.set_margin_start(12)
        filter_bar.set_margin_end(12)
        filter_bar.append(self._search)
        filter_bar.append(self._filter)
        filter_bar.append(sort_label)
        filter_bar.append(self._sort_dropdown)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.add_css_class("navigation-sidebar")
        self._listbox.set_valign(Gtk.Align.START)
        self._listbox.set_sort_func(self._sort_rows)

        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroller.set_vexpand(True)
        list_clamp = Adw.Clamp(maximum_size=900, tightening_threshold=700)
        list_clamp.set_child(self._listbox)
        list_scroller.set_child(list_clamp)

        empty = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title=_("No applications match"),
            description=_("Adjust the search text or the filter."),
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
        self._title.set_subtitle(
            ngettext("%d application", "%d applications", len(self._apps))
            % len(self._apps)
        )
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

    def _on_sort_changed(self, dropdown: Gtk.DropDown, *_args) -> None:
        index = max(0, min(dropdown.get_selected(), len(SORT_KEYS) - 1))
        self._sort = SORT_KEYS[index]
        db.set_setting(SORT_KEY, self._sort)
        self._apply_filter()

    def _sort_rows(self, first: Gtk.ListBoxRow, second: Gtk.ListBoxRow) -> int:
        left = getattr(first, "_app", None)
        right = getattr(second, "_app", None)
        if left is None or right is None:
            return 0
        left_key = _sort_key(left, self._sort)
        right_key = _sort_key(right, self._sort)
        if left_key < right_key:
            return -1
        if left_key > right_key:
            return 1
        return 0

    def _apply_filter(self) -> None:
        query = self._search.get_text().strip().lower()
        index = max(0, min(self._filter.get_selected(), len(FILTER_KEYS) - 1))
        choice = FILTER_KEYS[index]
        visible = 0
        for row in self._rows:
            app = getattr(row, "_app", None)
            show = True
            if query and query not in getattr(row, "_search", ""):
                show = False
            if show and choice != "all":
                if choice == "system":
                    show = app is not None and app.kind not in ("flatpak", "appimage", "tarball")
                else:
                    show = app is not None and app.kind == FILTER_KINDS[choice]
            row.set_visible(show)
            if show:
                visible += 1
        self._listbox.invalidate_sort()
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
                ngettext("%d Flatpak update", "%d Flatpak updates", count) % count
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
                progress(_("Updating %s…") % (item.name or item.application))
                flatpak.update(item.application, scope, progress=progress, cancel=cancel)
            return len(updates)

        dialog = OperationDialog(
            _("Updating Flatpak applications"),
            worker,
            on_success=lambda count: self._on_updates_done(count),
            parent=root,
        )
        dialog.present(root)

    def _on_updates_done(self, count: int) -> None:
        show_toast(
            self,
            ngettext("Updated %d application", "Updated %d applications", count) % count,
        )
        self.refresh()
