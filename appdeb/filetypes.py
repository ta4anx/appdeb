"""Single source of truth for which file types AppDeb can install."""

from __future__ import annotations

from pathlib import Path

from .i18n import _

TARBALL_SUFFIXES = (".tar.gz", ".tgz")
FLATPAK_SUFFIXES = (".flatpak", ".flatpakref")
DEB_SUFFIXES = (".deb",)
APPIMAGE_SUFFIXES = (".appimage",)

KINDS = ("tarball", "appimage", "deb", "flatpak")


def _suffix_kind(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(TARBALL_SUFFIXES):
        return "tarball"
    if name.endswith(FLATPAK_SUFFIXES):
        return "flatpak"
    if name.endswith(DEB_SUFFIXES):
        return "deb"
    if name.endswith(APPIMAGE_SUFFIXES):
        return "appimage"
    return None


def kind(path: str | Path) -> str | None:
    """Return the install workflow for ``path``, or ``None`` when unsupported.

    Content sniffing is only used for formats where the extension is not
    decisive (``.tar.gz`` must really be a gzip tar, ``.AppImage`` must carry
    the AppImage magic); everything else is routed by extension.
    """
    path = Path(path)
    guessed = _suffix_kind(path)
    if guessed == "tarball":
        try:
            from .backends import tarball

            return "tarball" if tarball.is_tarball(path) else None
        except Exception:  # noqa: BLE001 - unreadable archive means unsupported
            return None
    if guessed == "appimage":
        try:
            from .backends import appimage

            return "appimage" if appimage.is_appimage(path) else None
        except Exception:  # noqa: BLE001
            return None
    return guessed


def supported_suffixes() -> tuple[str, ...]:
    """Canonical display suffixes for the supported install workflows."""
    return (".AppImage", ".deb", ".flatpak", ".flatpakref", ".tar.gz", ".tgz")


def label(kind_name: str | None) -> str:
    """Human-readable name of an install workflow."""
    return {
        "tarball": _("Tarball"),
        "appimage": _("AppImage"),
        "deb": _("Debian package"),
        "flatpak": _("Flatpak bundle"),
    }.get(kind_name or "", _("Unsupported file type"))
