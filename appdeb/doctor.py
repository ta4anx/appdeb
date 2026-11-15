"""Desktop entry doctor: detects and repairs dock icon / entry problems."""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, Gtk  # noqa: E402

from . import config, db, desktop, icons  # noqa: E402
from .i18n import _  # noqa: E402
from .models import DesktopEntry, Issue  # noqa: E402


def _first_exec(exec_line: str) -> str:
    tokens = (exec_line or "").split()
    index = 0
    while index < len(tokens) and tokens[index] == "env":
        index += 1
        while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("/"):
            index += 1
    return tokens[index] if index < len(tokens) else ""


def normalize_app_id(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\.desktop$", "", text)
    return text.replace("_", "-")


def _x11_windows() -> list[tuple[str, str]]:
    """Return (window id, WM_CLASS first field) pairs from the X server."""
    if not shutil.which("xprop"):
        return []
    try:
        listing = subprocess.run(
            ["xprop", "-root", "_NET_CLIENT_LIST"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if listing.returncode != 0:
        return []
    ids = re.findall(r"0x[0-9a-fA-F]+", listing.stdout)
    windows: list[tuple[str, str]] = []
    for window_id in ids:
        try:
            info = subprocess.run(
                ["xprop", "-id", window_id, "WM_CLASS"], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.search(r'"([^"]+)"', info.stdout)
        if match:
            windows.append((window_id, match.group(1)))
    return windows


def _icon_theme():
    try:
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
    except (ValueError, ImportError):
        return None
    if display is None:
        return None
    return Gtk.IconTheme.get_for_display(display)


ICON_ROOTS = (
    Path("/usr/share/icons"),
    Path("/usr/local/share/icons"),
    Path("/usr/share/pixmaps"),
    Path("/var/lib/snapd/desktop/icons"),
    Path("/var/lib/flatpak/exports/share/icons"),
)


@functools.lru_cache(maxsize=4)
def _icon_index(extra_root: str) -> frozenset[str]:
    roots = [Path(extra_root), *ICON_ROOTS]
    names: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() in {".png", ".svg", ".xpm"} and path.is_file():
                names.add(path.stem)
    return frozenset(names)


def _icon_is_missing(icon_value: str) -> bool:
    if not icon_value:
        return True
    candidate = Path(icon_value)
    if candidate.is_absolute():
        return not candidate.exists()
    theme = _icon_theme()
    if theme is not None and theme.has_icon(icon_value):
        return False
    index = _icon_index(str(config.user_icon_dir()))
    if icon_value in index:
        return False
    return not any(stem.startswith(f"{icon_value}_") for stem in index)


def scan() -> list[Issue]:
    issues: list[Issue] = []
    entries = desktop.scan(include_hidden=True)
    windows = _x11_windows()
    normalized = [(wid, normalize_app_id(cls), cls) for wid, cls in windows]

    for entry in entries:
        path = Path(entry.path)
        problems = [line for line in desktop.validate(path) if ": error:" in line or "error:" in line]
        if problems:
            issues.append(
                Issue(
                    code="invalid-desktop",
                    path=str(path),
                    desktop_id=entry.id,
                    severity="error",
                    message=problems[0],
                    fixable=desktop.duplicate_group_count(path) > 1,
                )
            )
        if desktop.duplicate_group_count(path) > 1:
            issues.append(
                Issue(
                    code="duplicate-groups",
                    path=str(path),
                    desktop_id=entry.id,
                    severity="error",
                    message=_(
                        "desktop file has more than one [Desktop Entry] group"
                    ),
                    fixable=True,
                )
            )
        icon_value = entry.icon
        if _icon_is_missing(icon_value):
            issues.append(
                Issue(
                    code="missing-icon",
                    path=str(path),
                    desktop_id=entry.id,
                    severity="warning",
                    message=_("icon not found: %s") % (icon_value or _("(empty)")),
                    fixable=True,
                )
            )
        elif Path(icon_value).is_absolute() and entry.icon:
            issues.append(
                Issue(
                    code="absolute-icon",
                    path=str(path),
                    desktop_id=entry.id,
                    severity="info",
                    message=_("icon is an absolute path outside the icon theme"),
                    fixable=True,
                )
            )
        token = _first_exec(entry.exec_line)
        if token:
            candidate = Path(token)
            if candidate.is_absolute() and not candidate.exists():
                issues.append(
                    Issue(
                        code="missing-exec",
                        path=str(path),
                        desktop_id=entry.id,
                        severity="error",
                        message=_("executable not found: %s") % candidate,
                        fixable=False,
                    )
                )
        if not entry.startup_wm_class:
            candidates = {
                normalize_app_id(entry.id),
                normalize_app_id(entry.name),
                normalize_app_id(Path(_first_exec(entry.exec_line)).name),
            }
            candidates.discard("")
            for _wid, window_norm, raw_class in normalized:
                if window_norm and window_norm in candidates:
                    issues.append(
                        Issue(
                            code="no-wm-class",
                            path=str(path),
                            desktop_id=entry.id,
                            severity="warning",
                            message=_(
                                "window class %s is not associated with this entry"
                            )
                            % raw_class,
                            fixable=True,
                        )
                    )
                    break
    return issues


def backup_desktop_entries() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    target = config.backup_dir() / f"doctor-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    source = config.user_applications_dir()
    if source.is_dir():
        for path in sorted(source.glob("*.desktop")):
            try:
                shutil.copyfile(path, target / path.name)
            except OSError:
                pass
    db_file = config.db_path()
    if db_file.exists():
        try:
            shutil.copyfile(db_file, target / db_file.name)
        except OSError:
            pass
    return target


def _entry_for(issue: Issue) -> DesktopEntry | None:
    path = Path(issue.path)
    entry = desktop.load(path)
    if entry is None:
        # Broken file: build a minimal handle so repairs can proceed.
        entries = {e.id: e for e in desktop.scan(include_hidden=True)}
        entry = entries.get(issue.desktop_id)
    return entry


def fix(issue: Issue) -> bool:
    path = Path(issue.path)
    backup_desktop_entries()

    if issue.code == "duplicate-groups":
        changed = desktop.repair_duplicate_groups(path)
        if changed:
            desktop.update_desktop_database(path.parent)
        return changed

    entry = _entry_for(issue)
    if entry is None:
        return False

    if issue.code == "no-wm-class":
        windows = _x11_windows()
        target = None
        for _wid, cls in windows:
            if normalize_app_id(cls) == normalize_app_id(entry.id) or (
                entry.name and normalize_app_id(cls) == normalize_app_id(entry.name)
            ):
                target = cls
                break
        if target is None and windows:
            token = Path(_first_exec(entry.exec_line)).name
            for _wid, cls in windows:
                if token and normalize_app_id(cls) == normalize_app_id(token):
                    target = cls
                    break
        if not target:
            return False
        fresh = desktop.set_string(entry, "StartupWMClass", target)
        db.upsert(entry.id, notes=f"wm_class:{fresh.startup_wm_class}")
        desktop.update_desktop_database()
        return True

    if issue.code == "absolute-icon":
        value = entry.icon
        if not value or not Path(value).is_absolute():
            return False
        try:
            name = icons.install_icon(Path(value), entry.id)
        except ValueError:
            return False
        desktop.set_string(entry, "Icon", name)
        db.upsert(entry.id, icon_name=name)
        desktop.update_desktop_database()
        return True

    if issue.code == "invalid-desktop":
        return desktop.duplicate_group_count(path) > 1 and desktop.repair_duplicate_groups(path)

    return False
