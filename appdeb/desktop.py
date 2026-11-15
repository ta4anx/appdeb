"""Desktop entry parsing, scanning, and user-scoped overrides."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import config, db  # noqa: E402
from .i18n import _  # noqa: E402
from .models import DesktopEntry  # noqa: E402

GROUP = "Desktop Entry"
ORIGINAL_NAME_KEY = "X-Appdeb-OriginalName"
ORIGINAL_ICON_KEY = "X-Appdeb-OriginalIcon"
ORIGINAL_WM_KEY = "X-Appdeb-OriginalWMClass"

_MISSING = (GLib.KeyFileError.GROUP_NOT_FOUND, GLib.KeyFileError.KEY_NOT_FOUND)


def _get(kf: GLib.KeyFile, key: str, group: str = GROUP) -> str:
    try:
        return kf.get_string(group, key) or ""
    except GLib.Error:
        return ""


def _get_bool(kf: GLib.KeyFile, key: str, group: str = GROUP) -> bool:
    try:
        return bool(kf.get_boolean(group, key))
    except GLib.Error:
        return False


def _has(kf: GLib.KeyFile, key: str, group: str = GROUP) -> bool:
    return key in _keys(kf, group)


def _keys(kf: GLib.KeyFile, group: str = GROUP) -> list[str]:
    try:
        return list(kf.get_keys(group)[0])
    except GLib.Error:
        return []


def _groups(kf: GLib.KeyFile) -> list[str]:
    try:
        return list(kf.get_groups()[0])
    except GLib.Error:
        return []


def split_groups(text: str) -> list[tuple[str, list[str]]]:
    """Split raw key-file text into (header, lines) blocks, dropping comments."""
    blocks: list[tuple[str, list[str]]] = []
    header: str | None = None
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if header is not None:
                blocks.append((header, lines))
            header = stripped[1:-1]
            lines = []
        elif header is not None:
            lines.append(raw)
    if header is not None:
        blocks.append((header, lines))
    return blocks


def keyfile_from_text(text: str) -> GLib.KeyFile:
    """Parse key-file text, keeping only the last [Desktop Entry] group."""
    blocks = split_groups(text)
    last = None
    for index, (header, _lines) in enumerate(blocks):
        if header == GROUP:
            last = index
    kf = GLib.KeyFile()
    if last is None:
        return kf
    body: list[str] = []
    for index, (header, lines) in enumerate(blocks):
        if header == GROUP and index != last:
            continue
        body.append(f"[{header}]")
        body.extend(lines)
    data = "\n".join(body) + "\n"
    try:
        kf.load_from_data(data, len(data.encode()))
    except GLib.Error:
        pass
    return kf


def load_keyfile(path: Path) -> GLib.KeyFile:
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        return kf
    except GLib.Error:
        pass
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return GLib.KeyFile()
    return keyfile_from_text(text)


def save_keyfile(kf: GLib.KeyFile, path: Path) -> None:
    data = kf.to_data()[0]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    os.chmod(path, 0o644)


def _entry_from_keyfile(kf: GLib.KeyFile, path: Path) -> DesktopEntry | None:
    if not _has(kf, "Type"):
        return None
    if _get(kf, "Type") != "Application":
        return None
    name = _get(kf, "Name") or path.stem
    categories = [c for c in _get(kf, "Categories").split(";") if c]
    hidden = _get_bool(kf, "Hidden")
    no_display = _get_bool(kf, "NoDisplay")

    display_name = name
    row = db.get(path.name)
    if row and row.get("display_name"):
        display_name = row["display_name"]

    return DesktopEntry(
        id=path.name,
        path=path,
        name=display_name,
        comment=_get(kf, "Comment"),
        exec_line=_get(kf, "Exec"),
        icon=_get(kf, "Icon"),
        terminal=_get_bool(kf, "Terminal"),
        categories=categories,
        startup_wm_class=_get(kf, "StartupWMClass"),
        no_display=no_display or hidden,
        source=source_for(path),
    )


def load(path: Path) -> DesktopEntry | None:
    path = Path(path)
    if not path.is_file():
        return None
    kf = load_keyfile(path)
    return _entry_from_keyfile(kf, path)


def application_dirs() -> list[Path]:
    dirs: list[Path] = [config.user_applications_dir()]
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    for item in data_dirs.split(":"):
        if item:
            dirs.append(Path(item) / "applications")
    dirs.extend(
        [
            config.data_home() / "flatpak" / "exports" / "share" / "applications",
            Path("/var/lib/flatpak/exports/share/applications"),
            Path("/var/lib/snapd/desktop/applications"),
        ]
    )
    seen: set[Path] = set()
    result: list[Path] = []
    for directory in dirs:
        resolved = directory.expanduser()
        try:
            key = resolved.resolve()
        except OSError:
            key = resolved
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    return result


def source_for(path: Path) -> str:
    path = Path(path)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    user_dir = config.user_applications_dir()
    try:
        resolved.relative_to(user_dir.resolve())
        return "user"
    except (OSError, ValueError):
        pass
    text = str(resolved)
    if "/flatpak/" in text:
        return "flatpak"
    if "/snapd/" in text:
        return "snap"
    return "system"


def scan(include_hidden: bool = False) -> list[DesktopEntry]:
    entries: list[DesktopEntry] = []
    seen: set[str] = set()
    for directory in application_dirs():
        try:
            children = sorted(directory.iterdir())
        except OSError:
            continue
        for child in children:
            if child.suffix != ".desktop" or not child.is_file():
                continue
            if child.name in seen:
                continue
            entry = load(child)
            if entry is None:
                continue
            seen.add(child.name)
            if entry.no_display and not include_hidden:
                continue
            entries.append(entry)
    return entries


def ensure_user_override(entry: DesktopEntry) -> Path:
    user_dir = config.user_applications_dir()
    source = Path(entry.path)
    target = user_dir / (entry.path.name if source.parent == user_dir else entry.id)
    if source.parent == user_dir:
        return source
    user_dir.mkdir(parents=True, exist_ok=True)
    kf = load_keyfile(source)
    if _groups(kf) == []:
        kf = GLib.KeyFile()
        kf.set_string(GROUP, "Type", "Application")
        kf.set_string(GROUP, "Name", entry.name)
        kf.set_string(GROUP, "Exec", entry.exec_line)
    save_keyfile(kf, target)
    row = db.get(entry.id) or {}
    updates = {
        "original_name": row.get("original_name") or _get(kf, "Name"),
        "original_icon": row.get("original_icon") or _get(kf, "Icon"),
        "original_wm_class": row.get("original_wm_class") or _get(kf, "StartupWMClass"),
    }
    db.ensure_row(entry.id, **updates)
    return target


def _refresh_entry(entry: DesktopEntry, path: Path) -> DesktopEntry:
    fresh = load(path)
    if fresh is None:
        return entry
    fresh.source = source_for(path)
    return fresh


def update_desktop_database(directory: Path | None = None) -> None:
    target = directory or config.user_applications_dir()
    if not shutil.which("update-desktop-database"):
        return
    try:
        subprocess.run(["update-desktop-database", str(target)], timeout=30, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def rename(entry: DesktopEntry, new_name: str) -> DesktopEntry:
    cleaned = " ".join(new_name.split())
    if not cleaned:
        raise ValueError(_("name is empty"))
    if "/" in cleaned or "\n" in cleaned:
        raise ValueError(_("name contains a forbidden character"))
    cleaned = cleaned[:80]

    path = ensure_user_override(entry)
    kf = load_keyfile(path)
    if not _has(kf, ORIGINAL_NAME_KEY):
        kf.set_string(GROUP, ORIGINAL_NAME_KEY, _get(kf, "Name") or entry.name)
    for key in _keys(kf):
        if key.startswith("Name["):
            try:
                kf.remove_key(GROUP, key)
            except GLib.Error:
                pass
    kf.set_string(GROUP, "Name", cleaned)
    save_keyfile(kf, path)

    row = db.get(entry.id) or {}
    db.upsert(
        entry.id,
        display_name=cleaned,
        original_name=row.get("original_name") or _get(kf, ORIGINAL_NAME_KEY),
    )
    update_desktop_database()
    return _refresh_entry(entry, path)


def set_string(entry: DesktopEntry, key: str, value: str) -> DesktopEntry:
    path = ensure_user_override(entry)
    kf = load_keyfile(path)
    if key == "Icon" and not _has(kf, ORIGINAL_ICON_KEY):
        kf.set_string(GROUP, ORIGINAL_ICON_KEY, _get(kf, "Icon"))
    if key == "StartupWMClass" and not _has(kf, ORIGINAL_WM_KEY):
        kf.set_string(GROUP, ORIGINAL_WM_KEY, _get(kf, "StartupWMClass"))
    kf.set_string(GROUP, key, value)
    save_keyfile(kf, path)
    if key == "Icon":
        db.upsert(entry.id, icon_name=value)
    elif key == "StartupWMClass":
        db.upsert(entry.id, notes=f"wm_class:{value}")
    fresh = _refresh_entry(entry, path)
    update_desktop_database()
    return fresh


def remove_key(entry: DesktopEntry, key: str) -> DesktopEntry:
    path = ensure_user_override(entry)
    kf = load_keyfile(path)
    if _has(kf, key):
        try:
            kf.remove_key(GROUP, key)
        except GLib.Error:
            pass
    save_keyfile(kf, path)
    fresh = _refresh_entry(entry, path)
    update_desktop_database()
    return fresh


def safe_shortcut_name(name: str) -> str:
    cleaned = re.sub(r"[/\\\x00-\x1f]", "", name).strip()
    cleaned = cleaned or _("Application")
    return cleaned[:120]


def shortcut_path(entry: DesktopEntry) -> Path:
    return config.desktop_dir() / f"{safe_shortcut_name(entry.name)}.desktop"


def create_shortcut(entry: DesktopEntry) -> Path:
    target_dir = config.desktop_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    base = safe_shortcut_name(entry.name)
    path = target_dir / f"{base}.desktop"
    counter = 2
    while path.exists():
        path = target_dir / f"{base} ({counter}).desktop"
        counter += 1
    shutil.copyfile(entry.path, path)
    os.chmod(path, 0o755)
    if shutil.which("gio"):
        try:
            subprocess.run(
                ["gio", "set", str(path), "metadata::trusted", "true"],
                timeout=10,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return path


def remove_shortcut(entry: DesktopEntry) -> bool:
    target_dir = config.desktop_dir()
    base = safe_shortcut_name(entry.name)
    candidates = [target_dir / f"{base}.desktop"]
    if target_dir.is_dir():
        candidates.extend(sorted(target_dir.glob(f"{base} (*).desktop")))
    removed = False
    for path in candidates:
        if path.exists():
            try:
                path.unlink()
                removed = True
            except OSError:
                pass
    return removed


def launch(entry: DesktopEntry) -> None:
    info = Gio.DesktopAppInfo.new_from_filename(str(entry.path))
    if info is not None:
        try:
            info.launch([], None)
            db.touch_launched(entry.id)
            return
        except GLib.Error:
            pass
    tokens = entry.exec_line.split()
    if tokens:
        candidate = Path(tokens[0])
        if candidate.is_absolute() and candidate.exists():
            subprocess.Popen([str(candidate)])
            db.touch_launched(entry.id)


def validate(path: Path) -> list[str]:
    if not shutil.which("desktop-file-validate"):
        return []
    try:
        proc = subprocess.run(
            ["desktop-file-validate", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [str(exc)]
    output = (proc.stdout or "") + (proc.stderr or "")
    return [line for line in output.splitlines() if line.strip()]


def duplicate_group_count(path: Path) -> int:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.strip() == "[Desktop Entry]")


def repair_duplicate_groups(path: Path) -> bool:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    blocks = split_groups(text)
    desktop_blocks = [b for b in blocks if b[0] == GROUP]
    if len(desktop_blocks) < 2:
        return False

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup = config.backup_dir() / f"desktop-repair-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(path, backup / path.name)
    except OSError:
        pass

    last_index = max(i for i, (header, _lines) in enumerate(blocks) if header == GROUP)
    lines_out: list[str] = []
    for index, (header, lines) in enumerate(blocks):
        if header == GROUP and index != last_index:
            continue
        lines_out.append(f"[{header}]")
        lines_out.extend(lines)
    path.write_text("\n".join(lines_out).rstrip("\n") + "\n", encoding="utf-8")
    os.chmod(path, 0o644)
    return True
