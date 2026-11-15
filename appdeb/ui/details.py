"""Application details page: metadata, resources, files, permissions."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import apps, db, desktop, icons, processes  # noqa: E402
from ..services import autostart  # noqa: E402
from . import settings  # noqa: E402
from .dialogs import OperationDialog, confirm  # noqa: E402
from .widgets import (  # noqa: E402
    badge,
    escape_markup,
    file_filter,
    file_filters,
    icon_image,
    monospace_view,
    show_toast,
)

GROUP = "Desktop Entry"

PERMISSIONS = [
    ("Network", "shared", "network", "share", "unshare"),
    ("IPC", "shared", "ipc", "share", "unshare"),
    ("Wayland", "sockets", "wayland", "socket", "nosocket"),
    ("X11", "sockets", "x11", "socket", "nosocket"),
    ("Audio", "sockets", "pulseaudio", "socket", "nosocket"),
    ("Home folder", "filesystems", "home", "filesystem", "nofilesystem"),
    ("Host filesystem", "filesystems", "host", "filesystem", "nofilesystem"),
]


def _key(entry, key: str) -> str:
    try:
        kf = desktop.load_keyfile(entry.path)
        return kf.get_string(GROUP, key)
    except (GLib.Error, OSError):
        return ""


def _parse_overrides(text: str) -> dict[str, set[str]]:
    state: dict[str, set[str]] = {"shared": set(), "sockets": set(), "filesystems": set()}
    in_context = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_context = line == "[Context]"
            continue
        if not in_context or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key not in state:
            state[key] = set()
        for item in value.split(";"):
            item = item.strip()
            if item:
                state[key].add(item)
    return state


class DetailsPage(Adw.NavigationPage):
    def __init__(self, app: apps.InstalledApp, on_changed=None) -> None:
        super().__init__()
        self.app = app
        self._on_changed = on_changed
        self._timer_id = 0
        self._autostart_guard = False
        self._perm_guard = False
        self._file_dialog = None
        self._build_token = 0
        self._store_group: Adw.PreferencesGroup | None = None
        self._screenshot_group: Adw.PreferencesGroup | None = None
        self._sources_group: Adw.PreferencesGroup | None = None
        self._title = Adw.WindowTitle(title=app.display_name)
        self._build()
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self._build_token += 1
        self.set_title(self.app.display_name)
        self._title.set_title(self.app.display_name)
        self._title.set_subtitle(apps.kind_label(self.app.kind))

        header = Adw.HeaderBar()
        header.set_title_widget(self._title)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(18)
        body.set_margin_bottom(24)
        body.set_margin_start(18)
        body.set_margin_end(18)
        body.append(self._build_header_card())
        body.append(self._build_store_group())
        body.append(self._build_screenshots_group())
        body.append(self._build_details_group())
        body.append(self._build_wm_group())
        body.append(self._build_parameters_group())
        body.append(self._build_resource_group())
        body.append(self._build_files_group())
        body.append(self._build_sources_group())
        flatpak_group = self._build_flatpak_group()
        if flatpak_group is not None:
            body.append(flatpak_group)
        body.append(self._build_autostart_group())
        appimage_group = self._build_appimage_group()
        if appimage_group is not None:
            body.append(appimage_group)
        tarball_group = self._build_tarball_group()
        if tarball_group is not None:
            body.append(tarball_group)
        deb_group = self._build_deb_group()
        if deb_group is not None:
            body.append(deb_group)
        snap_group = self._build_snap_group()
        if snap_group is not None:
            body.append(snap_group)

        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=700)
        clamp.set_child(body)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(clamp)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        self.set_child(toolbar)
        self._load_store_metadata()

    def _build_header_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("card")
        card.set_margin_top(6)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        top.set_margin_top(18)
        top.set_margin_start(18)
        top.set_margin_end(18)
        top.append(icon_image(self.app.entry.icon, 96))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        text.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=self.app.display_name, xalign=0.0)
        name.add_css_class("title-1")
        name.set_wrap(True)
        text.append(name)
        if self.app.entry.comment:
            comment = Gtk.Label(label=self.app.entry.comment, xalign=0.0)
            comment.set_wrap(True)
            comment.add_css_class("dim-label")
            text.append(comment)

        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        badges.append(badge(apps.kind_label(self.app.kind)))
        badges.append(badge("User" if self.app.scope == "user" else "System", "Installation scope"))
        version = self.app.version or _key(self.app.entry, "X-Appdeb-Version")
        if version:
            badges.append(badge(f"Version {version}"))
        text.append(badges)
        top.append(text)

        actions = Gtk.FlowBox()
        actions.set_selection_mode(Gtk.SelectionMode.NONE)
        actions.set_max_children_per_line(4)
        actions.set_column_spacing(8)
        actions.set_row_spacing(8)
        actions.set_margin_start(18)
        actions.set_margin_end(18)
        actions.set_margin_bottom(18)
        actions.append(self._action_button("Launch", "media-playback-start-symbolic", self._on_launch, "suggested-action"))
        actions.append(self._action_button("Desktop Shortcut", "user-desktop-symbolic", self._on_shortcut))
        actions.append(self._action_button("Change Icon", "image-x-generic-symbolic", self._on_change_icon))
        actions.append(self._action_button("Reset Icon", "edit-undo-symbolic", self._on_reset_icon))
        actions.append(self._action_button("Rename", "edit-rename-symbolic", self._on_rename))
        actions.append(self._action_button("Uninstall", "user-trash-symbolic", self._on_uninstall, "destructive-action"))

        card.append(top)
        card.append(actions)
        return card

    def _action_button(self, label: str, icon: str, handler, css: str | None = None) -> Gtk.Button:
        button = Gtk.Button()
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        content.append(Gtk.Image.new_from_icon_name(icon))
        content.append(Gtk.Label(label=label))
        button.set_child(content)
        button.set_tooltip_text(label)
        if css:
            button.add_css_class(css)
        button.connect("clicked", lambda _b: handler())
        return button

    def _info_row(self, title: str, value: str, *, selectable: bool = False) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=escape_markup(value or "—"))
        if selectable:
            row.set_subtitle_selectable(True)
        return row

    def _build_details_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Details")
        group.add(self._info_row("Type", apps.kind_label(self.app.kind)))
        group.add(self._info_row("Version", self.app.version))
        group.add(self._info_row("Size", apps.human_size(self.app.size_bytes)))
        group.add(self._info_row("Scope", "User" if self.app.scope == "user" else "System"))
        installed = (self.app.db or {}).get("install_date") or ""
        group.add(self._info_row("Installed", installed[:10]))
        group.add(self._info_row("Source / reference", self.app.source_ref))
        group.add(self._info_row("Desktop entry ID", self.app.desktop_id, selectable=True))
        group.add(self._info_row("Desktop file", str(self.app.entry.path), selectable=True))
        group.add(self._info_row("Exec", self.app.entry.exec_line, selectable=True))
        return group

    def _build_wm_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Dock icon association",
            description=(
                "The window class (WM_CLASS) GNOME uses to match this application's "
                "windows to the desktop entry."
            ),
        )
        self._wm_row = Adw.EntryRow(title="Window class")
        self._wm_row.set_text(self.app.entry.startup_wm_class)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._on_save_wm_class)
        self._wm_row.add_suffix(save)
        group.add(self._wm_row)
        return group

    def _build_resource_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Resources")
        self._cpu_value = badge("—")
        self._rss_value = badge("—")
        self._proc_value = badge("—")
        self._thread_value = badge("—")
        self._io_value = badge("—")
        self._started_value = badge("—")
        for title, value in (
            ("CPU", self._cpu_value),
            ("Memory", self._rss_value),
            ("Processes", self._proc_value),
            ("Threads", self._thread_value),
            ("Disk read / write", self._io_value),
            ("Started", self._started_value),
        ):
            row = Adw.ActionRow(title=title)
            value.add_css_class("stat-value")
            row.add_suffix(value)
            group.add(row)
        end_row = Adw.ActionRow(
            title="End processes",
            subtitle="Send SIGTERM to this application's processes",
        )
        end_button = Gtk.Button(label="End processes")
        end_button.add_css_class("destructive-action")
        end_button.connect("clicked", lambda _b: self._on_end_processes())
        end_row.add_suffix(end_button)
        group.add(end_row)
        return group

    def _build_files_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Files")
        for label, path in apps.candidate_files(self.app):
            row = Adw.ActionRow(title=label, subtitle=escape_markup(path))
            row.set_subtitle_selectable(True)
            open_button = Gtk.Button(icon_name="folder-open-symbolic")
            open_button.set_tooltip_text("Open location")
            open_button.add_css_class("flat")
            open_button.connect("clicked", lambda _b, p=path: self._open_path(p))
            row.add_suffix(open_button)
            group.add(row)
        return group

    # ----------------------------------------------------------- store details
    def _build_store_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Store details")
        self._store_group = group
        spinner_row = Adw.ActionRow(title="Loading store details…")
        spinner = Gtk.Spinner()
        spinner.set_valign(Gtk.Align.CENTER)
        spinner.start()
        spinner_row.add_suffix(spinner)
        group.add(spinner_row)
        self._store_status_row = spinner_row
        return group

    def _build_screenshots_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Screenshots")
        group.set_visible(False)
        self._screenshot_group = group
        return group

    def _load_store_metadata(self) -> None:
        token = self._build_token
        app = self.app

        def worker() -> None:
            data = None
            try:
                from .. import storemeta

                data = storemeta.details(app)
            except Exception:  # noqa: BLE001 - store metadata is optional
                data = None
            if data is None:
                GLib.idle_add(self._hide_store_groups, token)
                return
            GLib.idle_add(self._apply_store_details, token, data)
            shots = list(getattr(data, "screenshots", None) or [])[:4]
            if not shots:
                GLib.idle_add(self._hide_screenshots, token)
                return
            GLib.idle_add(self._apply_screenshot_meta, token, data, shots)

        threading.Thread(target=worker, daemon=True).start()

    def _hide_store_groups(self, token: int) -> bool:
        if token != self._build_token:
            return GLib.SOURCE_REMOVE
        if self._store_group is not None:
            self._store_group.set_visible(False)
        if self._screenshot_group is not None:
            self._screenshot_group.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _hide_screenshots(self, token: int) -> bool:
        if token != self._build_token:
            return GLib.SOURCE_REMOVE
        if self._screenshot_group is not None:
            self._screenshot_group.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _apply_store_details(self, token: int, data) -> bool:
        if token != self._build_token or self._store_group is None:
            return GLib.SOURCE_REMOVE
        group = self._store_group
        status = getattr(self, "_store_status_row", None)
        if status is not None:
            status.set_visible(False)
        source = (getattr(data, "source", "") or "Store").strip()
        name = getattr(data, "name", "") or ""
        app_id = getattr(data, "app_id", "") or ""
        self._store_app_id = app_id
        expander = Adw.ExpanderRow(title=f"{source.title()} store", subtitle=escape_markup(name or app_id))
        expander.set_expanded(True)

        description = getattr(data, "description", "") or getattr(data, "summary", "") or ""
        if description:
            label = Gtk.Label(label=description)
            label.set_wrap(True)
            label.set_selectable(True)
            label.set_xalign(0.0)
            label.set_margin_top(8)
            label.set_margin_bottom(8)
            label.set_margin_start(12)
            label.set_margin_end(12)
            description_row = Adw.ActionRow()
            description_row.set_child(label)
            expander.add_row(description_row)

        for title, key in (
            ("Developer", "developer"),
            ("License", "license"),
        ):
            value = getattr(data, key, "") or ""
            if value:
                expander.add_row(self._info_row(title, value, selectable=True))

        homepage = getattr(data, "homepage", "") or ""
        if homepage:
            row = self._info_row("Homepage", homepage, selectable=True)
            link = Gtk.Button(icon_name="adw-external-link-symbolic")
            link.set_tooltip_text("Open homepage")
            link.add_css_class("flat")
            link.connect("clicked", lambda _b, url=homepage: self._open_uri(url))
            row.add_suffix(link)
            expander.add_row(row)

        if app_id:
            expander.add_row(self._info_row("Store ID", app_id, selectable=True))

        group.add(expander)
        group.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _apply_screenshot_meta(self, token: int, data, shots) -> bool:
        if token != self._build_token or self._screenshot_group is None:
            return GLib.SOURCE_REMOVE
        group = self._screenshot_group
        app_id = getattr(data, "app_id", "") or ""
        self._store_app_id = app_id

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_hexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        pairs: list[tuple[Gtk.Picture, object]] = []
        for shot in shots:
            picture = Gtk.Picture()
            picture.set_size_request(-1, 170)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_can_shrink(True)
            overlay = Gtk.Overlay()
            overlay.set_child(picture)
            caption = getattr(shot, "caption", "") or ""
            if caption:
                label = Gtk.Label(label=caption)
                label.add_css_class("caption")
                label.add_css_class("osd")
                label.set_valign(Gtk.Align.END)
                label.set_halign(Gtk.Align.FILL)
                label.set_margin_bottom(6)
                label.set_margin_start(6)
                label.set_margin_end(6)
                label.set_max_width_chars(36)
                overlay.add_overlay(label)
            click = Gtk.GestureClick()
            click.connect("released", self._on_screenshot_clicked, shot)
            picture.add_controller(click)
            box.append(overlay)
            pairs.append((picture, shot))
        scroller.set_child(box)
        group.add(scroller)
        group.set_visible(True)
        self._download_screenshots(token, app_id, pairs)
        return GLib.SOURCE_REMOVE

    def _download_screenshots(
        self, token: int, app_id: str, pairs: list[tuple[Gtk.Picture, object]]
    ) -> None:
        def worker() -> None:
            for picture, shot in pairs:
                path = None
                try:
                    from .. import storemeta

                    path = storemeta.download_screenshot(app_id, shot)
                except Exception:  # noqa: BLE001 - broken thumbnails are skipped
                    path = None
                if path:
                    GLib.idle_add(self._set_screenshot_file, token, picture, shot, path)

        threading.Thread(target=worker, daemon=True).start()

    def _set_screenshot_file(self, token: int, picture: Gtk.Picture, shot, path) -> bool:
        if token != self._build_token:
            return GLib.SOURCE_REMOVE
        target = Path(path)
        if not target.is_file():
            return GLib.SOURCE_REMOVE
        try:
            picture.set_filename(str(target))
        except Exception:  # noqa: BLE001 - older GTK fallback
            try:
                picture.set_file(Gio.File.new_for_path(str(target)))
            except Exception:  # noqa: BLE001
                return GLib.SOURCE_REMOVE
        try:
            shot.local_path = target
        except Exception:  # noqa: BLE001 - frozen dataclass; rely on cache by URL
            pass
        return GLib.SOURCE_REMOVE

    def _on_screenshot_clicked(
        self, _gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float, shot
    ) -> None:
        path = getattr(shot, "local_path", None)
        if path is None:
            try:
                from .. import storemeta

                path = storemeta.download_screenshot(
                    getattr(self, "_store_app_id", "") or "", shot
                )
            except Exception:  # noqa: BLE001
                path = None
        if path is None or not Path(path).is_file():
            show_toast(self, "Screenshot is not available")
            return
        caption = getattr(shot, "caption", "") or ""
        dialog = Adw.Dialog()
        dialog.set_title(caption or "Screenshot")
        dialog.set_content_width(900)
        dialog.set_content_height(650)
        header = Adw.HeaderBar()
        picture = Gtk.Picture.new_for_filename(str(path))
        picture.set_can_shrink(True)
        picture.set_vexpand(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.append(picture)
        if caption:
            label = Gtk.Label(label=caption)
            label.set_wrap(True)
            label.set_xalign(0.0)
            label.add_css_class("dim-label")
            content.append(label)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(content)
        dialog.set_child(toolbar)
        dialog.present(self.get_root())

    def _open_uri(self, url: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as exc:
            show_toast(self, f"Could not open link: {exc.message}")

    # ----------------------------------------------------- change package manager
    def _build_sources_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Change package manager",
            description=(
                "Install this application from another package manager. "
                "The current copy is not touched until you remove it."
            ),
        )
        status = Adw.ActionRow(title="Looking for alternative package sources…")
        spinner = Gtk.Spinner()
        spinner.set_valign(Gtk.Align.CENTER)
        spinner.start()
        status.add_suffix(spinner)
        group.add(status)
        self._sources_status = status
        self._sources_group = group
        token = self._build_token
        app = self.app

        def worker() -> None:
            candidates: list = []
            error = ""
            try:
                from ..sources import find_sources

                candidates = find_sources(app)
            except Exception as exc:  # noqa: BLE001 - shown inline
                error = str(exc) or exc.__class__.__name__
            GLib.idle_add(self._apply_sources, token, group, candidates, error)

        threading.Thread(target=worker, daemon=True).start()
        return group

    def _apply_sources(
        self,
        token: int,
        group: Adw.PreferencesGroup,
        candidates: list,
        error: str,
    ) -> bool:
        if token != self._build_token:
            return GLib.SOURCE_REMOVE
        status = getattr(self, "_sources_status", None)
        if error:
            if status is not None:
                status.set_title(f"Could not search for alternative sources: {error}")
                status.set_visible(True)
            return GLib.SOURCE_REMOVE
        if not candidates:
            if status is not None:
                status.set_title("No alternative package sources found.")
                status.set_visible(True)
            return GLib.SOURCE_REMOVE
        if status is not None:
            status.set_visible(False)
        for candidate in candidates:
            kind = getattr(candidate, "kind", "") or ""
            name = getattr(candidate, "name", "") or getattr(candidate, "ref", "") or ""
            ref = getattr(candidate, "ref", "") or ""
            version = getattr(candidate, "version", "") or ""
            origin = getattr(candidate, "origin", "") or ""
            summary = getattr(candidate, "summary", "") or ""
            if len(summary) > 90:
                summary = summary[:87].rstrip() + "…"
            parts = [part for part in (ref, version, origin) if part]
            subtitle = " · ".join(parts)
            if summary:
                subtitle = f"{subtitle} · {summary}" if subtitle else summary
            if kind == "snap":
                subtitle = (
                    f"{subtitle} · Snap installs prompt for authentication"
                    if subtitle
                    else "Snap installs prompt for authentication"
                )
            label = self._kind_label(kind)
            row = Adw.ActionRow(title=escape_markup(f"{label} · {name}"), subtitle=escape_markup(subtitle))
            row.set_subtitle_lines(2)
            install = Gtk.Button(label="Install")
            install.add_css_class("suggested-action")
            install.connect(
                "clicked", lambda _b, c=candidate: self._install_candidate(c)
            )
            row.add_suffix(install)
            group.add(row)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _kind_label(kind: str) -> str:
        return {
            "brew": "Homebrew",
            "snap": "Snap",
            "flatpak": "Flatpak",
        }.get(kind, apps.kind_label(kind))

    def _install_candidate(self, candidate) -> None:
        name = getattr(candidate, "name", "") or getattr(candidate, "ref", "") or "package"
        scope = settings.flatpak_scope()

        def worker(progress, cancel):
            from ..sources import install_candidate

            return install_candidate(
                candidate,
                scope=scope,
                progress=progress,
                cancel=cancel,
            )

        dialog = OperationDialog(
            f"Installing {name}",
            worker,
            on_success=lambda _result: self._candidate_installed(candidate),
            on_error=lambda exc: show_toast(self, f"Installation failed: {exc}"),
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _candidate_installed(self, candidate) -> None:
        name = getattr(candidate, "name", "") or getattr(candidate, "ref", "") or "package"
        current = apps.kind_label(self.app.kind)
        confirm(
            self.get_root(),
            "Remove the current copy?",
            f"Remove the {current.lower()} copy of {self.app.display_name}?\n\n"
            f"{name} was installed with {self._kind_label(getattr(candidate, 'kind', ''))}. "
            "You can keep both copies.",
            confirm_label="Remove current copy",
            destructive=True,
            on_confirm=self._uninstall_current_kind,
        )

    def _uninstall_current_kind(self) -> None:
        def worker(progress, cancel):
            from ..sources import uninstall_kind

            return uninstall_kind(self.app, progress=progress, cancel=cancel)

        dialog = OperationDialog(
            f"Removing {self.app.display_name}",
            worker,
            on_success=lambda _result: self._notify_changed(),
            on_error=lambda exc: show_toast(self, f"Removal failed: {exc}"),
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    # --------------------------------------------------- application parameters
    def _fresh_entry(self):
        try:
            fresh = desktop.load(self.app.entry.path)
        except Exception:  # noqa: BLE001 - fall back to the cached entry
            fresh = None
        return fresh or self.app.entry

    def _build_parameters_group(self) -> Adw.PreferencesGroup:
        entry = self._fresh_entry()
        group = Adw.PreferencesGroup(
            title="Application parameters",
            description="Values written to this application's desktop entry.",
        )
        categories = ";".join(entry.categories or []) or _key(entry, "Categories")
        rows = (
            ("Comment", "Comment", entry.comment or _key(entry, "Comment")),
            ("Categories", "Categories", categories),
            ("Keywords", "Keywords", _key(entry, "Keywords")),
            ("Exec", "Exec", entry.exec_line),
        )
        for title, key, value in rows:
            row = Adw.EntryRow(title=title)
            row.set_text(value or "")
            save = Gtk.Button(label="Save")
            save.connect(
                "clicked",
                lambda _b, r=row, k=key: self._save_parameter(r, k),
            )
            row.add_suffix(save)
            group.add(row)

        terminal = Adw.SwitchRow(
            title="Terminal application",
            subtitle="Run this application in a terminal window",
        )
        terminal.set_active(bool(entry.terminal))
        terminal.connect("notify::active", self._on_terminal_toggled)
        group.add(terminal)

        hidden = Adw.SwitchRow(
            title="Hide from application menu",
            subtitle="Keep the desktop entry but omit it from application menus",
        )
        hidden.set_active(bool(entry.no_display))
        hidden.connect("notify::active", self._on_nodisplay_toggled)
        group.add(hidden)
        return group

    def _save_parameter(self, row: Adw.EntryRow, key: str) -> None:
        value = row.get_text().strip()
        if key == "Exec" and not value:
            show_toast(self, "Exec cannot be empty")
            return
        if key == "Categories" and value and not value.endswith(";"):
            value += ";"
            row.set_text(value)
        try:
            fresh = desktop.set_string(self._fresh_entry(), key, value)
        except (OSError, GLib.Error) as exc:
            show_toast(self, f"Could not update {key}: {exc}")
            return
        self.app.entry = fresh
        show_toast(self, f"{key} updated")
        self._notify_changed()

    def _on_terminal_toggled(self, row: Adw.SwitchRow, _param) -> None:
        value = "true" if row.get_active() else "false"
        try:
            fresh = desktop.set_string(self._fresh_entry(), "Terminal", value)
        except (OSError, GLib.Error) as exc:
            show_toast(self, f"Could not update Terminal: {exc}")
            return
        self.app.entry = fresh
        show_toast(
            self,
            "Application runs in a terminal" if row.get_active() else "Terminal disabled",
        )
        self._notify_changed()

    def _on_nodisplay_toggled(self, row: Adw.SwitchRow, _param) -> None:
        try:
            if row.get_active():
                fresh = desktop.set_string(self._fresh_entry(), "NoDisplay", "true")
            else:
                fresh = desktop.remove_key(self._fresh_entry(), "NoDisplay")
        except (OSError, GLib.Error) as exc:
            show_toast(self, f"Could not update menu visibility: {exc}")
            return
        self.app.entry = fresh
        show_toast(
            self,
            "Hidden from the application menu"
            if row.get_active()
            else "Shown in the application menu",
        )
        self._notify_changed()

    def _build_flatpak_group(self) -> Adw.PreferencesGroup | None:
        if self.app.kind != "flatpak" and self.app.entry.source != "flatpak":
            return None
        app_id = self._flatpak_id()
        if not app_id:
            return None
        group = Adw.PreferencesGroup(
            title="Flatpak permissions",
            description="Changes apply as user overrides for this application.",
        )
        try:
            from ..backends import flatpak

            state = _parse_overrides(flatpak.overrides(app_id))
        except Exception:  # noqa: BLE001 - permissions are best effort
            state = {}
        for label, group_key, value, on_action, off_action in PERMISSIONS:
            row = Adw.SwitchRow(title=label)
            row.set_active(value in state.get(group_key, set()))
            row.connect(
                "notify::active",
                self._on_permission_toggled,
                app_id,
                group_key,
                value,
                on_action,
                off_action,
            )
            group.add(row)

        expander = Adw.ExpanderRow(title="Effective permissions", subtitle=escape_markup(app_id))
        try:
            from ..backends import flatpak

            raw = flatpak.permissions(app_id, self.app.scope)
        except Exception:  # noqa: BLE001
            raw = "Permissions are unavailable."
        expander.add_row(monospace_view(raw, height=180))
        group.add(expander)

        reset_row = Adw.ActionRow(
            title="Reset all overrides",
            subtitle="Remove every user permission override for this application",
        )
        reset_button = Gtk.Button(label="Reset")
        reset_button.connect("clicked", lambda _b: self._on_reset_overrides(app_id))
        reset_row.add_suffix(reset_button)
        group.add(reset_row)
        return group

    def _build_autostart_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Startup")
        self._autostart_row = Adw.SwitchRow(
            title="Start at login",
            subtitle="Autostart this application when you sign in",
        )
        try:
            self._autostart_row.set_active(autostart.enabled(self.app.entry))
        except Exception:  # noqa: BLE001
            pass
        self._autostart_row.connect("notify::active", self._on_autostart_toggled)
        group.add(self._autostart_row)
        return group

    def _build_appimage_group(self) -> Adw.PreferencesGroup | None:
        if self.app.kind != "appimage":
            return None
        group = Adw.PreferencesGroup(title="AppImage")
        bundle = self.app.install_path or self._first_exec_path()
        group.add(self._info_row("Bundle", str(bundle) if bundle else ""))
        replace_row = Adw.ActionRow(
            title="Replace AppImage…",
            subtitle="Back up the current bundle and install a new version",
        )
        choose = Gtk.Button(label="Choose…")
        choose.connect("clicked", lambda _b: self._on_replace_appimage())
        replace_row.add_suffix(choose)
        group.add(replace_row)

        open_row = Adw.ActionRow(title="Open bundle folder")
        open_button = Gtk.Button(icon_name="folder-open-symbolic")
        open_button.add_css_class("flat")
        if bundle is not None:
            open_button.connect(
                "clicked",
                lambda _b, p=Path(bundle).parent: self._open_path(p),
            )
        open_row.add_suffix(open_button)
        group.add(open_row)
        return group

    def _build_tarball_group(self) -> Adw.PreferencesGroup | None:
        if self.app.kind != "tarball":
            return None
        entry = self.app.entry
        install_raw = _key(entry, "X-Appdeb-InstallPath") or (
            str(self.app.install_path) if self.app.install_path else ""
        )
        install_dir = Path(install_raw) if install_raw else None
        executable_rel = _key(entry, "X-Appdeb-Executable")
        executable = (install_dir / executable_rel) if install_dir and executable_rel else self._first_exec_path()

        group = Adw.PreferencesGroup(title="Tarball")
        group.add(self._info_row("Install directory", str(install_dir) if install_dir else "", selectable=True))
        group.add(self._info_row("Executable", str(executable) if executable else "", selectable=True))
        group.add(self._info_row("Original archive", _key(entry, "X-Appdeb-Source")))
        group.add(self._info_row("Detected version", self.app.version))

        open_row = Adw.ActionRow(title="Open install folder")
        open_button = Gtk.Button(icon_name="folder-open-symbolic")
        open_button.add_css_class("flat")
        if install_dir is not None:
            open_button.connect("clicked", lambda _b, p=install_dir: self._open_path(p))
        open_row.add_suffix(open_button)
        group.add(open_row)

        change_row = Adw.ActionRow(
            title="Change executable…",
            subtitle="Pick another executable inside the install directory",
        )
        change_button = Gtk.Button(label="Choose…")
        change_button.connect(
            "clicked",
            lambda _b, d=install_dir: self._on_change_executable(d),
        )
        change_row.add_suffix(change_button)
        group.add(change_row)

        replace_row = Adw.ActionRow(
            title="Reinstall / replace archive…",
            subtitle="Keep the desktop entry, icon and name while replacing the files",
        )
        replace_button = Gtk.Button(label="Choose…")
        replace_button.connect("clicked", lambda _b: self._on_replace_tarball())
        replace_row.add_suffix(replace_button)
        group.add(replace_row)
        return group

    def _build_deb_group(self) -> Adw.PreferencesGroup | None:
        if self.app.kind not in ("deb", "system", "snap"):
            return None
        package = self._deb_package()
        if not package:
            return None
        try:
            from ..backends import deb

            info = deb.details(package)
        except Exception:  # noqa: BLE001
            info = None
        if info is None and self.app.kind != "deb":
            return None

        group = Adw.PreferencesGroup(title="Debian package")
        group.add(self._info_row("Package", package, selectable=True))
        if info is not None:
            group.add(self._info_row("Version", info.version))
            group.add(self._info_row("Architecture", info.architecture))
            group.add(self._info_row("Installed size", apps.human_size(info.installed_size * 1024)))
            group.add(self._info_row("Maintainer", info.maintainer))
            group.add(self._info_row("Dependencies", info.depends))
            group.add(self._info_row("Description", info.description))

        purge_row = Adw.ActionRow(
            title="Purge configuration files",
            subtitle="Also remove system configuration when uninstalling",
        )
        self._purge = Gtk.CheckButton()
        purge_row.add_suffix(self._purge)
        group.add(purge_row)

        hold_row = Adw.SwitchRow(
            title="Hold package version",
            subtitle="Prevent apt from upgrading this package",
        )
        hold_row.set_sensitive(False)
        hold_row.connect("notify::active", self._on_deb_hold_toggled)
        group.add(hold_row)
        self._deb_hold_row = hold_row
        self._deb_hold_package = package
        self._deb_hold_guard = False
        self._load_deb_hold(self._build_token, package)

        remove_row = Adw.ActionRow(
            title="Remove package",
            subtitle="Shows the affected packages before removal",
        )
        remove_button = Gtk.Button(label="Remove…")
        remove_button.add_css_class("destructive-action")
        remove_button.connect("clicked", lambda _b, p=package: self._on_remove_deb(p))
        remove_row.add_suffix(remove_button)
        group.add(remove_row)
        return group

    # ------------------------------------------------------------------- snap
    def _snap_name(self) -> str:
        entry = self.app.entry
        name = _key(entry, "X-SnapInstanceName")
        if name:
            return name
        stem = Path(self.app.desktop_id or entry.path.name).name
        if stem.endswith(".desktop"):
            stem = stem[: -len(".desktop")]
        if "_" in stem:
            prefix = stem.split("_", 1)[0].strip()
            if prefix:
                return prefix
        executable = self._first_exec_path()
        if executable is not None:
            return executable.name
        return (entry.name or "").strip()

    def _build_snap_group(self) -> Adw.PreferencesGroup | None:
        if self.app.kind != "snap" and self.app.entry.source != "snap":
            return None
        name = self._snap_name()
        from ..backends import snap

        available = snap.available()
        group = Adw.PreferencesGroup(title="Snap")
        if not available:
            group.set_description("The snap command is not available on this system.")
        if not name:
            group.add(
                Adw.ActionRow(
                    title="Snap name unknown",
                    subtitle="This desktop entry does not identify a snap instance",
                )
            )
            return group

        self._snap_instance = name
        self._snap_channels: list[str] = []
        self._snap_channel_index = 0
        self._snap_channel_guard = False
        self._snap_hold_guard = False

        channel_row = Adw.ComboRow(title="Channel")
        channel_row.set_model(Gtk.StringList.new([]))
        channel_row.set_sensitive(False)
        channel_row.set_subtitle(
            "Loading channels…"
            if available
            else "The snap command is not available on this system."
        )
        channel_row.connect("notify::selected", self._on_snap_channel_selected)
        group.add(channel_row)
        self._snap_channel_row = channel_row

        refresh_row = Adw.ButtonRow(title="Refresh snap now")
        refresh_row.set_sensitive(available)
        if not available:
            refresh_row.set_tooltip_text("The snap command is not available on this system.")
        refresh_row.connect("activated", lambda _row: self._on_snap_refresh())
        group.add(refresh_row)

        hold_row = Adw.SwitchRow(
            title="Hold updates",
            subtitle="Pause automatic refreshes for this snap",
        )
        hold_row.set_sensitive(False)
        hold_row.connect("notify::active", self._on_snap_hold_toggled)
        group.add(hold_row)
        self._snap_hold_row = hold_row

        if available:
            self._load_snap_parameters(self._build_token, name)
        return group

    def _load_snap_parameters(self, token: int, name: str) -> None:
        def worker() -> None:
            from ..backends import snap

            channels: dict[str, str] = {}
            error = ""
            try:
                channels = snap.channels(name)
            except Exception as exc:  # noqa: BLE001 - shown inline
                error = str(exc) or exc.__class__.__name__
            tracking = ""
            try:
                tracking = snap.info(name).get("tracking", "") or ""
            except Exception:  # noqa: BLE001 - tracking is optional
                tracking = ""
            try:
                is_held = snap.held(name)
            except Exception:  # noqa: BLE001 - default to unheld
                is_held = False
            GLib.idle_add(
                self._apply_snap_parameters,
                token,
                channels,
                tracking,
                is_held,
                error,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_snap_parameters(
        self,
        token: int,
        channels: dict[str, str],
        tracking: str,
        is_held: bool,
        error: str,
    ) -> bool:
        if token != self._build_token:
            return GLib.SOURCE_REMOVE
        names = list(channels)
        self._snap_channels = names
        row = getattr(self, "_snap_channel_row", None)
        if row is not None:
            if not names:
                row.set_sensitive(False)
                if error:
                    row.set_subtitle(escape_markup(f"Could not read snap channels: {error}"))
                else:
                    row.set_subtitle("No channels reported for this snap")
                self._snap_channel_index = -1
            else:
                index = self._preferred_channel(names, channels, tracking)
                self._snap_channel_guard = True
                row.set_model(Gtk.StringList.new(names))
                row.set_selected(index)
                self._snap_channel_index = index
                self._snap_channel_guard = False
                row.set_subtitle(
                    escape_markup(f"Following {names[index]}; switching refreshes the snap")
                )
                row.set_sensitive(True)
        hold = getattr(self, "_snap_hold_row", None)
        if hold is not None:
            self._snap_hold_guard = True
            hold.set_active(bool(is_held))
            self._snap_hold_guard = False
            hold.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _preferred_channel(
        self,
        names: list[str],
        channels: dict[str, str],
        tracking: str,
    ) -> int:
        if tracking in names:
            return names.index(tracking)
        version = self.app.version or _key(self.app.entry, "X-Appdeb-Version")
        if version:
            for index, name in enumerate(names):
                if channels.get(name) == version:
                    return index
        return 0

    def _restore_snap_channel(self) -> None:
        row = getattr(self, "_snap_channel_row", None)
        index = getattr(self, "_snap_channel_index", None)
        if row is None or index is None or index < 0:
            return
        self._snap_channel_guard = True
        row.set_selected(index)
        self._snap_channel_guard = False

    def _on_snap_channel_selected(self, row: Adw.ComboRow, _param) -> None:
        if self._snap_channel_guard:
            return
        names = list(getattr(self, "_snap_channels", []) or [])
        selected = row.get_selected()
        if not names or selected < 0 or selected >= len(names):
            return
        if selected == getattr(self, "_snap_channel_index", -1):
            return
        channel = names[selected]
        from ..backends import snap

        if not snap.available():
            self._restore_snap_channel()
            return
        dialog = confirm(
            self.get_root(),
            "Switch snap channel",
            f"Switch {self._snap_instance} to {channel}? The snap is refreshed now.",
            confirm_label="Switch channel",
            on_confirm=lambda: self._switch_snap_channel(channel, selected),
        )
        dialog.connect(
            "response",
            lambda _dialog, response: (
                None if response == "confirm" else self._restore_snap_channel()
            ),
        )

    def _switch_snap_channel(self, channel: str, selected: int) -> None:
        from ..backends import snap

        name = self._snap_instance

        def worker(progress, cancel):
            snap.refresh(name, progress=progress, cancel=cancel, channel=channel)
            return True

        dialog = OperationDialog(
            f"Switching {name} to {channel}",
            worker,
            on_success=lambda _result: self._snap_channel_switched(name, channel, selected),
            on_error=self._snap_channel_failed,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _snap_channel_switched(self, name: str, channel: str, selected: int) -> None:
        self._snap_channel_index = selected
        show_toast(self, f"{name} now follows {channel}")
        self._notify_changed()

    def _snap_channel_failed(self, exc: BaseException) -> None:
        self._restore_snap_channel()
        show_toast(self, f"Could not switch channel: {exc}")

    def _on_snap_refresh(self) -> None:
        from ..backends import snap

        name = self._snap_instance

        def worker(progress, cancel):
            snap.refresh(name, progress=progress, cancel=cancel)
            return True

        dialog = OperationDialog(
            f"Refreshing {name}",
            worker,
            on_success=lambda _result: self._snap_refreshed(name),
            on_error=lambda exc: show_toast(self, f"Could not refresh snap: {exc}"),
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _snap_refreshed(self, name: str) -> None:
        show_toast(self, f"Refreshed {name}")
        self._notify_changed()

    def _on_snap_hold_toggled(self, row: Adw.SwitchRow, _param) -> None:
        if self._snap_hold_guard:
            return
        name = getattr(self, "_snap_instance", "")
        if not name:
            return
        enabled = row.get_active()
        from ..backends import snap

        def worker(progress, cancel):
            snap.hold(name, enabled, progress=progress, cancel=cancel)
            return True

        def failed(exc: BaseException) -> None:
            self._snap_hold_guard = True
            row.set_active(not enabled)
            self._snap_hold_guard = False
            show_toast(self, f"Could not change update hold: {exc}")

        dialog = OperationDialog(
            "Holding snap updates" if enabled else "Resuming snap updates",
            worker,
            on_success=lambda _result: show_toast(
                self,
                f"Updates for {name} are held" if enabled else f"Updates for {name} resumed",
            ),
            on_error=failed,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    # -------------------------------------------------------------- deb hold
    def _load_deb_hold(self, token: int, package: str) -> None:
        def worker() -> None:
            from ..backends import deb

            try:
                is_held = deb.held(package)
            except Exception:  # noqa: BLE001 - default to unheld
                is_held = False
            GLib.idle_add(self._apply_deb_hold, token, is_held)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_deb_hold(self, token: int, is_held: bool) -> bool:
        if token != self._build_token:
            return GLib.SOURCE_REMOVE
        row = getattr(self, "_deb_hold_row", None)
        if row is None:
            return GLib.SOURCE_REMOVE
        self._deb_hold_guard = True
        row.set_active(bool(is_held))
        self._deb_hold_guard = False
        row.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _on_deb_hold_toggled(self, row: Adw.SwitchRow, _param) -> None:
        if self._deb_hold_guard:
            return
        package = getattr(self, "_deb_hold_package", "")
        if not package:
            return
        enabled = row.get_active()
        from ..backends import deb

        def worker(progress, cancel):
            deb.hold(package, enabled, progress=progress, cancel=cancel)
            return True

        def failed(exc: BaseException) -> None:
            self._deb_hold_guard = True
            row.set_active(not enabled)
            self._deb_hold_guard = False
            show_toast(self, f"Could not change package hold: {exc}")

        dialog = OperationDialog(
            "Holding package version" if enabled else "Releasing package hold",
            worker,
            on_success=lambda _result: show_toast(
                self,
                f"{package} held at its current version"
                if enabled
                else f"{package} can be upgraded again",
            ),
            on_error=failed,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    # -------------------------------------------------------------- resources
    def _on_map(self, *_args) -> None:
        self._update_resources()
        if not self._timer_id:
            self._timer_id = GLib.timeout_add_seconds(2, self._tick_resources)

    def _on_unmap(self, *_args) -> None:
        self._stop_timer()

    def _stop_timer(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    def _tick_resources(self) -> bool:
        self._update_resources()
        return GLib.SOURCE_CONTINUE

    def _update_resources(self) -> None:
        try:
            stats = processes.app_stats(self.app)
        except Exception:  # noqa: BLE001 - monitoring must never crash the UI
            return
        self._cpu_value.set_text(f"{stats.cpu_percent:.0f} %")
        self._rss_value.set_text(apps.human_size(stats.rss_bytes) or "—")
        self._proc_value.set_text(str(stats.pid_count))
        self._thread_value.set_text(str(stats.threads))
        self._io_value.set_text(
            f"{apps.human_size(stats.read_bytes) or '0 B'} / "
            f"{apps.human_size(stats.write_bytes) or '0 B'}"
        )
        if stats.started_at:
            started = datetime.fromtimestamp(stats.started_at).strftime("%Y-%m-%d %H:%M")
        else:
            started = "—"
        self._started_value.set_text(started)

    # ------------------------------------------------------------------ utils
    def _flatpak_id(self) -> str:
        return self.app.source_ref or _key(self.app.entry, "X-Flatpak")

    def _first_exec_path(self) -> Path | None:
        return apps.executable_path(self.app)

    def _deb_package(self) -> str:
        if self.app.source_ref and self.app.kind == "deb":
            return self.app.source_ref
        if self.app.kind not in ("deb", "system", "snap"):
            return ""
        try:
            from ..backends import deb

            return deb.package_for_desktop(Path(self.app.entry.path)) or ""
        except Exception:  # noqa: BLE001
            return ""

    def _open_path(self, path: Path) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(Path(path).as_uri(), None)
        except GLib.Error as exc:
            show_toast(self, f"Could not open location: {exc.message}")

    def _pop(self) -> None:
        parent = self.get_parent()
        if isinstance(parent, Adw.NavigationView):
            parent.pop()

    def _notify_changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    def _reload(self) -> None:
        fresh = apps.get_app(self.app.desktop_id)
        if fresh is None:
            self._notify_changed()
            self._pop()
            return
        self.app = fresh
        self._build()
        self._update_resources()
        self._notify_changed()

    # ---------------------------------------------------------------- actions
    def _on_launch(self) -> None:
        try:
            desktop.launch(self.app.entry)
        except Exception as exc:  # noqa: BLE001
            show_toast(self, f"Could not launch: {exc}")
            return
        show_toast(self, f"Launching {self.app.display_name}")

    def _on_shortcut(self) -> None:
        base = desktop.shortcut_path(self.app.entry)
        root = self.get_root()
        if base.exists():
            confirm(
                root,
                "Desktop shortcut",
                f"A shortcut for {self.app.display_name} already exists. Remove it?",
                confirm_label="Remove shortcut",
                destructive=True,
                on_confirm=self._remove_shortcut,
            )
        else:
            try:
                path = desktop.create_shortcut(self.app.entry)
            except OSError as exc:
                show_toast(self, f"Could not create shortcut: {exc}")
                return
            show_toast(self, f"Shortcut created: {path}")

    def _remove_shortcut(self) -> None:
        if desktop.remove_shortcut(self.app.entry):
            show_toast(self, "Shortcut removed")
        else:
            show_toast(self, "No shortcut found")

    def _on_change_icon(self) -> None:
        dialog = Gtk.FileDialog(title="Choose application icon")
        filt = file_filter("Images", ["*.png", "*.svg", "*.jpg", "*.jpeg", "*.webp"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(self.get_root(), None, self._on_icon_chosen)

    def _on_icon_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if path is None:
            return
        try:
            name = icons.install_icon(path, self.app.desktop_id)
            desktop.set_string(self.app.entry, "Icon", name)
            db.upsert(self.app.desktop_id, icon_name=name)
        except (ValueError, OSError) as exc:
            show_toast(self, f"Could not install icon: {exc}")
            return
        show_toast(self, "Application icon updated")
        self._reload()

    def _on_reset_icon(self) -> None:
        path = desktop.ensure_user_override(self.app.entry)
        original = ""
        try:
            kf = desktop.load_keyfile(path)
            original = kf.get_string(GROUP, "X-Appdeb-OriginalIcon")
        except GLib.Error:
            original = ""
        if original:
            desktop.set_string(self.app.entry, "Icon", original)
            db.upsert(self.app.desktop_id, icon_name=original)
        else:
            desktop.remove_key(self.app.entry, "Icon")
        show_toast(self, "Application icon reset")
        self._reload()

    def _on_rename(self) -> None:
        entry = Gtk.Entry()
        entry.set_text(self.app.display_name)
        entry.set_activates_default(True)
        dialog = Adw.AlertDialog(
            heading="Rename application",
            body="This changes the name shown in menus, the dock and this window.",
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_close_response("cancel")
        dialog.set_default_response("rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)

        def _on_response(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "rename":
                return
            try:
                fresh = desktop.rename(self.app.entry, entry.get_text())
            except ValueError as exc:
                show_toast(self, f"Rename failed: {exc}")
                return
            self.app.entry = fresh
            show_toast(self, "Application renamed")
            self._reload()

        dialog.connect("response", _on_response)
        dialog.present(self.get_root())

    def _on_save_wm_class(self, _button: Gtk.Button) -> None:
        value = self._wm_row.get_text().strip()
        try:
            if value:
                fresh = desktop.set_string(self.app.entry, "StartupWMClass", value)
                self.app.entry = fresh
            else:
                fresh = desktop.remove_key(self.app.entry, "StartupWMClass")
                self.app.entry = fresh
        except (OSError, GLib.Error) as exc:
            show_toast(self, f"Could not update window class: {exc}")
            return
        show_toast(self, "Dock icon association updated")
        self._notify_changed()

    def _on_end_processes(self) -> None:
        confirm(
            self.get_root(),
            "End processes",
            f"Terminate all processes belonging to {self.app.display_name}?",
            confirm_label="End processes",
            destructive=True,
            on_confirm=self._end_processes,
        )

    def _end_processes(self) -> None:
        try:
            count = processes.terminate(self.app)
        except Exception as exc:  # noqa: BLE001
            show_toast(self, f"Could not terminate processes: {exc}")
            return
        if count:
            show_toast(self, f"Ended {count} process" + ("" if count == 1 else "es"))
        else:
            show_toast(self, "No running processes")
        self._update_resources()

    # -------------------------------------------------------------- flatpak
    def _on_permission_toggled(
        self,
        row: Adw.SwitchRow,
        _param,
        app_id: str,
        group_key: str,
        value: str,
        on_action: str,
        off_action: str,
    ) -> None:
        if self._perm_guard:
            return
        active = row.get_active()
        action = on_action if active else off_action
        try:
            from ..backends import flatpak

            flatpak.set_override(app_id, action, value)
        except Exception as exc:  # noqa: BLE001
            show_toast(self, f"Could not change permission: {exc}")
            self._perm_guard = True
            row.set_active(not active)
            self._perm_guard = False
            return
        show_toast(self, f"{row.get_title()} {'enabled' if active else 'disabled'}")

    def _on_reset_overrides(self, app_id: str) -> None:
        confirm(
            self.get_root(),
            "Reset Flatpak overrides",
            f"Remove all user permission overrides for {app_id}?",
            confirm_label="Reset",
            destructive=True,
            on_confirm=lambda: self._reset_overrides(app_id),
        )

    def _reset_overrides(self, app_id: str) -> None:
        try:
            from ..backends import flatpak

            flatpak.reset_overrides(app_id)
        except Exception as exc:  # noqa: BLE001
            show_toast(self, f"Could not reset overrides: {exc}")
            return
        show_toast(self, "Flatpak overrides reset")
        self._build()

    # -------------------------------------------------------------- autostart
    def _on_autostart_toggled(self, row: Adw.SwitchRow, _param) -> None:
        if self._autostart_guard:
            return
        try:
            autostart.set_enabled(self.app.entry, row.get_active())
        except OSError as exc:
            self._autostart_guard = True
            row.set_active(not row.get_active())
            self._autostart_guard = False
            show_toast(self, f"Could not change autostart: {exc}")
            return
        show_toast(self, "Autostart enabled" if row.get_active() else "Autostart disabled")

    # --------------------------------------------------------------- AppImage
    def _on_replace_appimage(self) -> None:
        confirm(
            self.get_root(),
            "Replace AppImage",
            (
                f"{self.app.display_name} is replaced in place. Quit the application first; "
                "the current bundle is backed up next to the new one."
            ),
            confirm_label="Choose replacement",
            on_confirm=self._choose_appimage_replacement,
        )

    def _choose_appimage_replacement(self) -> None:
        dialog = Gtk.FileDialog(title="Choose replacement AppImage")
        filt = file_filter("AppImage", ["*.AppImage", "*.appimage"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(self.get_root(), None, self._on_replacement_chosen)

    def _on_replacement_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if path is None:
            return
        from ..backends import appimage

        if not appimage.is_appimage(path):
            show_toast(self, "The selected file is not an AppImage")
            return

        def worker(progress, cancel):
            return appimage.replace(self.app, path)

        dialog = OperationDialog(
            "Replacing AppImage",
            worker,
            on_success=self._on_replaced,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _on_replaced(self, _result) -> None:
        show_toast(self, "AppImage replaced; restart the application to use it")
        self._reload()

    # ---------------------------------------------------------------- tarball
    def _on_change_executable(self, install_dir: Path | None) -> None:
        if install_dir is None:
            show_toast(self, "The install directory is unknown")
            return
        dialog = Gtk.FileDialog(title="Choose executable")
        filt = Gtk.FileFilter()
        filt.set_name("Executables")
        dialog.set_default_filter(filt)
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
        self._file_dialog = dialog
        dialog.open(self.get_root(), None, lambda d, r: self._on_executable_chosen(d, r, install_dir))

    def _on_executable_chosen(self, dialog: Gtk.FileDialog, result, install_dir: Path) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if path is None:
            return
        try:
            relative = path.resolve().relative_to(Path(install_dir).resolve())
        except (OSError, ValueError):
            show_toast(self, "Choose an executable inside the install directory")
            return
        try:
            path.chmod(0o755)
        except OSError:
            pass
        fresh = desktop.set_string(self.app.entry, "Exec", str(path))
        fresh = desktop.set_string(fresh, "X-Appdeb-Executable", str(relative))
        self.app.entry = fresh
        show_toast(self, "Executable updated")
        self._reload()

    def _on_replace_tarball(self) -> None:
        confirm(
            self.get_root(),
            "Replace tarball installation",
            (
                f"The files for {self.app.display_name} are replaced and the current install "
                "directory is backed up. Quit the application first."
            ),
            confirm_label="Choose archive",
            on_confirm=self._choose_tarball_replacement,
        )

    def _choose_tarball_replacement(self) -> None:
        dialog = Gtk.FileDialog(title="Choose replacement .tar.gz")
        filt = file_filter("Archives", ["*.tar.gz", "*.tgz"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(self.get_root(), None, self._on_tarball_replacement_chosen)

    def _on_tarball_replacement_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if path is None:
            return
        from ..backends import tarball

        def worker(progress, cancel):
            return tarball.replace(self.app, path)

        dialog = OperationDialog(
            "Replacing tarball installation",
            worker,
            on_success=self._on_replaced,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    # -------------------------------------------------------------------- deb
    def _on_remove_deb(self, package: str) -> None:
        try:
            from ..backends import deb

            impact = deb.removal_impact(package)
        except Exception:  # noqa: BLE001
            impact = []
        body = f"Remove the package {package}?"
        if impact:
            body += "\n\nThe following packages will be removed:\n" + "\n".join(impact[:20])
        purge = self._purge.get_active()
        confirm(
            self.get_root(),
            "Remove Debian package",
            body,
            confirm_label="Remove",
            destructive=True,
            on_confirm=lambda: self._remove_deb(package, purge),
        )

    def _remove_deb(self, package: str, purge: bool) -> None:
        from ..backends import deb

        def worker(progress, cancel):
            deb.remove(package, purge, progress=progress, cancel=cancel)
            return True

        dialog = OperationDialog(
            f"Removing {package}",
            worker,
            on_success=self._on_removed,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _on_removed(self, _result) -> None:
        show_toast(self, "Package removed")
        self._notify_changed()
        self._pop()

    # -------------------------------------------------------------- uninstall
    def _on_uninstall(self) -> None:
        kind = self.app.kind
        if kind not in ("flatpak", "appimage", "tarball", "deb"):
            show_toast(self, "Uninstall is not supported for this entry")
            return
        body = f"Uninstall {self.app.display_name}? This cannot be undone."
        if kind == "tarball":
            body += "\n\nThe managed install directory is deleted; the original archive is kept."
        if kind == "appimage":
            body += "\n\nThe managed bundle is deleted; your downloaded file is kept."
        confirm(
            self.get_root(),
            "Uninstall application",
            body,
            confirm_label="Uninstall",
            destructive=True,
            on_confirm=lambda: self._uninstall(kind),
        )

    def _uninstall(self, kind: str) -> None:
        app = self.app

        def worker(progress, cancel):
            if kind == "flatpak":
                from ..backends import flatpak

                app_id = self._flatpak_id()
                if not app_id:
                    raise RuntimeError("The Flatpak application ID is unknown")
                flatpak.uninstall(app_id, app.scope, progress=progress, cancel=cancel)
            elif kind == "appimage":
                from ..backends import appimage

                appimage.uninstall(app)
            elif kind == "tarball":
                from ..backends import tarball

                tarball.uninstall(app)
            elif kind == "deb":
                from ..backends import deb

                package = app.source_ref or deb.package_for_desktop(Path(app.entry.path))
                if not package:
                    raise RuntimeError("The Debian package name is unknown")
                deb.remove(package, False, progress=progress, cancel=cancel)
            else:
                raise RuntimeError("Uninstall is not supported for this entry")
            return True

        dialog = OperationDialog(
            f"Uninstalling {app.display_name}",
            worker,
            on_success=self._on_uninstalled,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _on_uninstalled(self, _result) -> None:
        show_toast(self, "Application uninstalled")
        self._notify_changed()
        self._pop()
