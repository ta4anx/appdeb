"""Package manager overview page: availability, stores and installed counts."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import apps, config, managerops  # noqa: E402
from ..i18n import _, ngettext  # noqa: E402
from .dialogs import OperationDialog, confirm  # noqa: E402
from .widgets import badge, escape_markup, show_toast  # noqa: E402

MANAGERS = ("flatpak", "snap", "brew", "apt")

VERSION_RE = re.compile(r"\d+(?:\.\d+)+(?:[+~][0-9A-Za-z.+~-]+)?")


def normalize_version(line: str) -> str:
    text = " ".join((line or "").split())
    match = VERSION_RE.search(text)
    return match.group(0) if match else text


def _capture_lines(args: list[str], *, timeout: float = 60) -> list[str] | None:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _capture_first_line(args: list[str]) -> str:
    lines = _capture_lines(args, timeout=10)
    return lines[0] if lines else ""


def _count_lines(args: list[str], *, skip_header: int = 0, predicate=None) -> int:
    lines = _capture_lines(args)
    if lines is None:
        return 0
    if skip_header:
        lines = lines[skip_header:]
    if predicate is not None:
        return sum(1 for line in lines if predicate(line))
    return len(lines)


def gather_counts(brew_path: str = "") -> dict[str, int]:
    """Count packages known to each manager; failures yield 0."""
    snap_lines = _capture_lines(["snap", "list"]) or []
    snap_count = 0
    if snap_lines:
        for line in snap_lines[1:]:
            lower = line.lower()
            if lower.startswith(("no snaps", "warning:")):
                continue
            snap_count += 1
    return {
        "flatpak": _count_lines(["flatpak", "list", "--app", "--columns=application"]),
        "snap": snap_count,
        "brew": _count_lines([brew_path or "brew", "list", "--formula"]),
        "apt": _count_lines(
            ["dpkg-query", "-W", "-f=${Status}\\n"],
            predicate=lambda line: line == "install ok installed",
        ),
    }


def _binary(*candidates: str) -> str:
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    for name in candidates:
        candidate = Path(name)
        if candidate.is_absolute() and candidate.is_file():
            return str(candidate)
    return ""


def _backend_available(module_name: str) -> bool:
    try:
        module = __import__(f"appdeb.backends.{module_name}", fromlist=["available"])
        return bool(module.available())
    except Exception:  # noqa: BLE001 - absent backend means unavailable
        return False


def gather() -> dict:
    """Collect manager availability, versions and installed application counts.

    Runs blocking subprocesses, so callers use it from a worker thread.
    """
    data: dict[str, dict] = {
        "flatpak": {
            "name": "Flatpak",
            "binary": "flatpak",
            "available": False,
            "version": "",
            "path": _binary("flatpak"),
            "count": 0,
        },
        "snap": {
            "name": "Snap",
            "binary": "snap",
            "available": False,
            "version": "",
            "path": _binary("snap"),
            "count": 0,
        },
        "brew": {
            "name": "Homebrew",
            "binary": "brew",
            "available": False,
            "version": "",
            "path": _binary("brew", "/home/linuxbrew/.linuxbrew/bin/brew"),
            "count": 0,
        },
        "apt": {
            "name": "APT / dpkg",
            "binary": "dpkg",
            "available": False,
            "version": "",
            "path": _binary("dpkg"),
            "count": 0,
        },
    }

    data["flatpak"]["version"] = normalize_version(
        _capture_first_line(["flatpak", "--version"])
    )
    data["snap"]["version"] = normalize_version(_capture_first_line(["snap", "version"]))
    data["brew"]["version"] = normalize_version(
        _capture_first_line([data["brew"]["path"] or "brew", "--version"])
    )
    data["apt"]["version"] = normalize_version(_capture_first_line(["dpkg", "--version"]))

    data["flatpak"]["available"] = _backend_available("flatpak")
    data["snap"]["available"] = _backend_available("snap")
    data["brew"]["available"] = _backend_available("brew")
    data["apt"]["available"] = bool(data["apt"]["path"])

    counts = gather_counts(brew_path=data["brew"]["path"])
    for key, count in counts.items():
        data[key]["count"] = count

    installed: list[apps.InstalledApp] = []
    try:
        installed = apps.scan_installed(include_hidden=True)
    except Exception:  # noqa: BLE001 - store counts are best effort
        installed = []

    appimage_store = config.appimage_store()
    tarball_store = config.tarball_store()

    def store_count(store: Path, kind: str) -> int:
        count = 0
        for app in installed:
            if app.kind != kind or app.install_path is None:
                continue
            try:
                Path(app.install_path).resolve().relative_to(store.resolve())
            except (OSError, ValueError):
                continue
            count += 1
        return count

    data["_stores"] = {
        "appimage": {
            "path": str(appimage_store),
            "count": store_count(appimage_store, "appimage"),
        },
        "tarball": {
            "path": str(tarball_store),
            "count": store_count(tarball_store, "tarball"),
        },
    }
    return data


class ManagersPage(Adw.NavigationPage):
    def __init__(self) -> None:
        super().__init__()
        self.set_title(_("Managers"))
        self._token: object | None = None
        self._manager_rows: dict[str, Adw.ActionRow] = {}
        self._manager_badges: dict[str, Gtk.Label] = {}
        self._manager_context: dict[str, Gtk.Label] = {}
        self._manager_buttons: dict[str, Gtk.Button] = {}
        self._count_rows: dict[str, Adw.ActionRow] = {}
        self._count_badges: dict[str, Gtk.Label] = {}
        self._store_rows: dict[str, Adw.ActionRow] = {}
        self._store_badges: dict[str, Gtk.Label] = {}

        self._title = Adw.WindowTitle(
            title=_("Managers"), subtitle=_("Package manager overview")
        )
        header = Adw.HeaderBar()
        header.set_title_widget(self._title)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text(_("Refresh package manager information"))
        refresh.connect("clicked", lambda _b: self.refresh())
        header.pack_end(refresh)

        page = Adw.PreferencesPage()

        manager_group = Adw.PreferencesGroup(
            title=_("Package managers"),
            description=_(
                "Package managers available on this system and their versions."
            ),
        )
        for key, title, binary in (
            ("flatpak", "Flatpak", "flatpak"),
            ("snap", "Snap", "snap"),
            ("brew", "Homebrew", "brew"),
            ("apt", "APT / dpkg", "dpkg"),
        ):
            row = Adw.ActionRow(title=title, subtitle=_("Checking…"))
            row.set_subtitle_lines(2)
            context = Gtk.Label(label=binary)
            context.add_css_class("dim-label")
            row.add_suffix(context)
            status = badge(_("Checking"))
            row.add_suffix(status)
            button = Gtk.Button(label=_("Install"))
            button.add_css_class("flat")
            button.connect("clicked", lambda _b, key=key: self._on_manager_action(key))
            row.add_suffix(button)
            manager_group.add(row)
            self._manager_rows[key] = row
            self._manager_badges[key] = status
            self._manager_context[key] = context
            self._manager_buttons[key] = button
        page.add(manager_group)

        store_group = Adw.PreferencesGroup(
            title=_("Managed stores"),
            description=_("Applications installed and owned by AppDeb."),
        )
        for key, title in (
            ("appimage", _("AppImage store")),
            ("tarball", _("Tarball store")),
        ):
            row = Adw.ActionRow(title=title, subtitle="—")
            row.set_subtitle_selectable(True)
            count = badge(_("0 apps"))
            row.add_suffix(count)
            store_group.add(row)
            self._store_rows[key] = row
            self._store_badges[key] = count
        page.add(store_group)

        counts_group = Adw.PreferencesGroup(
            title=_("Installed by manager"),
            description=_("Applications found through each package manager."),
        )
        for key, title in (
            ("flatpak", _("%s applications") % "Flatpak"),
            ("snap", _("%s applications") % "Snap"),
            ("brew", _("%s formulae") % "Homebrew"),
            ("apt", _("%s packages") % "Debian"),
        ):
            row = Adw.ActionRow(title=title, subtitle=_("Scanning…"))
            count = badge("—")
            row.add_suffix(count)
            counts_group.add(row)
            self._count_rows[key] = row
            self._count_badges[key] = count
        page.add(counts_group)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)

        self.refresh()

    # ------------------------------------------------------------ public API
    def refresh(self) -> None:
        token = object()
        self._token = token

        def worker() -> None:
            try:
                data = gather()
            except Exception as exc:  # noqa: BLE001 - fall back to placeholders
                data = {"_error": str(exc) or exc.__class__.__name__}
            GLib.idle_add(self._apply, token, data)

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------- render
    def _apply(self, token: object, data: dict) -> bool:
        if token is not self._token:
            return GLib.SOURCE_REMOVE
        if "_error" in data:
            for row in self._manager_rows.values():
                row.set_subtitle(_("Package manager information is unavailable"))
            return GLib.SOURCE_REMOVE

        for key, row in self._manager_rows.items():
            info = data.get(key, {})
            available = bool(info.get("available"))
            version = (info.get("version") or "").strip()
            path = (info.get("path") or "").strip()
            count = int(info.get("count", 0) or 0)
            unit = _("apps") if key in ("flatpak", "snap") else _("packages")
            if available:
                subtitle = version or _("Version unknown")
                subtitle = _("%s · %d %s") % (subtitle, count, unit)
                if path:
                    subtitle = _("%s · %s") % (subtitle, path)
                self._manager_badges[key].set_text(_("Available"))
                self._manager_context[key].set_text("")
            else:
                subtitle = _("Not installed")
                if version:
                    subtitle = _("%s · %s") % (subtitle, version)
                if path:
                    subtitle = _("%s · %s") % (subtitle, path)
                self._manager_badges[key].set_text(_("Not installed"))
                self._manager_context[key].set_text("")
            row.set_subtitle(escape_markup(subtitle))
            self._update_manager_button(
                key, (info.get("name") or key.title()), available, count
            )

        stores = data.get("_stores", {})
        for key, row in self._store_rows.items():
            info = stores.get(key, {})
            count = int(info.get("count", 0) or 0)
            row.set_subtitle(escape_markup(info.get("path") or "—"))
            self._store_badges[key].set_text(
                ngettext("%d app", "%d apps", count) % count
            )

        for key, row in self._count_rows.items():
            info = data.get(key, {})
            count = int(info.get("count", 0) or 0)
            name = info.get("name", key)
            if not info.get("available", True):
                row.set_subtitle(escape_markup(_("%s is not installed") % name))
            elif key == "brew":
                row.set_subtitle(
                    escape_markup(
                        _("%d formulae installed with %s") % (count, "Homebrew")
                    )
                )
            elif key == "apt":
                row.set_subtitle(
                    escape_markup(
                        _("%d Debian packages in the %s database") % (count, "dpkg")
                    )
                )
            else:
                row.set_subtitle(
                    escape_markup(_("%d applications known to %s") % (count, name))
                )
            self._count_badges[key].set_text(str(count))

        return GLib.SOURCE_REMOVE

    # --------------------------------------------------------- manager actions
    def _update_manager_button(
        self, key: str, name: str, available: bool, count: int
    ) -> None:
        button = self._manager_buttons.get(key)
        if button is None:
            return
        button._manager_name = name  # type: ignore[attr-defined]
        button._manager_available = available  # type: ignore[attr-defined]
        button._manager_count = count  # type: ignore[attr-defined]
        if available:
            button.set_label(_("Remove"))
            allowed, reason = managerops.can_remove(key, count)
            button.set_sensitive(allowed)
            button.set_tooltip_text(reason if not allowed else _("Remove %s") % name)
        else:
            button.set_label(_("Install"))
            allowed, reason = managerops.can_install(key)
            button.set_sensitive(True)
            button.set_tooltip_text("" if allowed else reason)

    def _manager_root(self) -> Gtk.Widget:
        return self.get_root() or self

    def _on_manager_action(self, key: str) -> None:
        button = self._manager_buttons.get(key)
        if button is None:
            return
        name = getattr(button, "_manager_name", key.title())
        count = int(getattr(button, "_manager_count", 0))
        if getattr(button, "_manager_available", False):
            self._remove_manager(key, name, count)
        else:
            self._install_manager(key, name)

    def _install_manager(self, key: str, name: str) -> None:
        allowed, reason = managerops.can_install(key)
        if not allowed:
            self._present_instructions(
                _("Install %s?") % name,
                _("This action is performed manually. Run the command below in a terminal."),
                reason,
            )
            return
        confirm(
            self._manager_root(),
            _("Install %s?") % name,
            _("This uses apt and asks for authentication."),
            confirm_label=_("Install"),
            on_confirm=lambda: self._run_manager_action("install", key, name),
        )

    def _remove_manager(self, key: str, name: str, count: int) -> None:
        allowed, reason = managerops.can_remove(key, count)
        if not allowed:
            if key in ("brew", "apt"):
                self._present_instructions(
                    _("Remove %s?") % name,
                    _("This action is performed manually. Run the command below in a terminal."),
                    reason,
                )
            else:
                show_toast(self, reason)
            return
        confirm(
            self._manager_root(),
            _("Remove %s?") % name,
            _("No packages from this manager remain."),
            confirm_label=_("Remove"),
            destructive=True,
            on_confirm=lambda: self._run_manager_action("remove", key, name),
        )

    def _present_instructions(self, heading: str, body: str, text: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", _("Close"))
        dialog.set_close_response("close")
        dialog.set_default_response("close")
        command = Gtk.Label(label=text)
        command.set_selectable(True)
        command.set_monospace(True)
        command.set_wrap(True)
        command.set_xalign(0.0)
        dialog.set_extra_child(command)
        dialog.present(self.get_root())

    def _run_manager_action(self, mode: str, key: str, name: str) -> None:
        title = (
            _("Installing %s") % name if mode == "install" else _("Removing %s") % name
        )

        def worker(progress, cancel) -> None:
            if mode == "install":
                managerops.install(key, progress=progress, cancel=cancel)
            else:
                managerops.remove(key, progress=progress, cancel=cancel)

        dialog = OperationDialog(
            title,
            worker,
            on_success=lambda _result: self._manager_action_done(mode, name),
            on_error=self._manager_action_failed,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _manager_action_done(self, mode: str, name: str) -> None:
        message = (
            _("%s installed") % name if mode == "install" else _("%s removed") % name
        )
        show_toast(self, message)
        self.refresh()

    def _manager_action_failed(self, exc: BaseException) -> None:
        show_toast(self, str(exc) or exc.__class__.__name__)
