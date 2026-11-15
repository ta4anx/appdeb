"""Installed application inventory: kind detection, sizes, file locations."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import config, db, desktop
from .models import DesktopEntry, InstalledApp

KIND_LABELS = {
    "flatpak": "Flatpak",
    "appimage": "AppImage",
    "tarball": "Tarball",
    "deb": "Deb",
    "snap": "Snap",
    "system": "System",
}

SIZE_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}


def parse_size(text: str) -> int | None:
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").replace("\u202f", " ").strip()
    match = re.match(r"^([0-9]+(?:[.,][0-9]+)?)\s*([A-Za-z]+)?$", cleaned)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "b").lower()
    factor = SIZE_UNITS.get(unit)
    if factor is None:
        return None
    return int(number * factor)


def human_size(value: int | None) -> str:
    if not value or value <= 0:
        return ""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1000
    return ""


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind.title() if kind else "System")


def _flatpak_rows() -> dict[str, dict]:
    from .backends import flatpak

    rows: dict[str, dict] = {}
    if not flatpak.available():
        return rows
    for app in flatpak.list_installed():
        rows[app.application] = {
            "version": app.version,
            "size": app.size_bytes,
            "scope": app.scope,
            "name": app.name,
            "origin": app.origin,
        }
    return rows


def _first_exec_token(exec_line: str) -> str:
    tokens = (exec_line or "").split()
    index = 0
    while index < len(tokens) and tokens[index] == "env":
        index += 1
        while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("/"):
            index += 1
    return tokens[index] if index < len(tokens) else ""


def _looks_appimage(exec_line: str) -> bool:
    text = exec_line or ""
    return ".AppImage" in text or "/.mount_" in text


def _detect_kind(entry: DesktopEntry, row: dict | None) -> str:
    kf = desktop.load_keyfile(entry.path)
    explicit = desktop._get(kf, "X-Appdeb-Kind")
    if explicit:
        return explicit
    if row and row.get("kind"):
        return row["kind"]
    if entry.source == "flatpak" or desktop._has(kf, "X-Flatpak"):
        return "flatpak"
    if entry.source == "snap":
        return "snap"
    if _looks_appimage(entry.exec_line):
        return "appimage"
    if row and row.get("install_path"):
        path = Path(row["install_path"])
        try:
            path.relative_to(config.tarball_store())
            return "tarball"
        except ValueError:
            if row and row.get("kind"):
                return row["kind"]
    return "system"


def _detect_scope(entry: DesktopEntry, kind: str, row: dict | None, flatpak_rows: dict[str, dict]) -> str:
    if row and row.get("scope"):
        return row["scope"]
    if kind == "flatpak":
        kf = desktop.load_keyfile(entry.path)
        app_id = desktop._get(kf, "X-Flatpak")
        if app_id and app_id in flatpak_rows:
            return flatpak_rows[app_id]["scope"]
        try:
            entry.path.resolve().relative_to(config.data_home().resolve())
            return "user"
        except (OSError, ValueError):
            return "system"
    try:
        Path(entry.path).resolve().relative_to(config.data_home().resolve())
        return "user"
    except (OSError, ValueError):
        return "system"


def _appimage_bundle(entry: DesktopEntry) -> Path | None:
    token = _first_exec_token(entry.exec_line)
    if not token:
        return None
    candidate = Path(token)
    if candidate.is_absolute() and candidate.exists() and candidate.suffix == ".AppImage":
        return candidate
    return None


def scan_installed(include_hidden: bool = False) -> list[InstalledApp]:
    rows = db.all_rows()
    flatpak_rows = _flatpak_rows()
    result: list[InstalledApp] = []
    for entry in desktop.scan(include_hidden=include_hidden):
        row = rows.get(entry.id)
        kind = _detect_kind(entry, row)
        kf = desktop.load_keyfile(entry.path)
        app_id = desktop._get(kf, "X-Flatpak")
        version = ""
        size = 0
        source_ref = ""
        install_path: Path | None = None

        if kind == "flatpak" and app_id and app_id in flatpak_rows:
            version = flatpak_rows[app_id]["version"]
            size = flatpak_rows[app_id]["size"]
            source_ref = app_id
        elif kind == "appimage":
            bundle = _appimage_bundle(entry)
            if bundle:
                install_path = bundle
                size = bundle.stat().st_size
            version = desktop._get(kf, "X-Appdeb-Version")
        elif kind == "tarball":
            version = desktop._get(kf, "X-Appdeb-Version")
            install_raw = desktop._get(kf, "X-Appdeb-InstallPath")
            if install_raw:
                install_path = Path(install_raw)
        else:
            version = desktop._get(kf, "X-Appdeb-Version")

        if row:
            version = row.get("version") or version
            if row.get("source_ref"):
                source_ref = row["source_ref"]
            if row.get("install_path"):
                install_path = Path(row["install_path"])
        if kind == "deb" or (not version and row and row.get("version")):
            version = (row or {}).get("version") or version

        result.append(
            InstalledApp(
                entry=entry,
                kind=kind,
                version=version or "",
                size_bytes=size or 0,
                scope=_detect_scope(entry, kind, row, flatpak_rows),
                source_ref=source_ref,
                install_path=install_path,
                db=row or {},
            )
        )
    return result


def get_app(desktop_id: str) -> InstalledApp | None:
    for app in scan_installed(include_hidden=True):
        if app.entry.id == desktop_id:
            return app
    return None


def candidate_files(app: InstalledApp) -> list[tuple[str, Path]]:
    seen: set[Path] = set()
    files: list[tuple[str, Path]] = []

    def add(label: str, path: Path | None) -> None:
        if path is None:
            return
        try:
            resolved = path.expanduser()
        except RuntimeError:
            return
        if resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        files.append((label, resolved))

    entry = app.entry
    add("Desktop file", Path(entry.path))

    token = _first_exec_token(entry.exec_line)
    if token:
        candidate = Path(token)
        if candidate.is_absolute() and candidate.exists():
            add("Executable", candidate)

    if app.install_path and app.install_path.exists():
        add("Install path", app.install_path)

    if app.kind == "flatpak":
        from .backends import flatpak

        app_id = app.source_ref or desktop._get(desktop.load_keyfile(entry.path), "X-Flatpak")
        if app_id:
            info = flatpak.info(app_id, app.scope)
            location = info.get("location")
            if location:
                add("Flatpak location", Path(location))
            add("Application data", Path.home() / ".var" / "app" / app_id)
            add("Configuration", config.config_home() / app_id)
            add("User data", config.data_home() / app_id)
            add("Cache", config.cache_home() / app_id)

    if app.kind == "tarball" and app.install_path and app.install_path.is_dir():
        for child in sorted(app.install_path.iterdir())[:3]:
            add(f"Inside install: {child.name}", child)

    name_variants = [
        app.display_name,
        app.display_name.replace(" ", ""),
        app.display_name.lower().replace(" ", "-"),
    ]
    for base in (config.config_home(), config.data_home(), config.cache_home()):
        for variant in name_variants:
            if variant:
                add("Matching folder", base / variant)

    return files


def executable_path(app: InstalledApp) -> Path | None:
    token = _first_exec_token(app.entry.exec_line)
    if not token:
        return None
    path = Path(token)
    if path.is_absolute() and path.exists():
        return path
    return None


def backend_binary_missing(app: InstalledApp) -> str | None:
    path = executable_path(app)
    if path and path.is_symlink() and not path.exists():
        return path.name
    if path is None and app.kind == "flatpak" and not shutil.which("flatpak"):
        return "flatpak"
    return None
