"""Install or remove whole package managers (Flatpak, Snap, Homebrew, APT).

APIs used by the Managers page. System managers go through polkit; Homebrew is
never touched automatically because its install location and shell profile are
outside the package database.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backends import runner
from .i18n import ngettext

APT_PACKAGES = {"flatpak": "flatpak", "snap": "snapd"}
PACKAGE_MANAGERS = ("flatpak", "snap", "brew", "apt")

BREW_REMOVE_COMMAND = "/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/uninstall.sh)\""
BREW_INSTALL_COMMAND = (
    '/bin/bash -c "$(curl -fsSL '
    'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
)


@dataclass(slots=True)
class ManagerState:
    kind: str
    name: str
    available: bool
    version: str = ""
    path: str = ""
    count: int = 0


def _message(kind: str, count: int) -> str:
    return ngettext(
        "%(name)s manages %(count)d package; remove it first.",
        "%(name)s manages %(count)d packages; remove it first.",
        count,
    ) % {"name": kind.title(), "count": count}


def can_remove(kind: str, count: int) -> tuple[bool, str]:
    """Return whether the manager may be removed right now, and why not."""
    if kind == "apt":
        return False, "APT and dpkg are part of the base system and cannot be removed here."
    if kind == "brew":
        return (
            False,
            "Homebrew is removed with its official script:\n" + BREW_REMOVE_COMMAND,
        )
    if count > 0:
        return False, _message(kind, count)
    return True, ""


def can_install(kind: str) -> tuple[bool, str]:
    if kind == "brew":
        return (
            False,
            "Install Homebrew from its official script:\n" + BREW_INSTALL_COMMAND,
        )
    if kind == "apt":
        return False, "APT and dpkg are part of the base system."
    return True, ""


def install(kind: str, progress=None, cancel=None) -> None:
    """Install a missing manager through apt (polkit prompt)."""
    allowed, reason = can_install(kind)
    if not allowed:
        raise runner.BackendError(reason)
    package = APT_PACKAGES.get(kind)
    if package is None:
        raise runner.BackendError(f"unknown package manager: {kind}")
    result = runner.run(
        [
            "pkexec",
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "install",
            "-y",
            package,
        ],
        timeout=1800,
        progress=progress,
        cancel=cancel,
    )
    if not result.ok:
        raise runner.BackendError(f"installing {package} failed", result.output)


def remove(kind: str, progress=None, cancel=None) -> None:
    """Remove an unused manager through apt (polkit prompt)."""
    allowed, reason = can_remove(kind, _installed_count(kind))
    if not allowed:
        raise runner.BackendError(reason)
    package = APT_PACKAGES.get(kind)
    if package is None:
        raise runner.BackendError(f"unknown package manager: {kind}")
    result = runner.run(
        [
            "pkexec",
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "remove",
            "-y",
            "--purge",
            package,
        ],
        timeout=1800,
        progress=progress,
        cancel=cancel,
    )
    if not result.ok:
        raise runner.BackendError(f"removing {package} failed", result.output)


def _installed_count(kind: str) -> int:
    try:
        if kind == "flatpak":
            from .backends import flatpak

            return len(flatpak.list_installed()) if flatpak.available() else 0
        if kind == "snap":
            from .backends import snap

            return len(snap.list_installed()) if snap.available() else 0
        if kind == "brew":
            from .backends import brew

            return len(brew.list_installed()) if brew.available() else 0
    except Exception:  # noqa: BLE001 - a broken backend must not block the UI
        return 0
    return 0
