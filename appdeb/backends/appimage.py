"""AppImage backend: metadata probing, managed install, replace and removal."""

from __future__ import annotations

import hashlib
import mmap
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from .. import config, db, desktop, icons  # noqa: E402
from ..i18n import _  # noqa: E402
from ..models import InstalledApp  # noqa: E402
from . import runner  # noqa: E402
from .runner import BackendError  # noqa: E402

GROUP = "Desktop Entry"
SQUASHFS_MAGIC = b"hsqs"
MAX_HITS = 30
CACHE_ICON_DIR = "appimage-icons"


@dataclass
class AppImageMetadata:
    name: str = ""
    comment: str = ""
    categories: str = ""
    icon: str = ""
    wm_class: str = ""
    version: str = ""
    desktop_id: str = ""


def _get(kf: GLib.KeyFile, key: str) -> str:
    try:
        return kf.get_string(GROUP, key) or ""
    except GLib.Error:
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]
    return value or "appimage"


def _unique_slug(name: str) -> str:
    base = _slug(name)
    candidate = base
    counter = 2
    while True:
        store = config.appimage_store() / candidate
        desktop_path = config.user_applications_dir() / f"appdeb-{candidate}.desktop"
        if not store.exists() and not desktop_path.exists():
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


def _sanitize_categories(text: str) -> str:
    tokens: list[str] = []
    for token in (text or "").replace(",", ";").split(";"):
        token = token.strip()
        if not token or not re.fullmatch(r"[A-Za-z0-9+.-]+", token):
            continue
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        tokens = ["Utility"]
    return ";".join(tokens) + ";"


def _exec_value(path: Path) -> str:
    text = str(path)
    if any(character.isspace() for character in text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise BackendError(_("cancelled"))


def is_appimage(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            data = handle.read(16)
    except OSError:
        return False
    return data.startswith(b"\x7fELF") and data[8:11] in (b"AI\x01", b"AI\x02")


def find_squashfs_offset(path: Path) -> int | None:
    path = Path(path)
    try:
        with open(path, "rb") as handle:
            mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
    except (OSError, ValueError):
        return None
    try:
        position = 0
        hits = 0
        while hits < MAX_HITS:
            index = mapped.find(SQUASHFS_MAGIC, position)
            if index < 0:
                break
            hits += 1
            position = index + 1
            try:
                result = runner.run(
                    ["unsquashfs", "-o", str(index), "-s", str(path)],
                    timeout=10,
                )
            except BackendError:
                continue
            if result.ok and "Found a valid SQUASHFS" in result.output:
                return index
        return None
    finally:
        mapped.close()


def _top_level_entries(output: str) -> list[str]:
    prefix = "squashfs-root/"
    names: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix) :]
        if not rest or "/" in rest or rest in names:
            continue
        names.append(rest)
    return names


def _listing(source: Path, offset: int) -> str:
    """Return the complete unsquashfs listing.

    ``runner.run`` keeps only the last 200 lines, which is far too little for a
    real bundle, so this read-only probe uses subprocess directly (no shell).
    """
    try:
        completed = subprocess.run(
            ["unsquashfs", "-o", str(offset), "-l", str(source)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout + "\n" + completed.stderr


def _extract_files(
    source: Path,
    offset: int,
    destination: Path,
    entries: list[str],
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    if not entries:
        return
    args = [
        "unsquashfs",
        "-o",
        str(offset),
        "-d",
        str(destination),
        "-no-progress",
        "-q",
        str(source),
        *entries,
    ]
    try:
        runner.run(args, timeout=120, progress=progress, cancel=cancel)
    except BackendError:
        # A missing optional entry must not abort metadata extraction; the caller
        # verifies every file it actually needs.
        return


def _cache_icon(tmp_icon: Path, source: Path) -> Path | None:
    try:
        stat = source.stat()
        key = hashlib.sha1(
            f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:16]
        cache = config.cache_home() / "appdeb" / CACHE_ICON_DIR
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"{key}{tmp_icon.suffix.lower()}"
        if not target.exists():
            shutil.copyfile(tmp_icon, target)
        return target
    except OSError:
        return None


def _extract_metadata(source: Path, *, keep_icon: bool = False) -> AppImageMetadata | None:
    source = Path(source)
    if not is_appimage(source):
        return None
    if shutil.which("unsquashfs") is None:
        return None
    offset = find_squashfs_offset(source)
    if offset is None:
        return None
    names = _top_level_entries(_listing(source, offset))
    if not names:
        return None
    desktops = sorted(name for name in names if name.lower().endswith(".desktop"))
    if not desktops:
        return None
    desktop_name = desktops[0]

    with tempfile.TemporaryDirectory(prefix="appdeb-appimage-") as raw_tmp:
        tmp = Path(raw_tmp)
        to_extract = [desktop_name]
        has_dir_icon = ".DirIcon" in names
        if has_dir_icon:
            to_extract.append(".DirIcon")
        _extract_files(source, offset, tmp, to_extract)
        desktop_file = tmp / desktop_name
        if not desktop_file.is_file():
            return None

        kf = desktop.load_keyfile(desktop_file)
        name = _get(kf, "Name") or Path(desktop_name).stem
        if not name:
            return None
        icon_value = _get(kf, "Icon")

        icon_file: Path | None = None
        if has_dir_icon:
            dir_icon = tmp / ".DirIcon"
            target_name = ""
            if dir_icon.is_symlink():
                target_name = os.path.basename(os.readlink(dir_icon))
            elif dir_icon.is_file():
                icon_file = dir_icon
            if target_name and target_name in names:
                target_path = tmp / target_name
                if not target_path.exists():
                    _extract_files(source, offset, tmp, [target_name])
                if target_path.exists():
                    icon_file = target_path
            elif target_name:
                candidate = dir_icon.parent / target_name
                if candidate.exists():
                    icon_file = candidate

        if icon_file is None:
            image_names = sorted(
                item for item in names if item.lower().endswith((".png", ".svg"))
            )
            wanted = Path(icon_value).stem if icon_value else ""
            chosen = ""
            if wanted:
                for item in image_names:
                    if Path(item).stem == wanted:
                        chosen = item
                        break
            if not chosen and image_names:
                chosen = image_names[0]
            if chosen:
                candidate = tmp / chosen
                if not candidate.exists():
                    _extract_files(source, offset, tmp, [chosen])
                if candidate.exists():
                    icon_file = candidate

        cached: Path | None = None
        if keep_icon and icon_file is not None:
            cached = _cache_icon(icon_file, source)

        metadata = AppImageMetadata(
            name=name,
            comment=_get(kf, "Comment"),
            categories=_get(kf, "Categories"),
            icon=str(cached) if cached is not None else "",
            wm_class=_get(kf, "StartupWMClass"),
            version=_get(kf, "X-AppImage-Version") or _get(kf, "Version"),
            desktop_id=Path(desktop_name).stem,
        )
        return metadata


def extract_metadata(path: Path) -> AppImageMetadata | None:
    return _extract_metadata(Path(path), keep_icon=True)


def _write_desktop(
    path: Path,
    *,
    name: str,
    comment: str,
    exec_value: str,
    icon: str,
    categories: str,
    wm_class: str,
    version: str,
    source_name: str,
    install_path: Path,
) -> None:
    kf = GLib.KeyFile()
    kf.set_string(GROUP, "Type", "Application")
    kf.set_string(GROUP, "Version", "1.5")
    kf.set_string(GROUP, "Name", name)
    if comment:
        kf.set_string(GROUP, "Comment", comment)
    kf.set_string(GROUP, "Exec", exec_value)
    if icon:
        kf.set_string(GROUP, "Icon", icon)
    kf.set_string(GROUP, "Terminal", "false")
    kf.set_string(GROUP, "Categories", _sanitize_categories(categories))
    kf.set_string(GROUP, "StartupNotify", "true")
    if wm_class:
        kf.set_string(GROUP, "StartupWMClass", wm_class)
    kf.set_string(GROUP, "X-Appdeb-Managed", "true")
    kf.set_string(GROUP, "X-Appdeb-Kind", "appimage")
    kf.set_string(GROUP, "X-Appdeb-Source", source_name)
    kf.set_string(GROUP, "X-Appdeb-InstallPath", str(install_path))
    if version:
        kf.set_string(GROUP, "X-Appdeb-Version", version)
    desktop.save_keyfile(kf, path)


def _installed(
    desktop_path: Path,
    *,
    desktop_id: str,
    bundle: Path,
    source: Path,
    version: str,
) -> InstalledApp:
    entry = desktop.load(desktop_path)
    if entry is None:
        raise BackendError(_("failed to write desktop entry"), str(desktop_path))
    return InstalledApp(
        entry=entry,
        kind="appimage",
        version=version,
        size_bytes=bundle.stat().st_size if bundle.exists() else 0,
        scope="user",
        source_ref=str(source),
        install_path=bundle,
        db=db.get(desktop_id) or {},
    )


def install(
    source: Path,
    name: str,
    icon_source: Path | None,
    wm_class: str,
    comment: str,
    categories: str,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> InstalledApp:
    source = Path(source)
    if not is_appimage(source):
        raise BackendError(_("not an AppImage: %s") % source)
    _check_cancel(cancel)
    config.ensure_dirs()
    db.init()

    metadata = _extract_metadata(source, keep_icon=icon_source is None)
    version = metadata.version if metadata else ""
    if not wm_class and metadata:
        wm_class = metadata.wm_class
    if not comment and metadata:
        comment = metadata.comment
    if not categories and metadata:
        categories = metadata.categories
    if icon_source is None and metadata and metadata.icon:
        icon_source = Path(metadata.icon)

    display_name = (name or "").strip() or (metadata.name if metadata else "") or "AppImage"
    slug = _unique_slug(display_name)
    store_dir = config.appimage_store() / slug
    store_dir.mkdir(parents=True, exist_ok=False)
    bundle = store_dir / f"{slug}.AppImage"
    try:
        _check_cancel(cancel)
        shutil.copy2(source, bundle)
        os.chmod(bundle, 0o755)
    except BaseException:
        shutil.rmtree(store_dir, ignore_errors=True)
        raise

    desktop_id = f"appdeb-{slug}.desktop"
    desktop_path = config.user_applications_dir() / desktop_id
    icon_name = ""
    if icon_source is not None:
        icon_name = icons.install_icon(Path(icon_source), desktop_id)

    try:
        _write_desktop(
            desktop_path,
            name=display_name,
            comment=comment,
            exec_value=_exec_value(bundle),
            icon=icon_name or "application-x-executable",
            categories=categories,
            wm_class=wm_class,
            version=version,
            source_name=source.name,
            install_path=bundle,
        )
    except BaseException:
        shutil.rmtree(store_dir, ignore_errors=True)
        raise

    db.upsert(
        desktop_id,
        kind="appimage",
        display_name=display_name,
        icon_name=icon_name or None,
        version=version,
        source_ref=str(source),
        install_path=str(bundle),
        install_date=_now(),
    )
    desktop.update_desktop_database()
    if progress is not None:
        try:
            progress(_("installed %s") % display_name)
        except Exception:  # noqa: BLE001
            pass
    return _installed(
        desktop_path,
        desktop_id=desktop_id,
        bundle=bundle,
        source=source,
        version=version,
    )


def _bundle_for(app: InstalledApp) -> Path | None:
    candidates: list[Path] = []
    if app.install_path:
        candidates.append(Path(app.install_path))
    row = app.db or db.get(app.desktop_id) or {}
    if row.get("install_path"):
        candidates.append(Path(row["install_path"]))
    kf = desktop.load_keyfile(app.entry.path)
    install_path = _get(kf, "X-Appdeb-InstallPath")
    if install_path:
        candidates.append(Path(install_path))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def replace(app: InstalledApp, new_source: Path) -> InstalledApp:
    new_source = Path(new_source)
    if not is_appimage(new_source):
        raise BackendError(_("not an AppImage: %s") % new_source)
    bundle = _bundle_for(app)
    if bundle is None or not bundle.exists():
        raise BackendError(_("installed bundle not found"))
    backup = Path(str(bundle) + f".bak-{_timestamp()}")
    shutil.copy2(bundle, backup)
    shutil.copy2(new_source, bundle)
    os.chmod(bundle, 0o755)
    metadata = _extract_metadata(new_source, keep_icon=False)
    version = metadata.version if metadata else ""
    if version:
        entry = desktop.set_string(app.entry, "X-Appdeb-Version", version)
    else:
        entry = app.entry
    db.upsert(
        app.desktop_id,
        version=version,
        source_ref=str(new_source),
        install_path=str(bundle),
    )
    fresh = InstalledApp(
        entry=entry,
        kind="appimage",
        version=version,
        size_bytes=bundle.stat().st_size if bundle.exists() else 0,
        scope=app.scope or "user",
        source_ref=str(new_source),
        install_path=bundle,
        db=db.get(app.desktop_id) or {},
    )
    return fresh


def uninstall(app: InstalledApp) -> None:
    config.ensure_dirs()
    db.init()
    desktop_id = app.desktop_id
    entry = app.entry
    icon_name = entry.icon
    row = app.db or db.get(desktop_id) or {}
    if not icon_name:
        icon_name = row.get("icon_name") or ""
    if icon_name.startswith("appdeb-"):
        icons.remove_icon(icon_name)

    bundle = _bundle_for(app)
    store_root = config.appimage_store()
    try:
        resolved_store = store_root.resolve()
    except OSError:
        resolved_store = store_root
    if bundle is not None:
        parent = bundle.parent
        try:
            resolved_parent = parent.resolve()
        except OSError:
            resolved_parent = parent
        try:
            resolved_parent.relative_to(resolved_store)
            inside_store = resolved_parent != resolved_store
        except (OSError, ValueError):
            inside_store = False
        if inside_store:
            shutil.rmtree(parent, ignore_errors=True)

    try:
        desktop_path = Path(entry.path)
        resolved_entry = desktop_path.resolve()
        resolved_user = config.user_applications_dir().resolve()
        resolved_entry.relative_to(resolved_user)
        if desktop_path.exists():
            desktop_path.unlink()
    except (OSError, ValueError):
        pass

    db.delete(desktop_id)
    desktop.update_desktop_database()
