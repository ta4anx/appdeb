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

from .. import apps, config  # noqa: E402
from .widgets import badge, escape_markup  # noqa: E402

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
        self.set_title("Managers")
        self._token: object | None = None
        self._manager_rows: dict[str, Adw.ActionRow] = {}
        self._manager_badges: dict[str, Gtk.Label] = {}
        self._manager_context: dict[str, Gtk.Label] = {}
        self._count_rows: dict[str, Adw.ActionRow] = {}
        self._count_badges: dict[str, Gtk.Label] = {}
        self._store_rows: dict[str, Adw.ActionRow] = {}
        self._store_badges: dict[str, Gtk.Label] = {}

        self._title = Adw.WindowTitle(title="Managers", subtitle="Package manager overview")
        header = Adw.HeaderBar()
        header.set_title_widget(self._title)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh package manager information")
        refresh.connect("clicked", lambda _b: self.refresh())
        header.pack_end(refresh)

        page = Adw.PreferencesPage()

        manager_group = Adw.PreferencesGroup(
            title="Package managers",
            description="Package managers available on this system and their versions.",
        )
        for key, title, binary in (
            ("flatpak", "Flatpak", "flatpak"),
            ("snap", "Snap", "snap"),
            ("brew", "Homebrew", "brew"),
            ("apt", "APT / dpkg", "dpkg"),
        ):
            row = Adw.ActionRow(title=title, subtitle="Checking…")
            row.set_subtitle_lines(2)
            context = Gtk.Label(label=binary)
            context.add_css_class("dim-label")
            row.add_suffix(context)
            status = badge("Checking")
            row.add_suffix(status)
            manager_group.add(row)
            self._manager_rows[key] = row
            self._manager_badges[key] = status
            self._manager_context[key] = context
        page.add(manager_group)

        store_group = Adw.PreferencesGroup(
            title="Managed stores",
            description="Applications installed and owned by AppDeb.",
        )
        for key, title in (
            ("appimage", "AppImage store"),
            ("tarball", "Tarball store"),
        ):
            row = Adw.ActionRow(title=title, subtitle="—")
            row.set_subtitle_selectable(True)
            count = badge("0 apps")
            row.add_suffix(count)
            store_group.add(row)
            self._store_rows[key] = row
            self._store_badges[key] = count
        page.add(store_group)

        counts_group = Adw.PreferencesGroup(
            title="Installed by manager",
            description="Applications found through each package manager.",
        )
        for key, title in (
            ("flatpak", "Flatpak applications"),
            ("snap", "Snap applications"),
            ("brew", "Homebrew formulae"),
            ("apt", "Debian packages"),
        ):
            row = Adw.ActionRow(title=title, subtitle="Scanning…")
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
                row.set_subtitle("Package manager information is unavailable")
            return GLib.SOURCE_REMOVE

        for key, row in self._manager_rows.items():
            info = data.get(key, {})
            available = bool(info.get("available"))
            version = (info.get("version") or "").strip()
            path = (info.get("path") or "").strip()
            count = int(info.get("count", 0) or 0)
            unit = "apps" if key in ("flatpak", "snap") else "packages"
            if available:
                subtitle = version or "Version unknown"
                subtitle = f"{subtitle} · {count} {unit}"
                if path:
                    subtitle = f"{subtitle} · {path}"
                self._manager_badges[key].set_text("Available")
                self._manager_context[key].set_text("")
            else:
                subtitle = "Not installed"
                if version:
                    subtitle = f"{subtitle} · {version}"
                if path:
                    subtitle = f"{subtitle} · {path}"
                self._manager_badges[key].set_text("Not installed")
                self._manager_context[key].set_text("")
            row.set_subtitle(escape_markup(subtitle))

        stores = data.get("_stores", {})
        for key, row in self._store_rows.items():
            info = stores.get(key, {})
            count = int(info.get("count", 0) or 0)
            row.set_subtitle(escape_markup(info.get("path") or "—"))
            self._store_badges[key].set_text(f"{count} app" + ("" if count == 1 else "s"))

        for key, row in self._count_rows.items():
            info = data.get(key, {})
            count = int(info.get("count", 0) or 0)
            name = info.get("name", key)
            if not info.get("available", True):
                row.set_subtitle(escape_markup(f"{name} is not installed"))
            elif key == "brew":
                row.set_subtitle(escape_markup(f"{count} formulae installed with Homebrew"))
            elif key == "apt":
                row.set_subtitle(escape_markup(f"{count} Debian packages in the dpkg database"))
            else:
                row.set_subtitle(escape_markup(f"{count} applications known to {name}"))
            self._count_badges[key].set_text(str(count))

        return GLib.SOURCE_REMOVE
