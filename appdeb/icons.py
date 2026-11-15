"""User hicolor icon theme import and icon resolution."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

from . import config  # noqa: E402

FALLBACK_ICON = "application-x-executable"

MINIMAL_INDEX = """[Icon Theme]
Name=Hicolor
Comment=Fallback icon theme
Hidden=true
Directories=16x16/apps,24x24/apps,32x32/apps,48x48/apps,64x64/apps,128x128/apps,256x256/apps,scalable/apps

[16x16/apps]
Size=16
Context=Applications
Type=Fixed

[24x24/apps]
Size=24
Context=Applications
Type=Fixed

[32x32/apps]
Size=32
Context=Applications
Type=Fixed

[48x48/apps]
Size=48
Context=Applications
Type=Fixed

[64x64/apps]
Size=64
Context=Applications
Type=Fixed

[128x128/apps]
Size=128
Context=Applications
Type=Fixed

[256x256/apps]
Size=256
Context=Applications
Type=Fixed

[scalable/apps]
Size=256
Context=Applications
MinSize=16
MaxSize=512
Type=Scalable
"""


def ensure_hicolor() -> Path:
    root = config.user_icon_dir()
    for size in ("256x256", "scalable"):
        (root / size / "apps").mkdir(parents=True, exist_ok=True)
    index = root / "index.theme"
    if not index.exists():
        system_index = Path("/usr/share/icons/hicolor/index.theme")
        if system_index.exists():
            shutil.copyfile(system_index, index)
        else:
            index.write_text(MINIMAL_INDEX, encoding="utf-8")
    return root


def icon_name_for(desktop_id: str) -> str:
    return "appdeb-" + hashlib.sha1(desktop_id.encode("utf-8")).hexdigest()[:12]


def install_icon(source: Path, desktop_id: str) -> str:
    source = Path(source)
    if not source.is_file():
        raise ValueError(f"Unsupported image: {source}")
    root = ensure_hicolor()
    name = icon_name_for(desktop_id)
    suffix = source.suffix.lower()

    if suffix == ".svg":
        target = root / "scalable" / "apps" / f"{name}.svg"
        shutil.copyfile(source, target)
        rebuild_cache()
        return name

    try:
        from PIL import Image

        with Image.open(source) as image:
            converted = image.convert("RGBA")
            converted.thumbnail((256, 256), Image.LANCZOS)
            target = root / "256x256" / "apps" / f"{name}.png"
            converted.save(target, format="PNG")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Unsupported image: {source}") from exc
    rebuild_cache()
    return name


def remove_icon(icon_name: str) -> None:
    if not icon_name or not icon_name.startswith("appdeb-"):
        return
    root = config.user_icon_dir()
    for path in (
        root / "256x256" / "apps" / f"{icon_name}.png",
        root / "scalable" / "apps" / f"{icon_name}.svg",
    ):
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    rebuild_cache()


def resolve(icon_value: str) -> Gio.Icon:
    value = (icon_value or "").strip()
    if not value:
        return Gio.ThemedIcon.new(FALLBACK_ICON)
    candidate = Path(value)
    if candidate.is_absolute():
        if candidate.exists():
            return Gio.FileIcon.new(Gio.File.new_for_path(str(candidate)))
        return Gio.ThemedIcon.new(FALLBACK_ICON)
    return Gio.ThemedIcon.new(value)


def rebuild_cache() -> bool:
    root = config.user_icon_dir()
    if not root.exists():
        return False
    if not shutil.which("gtk-update-icon-cache"):
        return False
    try:
        proc = subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", str(root)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
