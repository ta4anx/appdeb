"""Application details page: metadata, resources, files, permissions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import apps, db, desktop, icons, processes  # noqa: E402
from ..services import autostart  # noqa: E402
from .dialogs import OperationDialog, confirm  # noqa: E402
from .widgets import (  # noqa: E402
    badge,
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
        self._title = Adw.WindowTitle(title=app.display_name)
        self._build()
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
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
        body.append(self._build_details_group())
        body.append(self._build_wm_group())
        body.append(self._build_resource_group())
        body.append(self._build_files_group())
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
        row = Adw.ActionRow(title=title, subtitle=value or "—")
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
            row = Adw.ActionRow(title=label, subtitle=str(path))
            row.set_subtitle_selectable(True)
            open_button = Gtk.Button(icon_name="folder-open-symbolic")
            open_button.set_tooltip_text("Open location")
            open_button.add_css_class("flat")
            open_button.connect("clicked", lambda _b, p=path: self._open_path(p))
            row.add_suffix(open_button)
            group.add(row)
        return group

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

        expander = Adw.ExpanderRow(title="Effective permissions", subtitle=app_id)
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
