"""Autostart (XDG autostart) toggles for desktop entries."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from .. import config, desktop  # noqa: E402
from ..models import DesktopEntry  # noqa: E402


def target_path(entry: DesktopEntry) -> Path:
    return config.autostart_dir() / entry.id


def enabled(entry: DesktopEntry) -> bool:
    path = target_path(entry)
    if not path.is_file():
        return False
    kf = desktop.load_keyfile(path)
    try:
        if kf.get_boolean("Desktop Entry", "Hidden"):
            return False
    except GLib.Error:
        pass
    return True


def set_enabled(entry: DesktopEntry, enabled: bool) -> None:
    directory = config.autostart_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = target_path(entry)
    if not enabled:
        if target.exists():
            target.unlink()
        return
    source = Path(entry.path)
    override = config.user_applications_dir() / entry.id
    if override.is_file():
        source = override
    shutil.copyfile(source, target)
    kf = desktop.load_keyfile(target)
    kf.set_boolean("Desktop Entry", "Hidden", False)
    kf.set_boolean("Desktop Entry", "X-GNOME-Autostart-enabled", True)
    data = kf.to_data()[0]
    target.write_text(data, encoding="utf-8")
    os.chmod(target, 0o644)
