"""Filesystem locations, resolved lazily so XDG overrides work in tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _xdg(name: str, fallback: Path) -> Path:
    raw = os.environ.get(name)
    if raw:
        return Path(raw).expanduser()
    return fallback


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share")


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")


def cache_home() -> Path:
    return _xdg("XDG_CACHE_HOME", Path.home() / ".cache")


def app_data_dir() -> Path:
    return data_home() / "appdeb"


def db_path() -> Path:
    return app_data_dir() / "appdeb.db"


def appimage_store() -> Path:
    return app_data_dir() / "appimages"


def tarball_store() -> Path:
    return app_data_dir() / "tarballs"


def backup_dir() -> Path:
    return app_data_dir() / "backups"


def download_dir() -> Path:
    return cache_home() / "appdeb" / "downloads"


def user_applications_dir() -> Path:
    return data_home() / "applications"


def user_icon_dir() -> Path:
    return data_home() / "icons" / "hicolor"


def autostart_dir() -> Path:
    return config_home() / "autostart"


def desktop_dir() -> Path:
    try:
        out = subprocess.run(
            ["xdg-user-dir", "DESKTOP"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        value = out.stdout.strip()
        if value:
            return Path(value)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Desktop"


def ensure_dirs() -> None:
    for path in (
        app_data_dir(),
        appimage_store(),
        tarball_store(),
        backup_dir(),
        download_dir(),
        user_applications_dir(),
        autostart_dir(),
    ):
        path.mkdir(parents=True, exist_ok=True)
