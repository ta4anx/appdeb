"""Flatpak backend: list, search, install, update and permission overrides."""

from __future__ import annotations

import shutil
from typing import Callable

from ..apps import parse_size
from ..i18n import _
from ..models import FlatpakApp, FlatpakResult
from . import runner
from .runner import BackendError

SCOPES = ("user", "system")
OVERRIDE_ACTIONS = ("share", "unshare", "socket", "nosocket", "filesystem", "nofilesystem")
AUTH_MARKERS = ("not authorized", "requires authentication")


def _scope_flag(scope: str) -> str:
    if scope not in SCOPES:
        raise BackendError(_("invalid flatpak scope: %s") % scope)
    return f"--{scope}"


def _run(
    args: list[str],
    *,
    timeout: float | None = 300,
    progress: Callable[[str], None] | None = None,
    cancel=None,
):
    return runner.run(args, timeout=timeout, progress=progress, cancel=cancel)


def available() -> bool:
    return shutil.which("flatpak") is not None


def _parse_list(output: str) -> list[FlatpakApp]:
    apps: list[FlatpakApp] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 8:
            continue
        application, name, version, branch, arch, origin, size, installation = parts[:8]
        if not application:
            continue
        apps.append(
            FlatpakApp(
                application=application,
                name=name or application,
                version=version,
                branch=branch,
                arch=arch,
                origin=origin,
                size_bytes=parse_size(size) or 0,
                scope=installation.lower() or "system",
            )
        )
    return apps


def list_installed() -> list[FlatpakApp]:
    if not available():
        return []
    result = _run(
        [
            "flatpak",
            "list",
            "--app",
            "--columns=application,name,version,branch,arch,origin,size,installation",
        ],
        timeout=60,
    )
    if not result.ok:
        return []
    return _parse_list(result.output)


def _parse_search(output: str) -> list[FlatpakResult]:
    results: list[FlatpakResult] = []
    seen: set[tuple[str, str]] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 6:
            continue
        application, name, description, version, branch, remotes = parts[:6]
        if not application:
            continue
        key = (application, remotes)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            FlatpakResult(
                application=application,
                name=name or application,
                description=description,
                version=version,
                branch=branch,
                remotes=remotes,
            )
        )
    return results


def search(query: str) -> list[FlatpakResult]:
    text = (query or "").strip()
    if len(text) < 2 or not available():
        return []
    result = _run(
        [
            "flatpak",
            "search",
            "--columns=application,name,description,version,branch,remotes",
            text,
        ],
        timeout=120,
    )
    if not result.ok:
        raise BackendError(_("flatpak search failed"), result.output)
    return _parse_search(result.output)


def _needs_polkit(output: str) -> bool:
    lowered = (output or "").lower()
    return any(marker in lowered for marker in AUTH_MARKERS)


def install(
    ref: str,
    scope: str,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    flag = _scope_flag(scope)
    args = ["flatpak", "install", flag, "-y", "--noninteractive", ref]
    result = _run(args, timeout=3600, progress=progress, cancel=cancel)
    if result.ok:
        return
    if scope == "system" and _needs_polkit(result.output):
        pkexec_args = ["pkexec", "flatpak", "install", "--system", "-y", "--noninteractive", ref]
        retry = _run(pkexec_args, timeout=3600, progress=progress, cancel=cancel)
        if retry.ok:
            return
        raise BackendError(_("flatpak install failed"), retry.output or result.output)
    raise BackendError(_("flatpak install failed"), result.output)


def uninstall(
    app_id: str,
    scope: str,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    flag = _scope_flag(scope)
    args = ["flatpak", "uninstall", flag, "-y", "--noninteractive", app_id]
    result = _run(args, timeout=1800, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError(_("flatpak uninstall failed"), result.output)


def _parse_updates(output: str) -> list[FlatpakResult]:
    results: list[FlatpakResult] = []
    seen: set[tuple[str, str]] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 5:
            continue
        application, name, version, branch, origin = parts[:5]
        if not application:
            continue
        key = (application, origin)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            FlatpakResult(
                application=application,
                name=name or application,
                version=version,
                branch=branch,
                remotes=origin,
            )
        )
    return results


def updates() -> list[FlatpakResult]:
    if not available():
        return []
    result = _run(
        [
            "flatpak",
            "remote-ls",
            "--app",
            "--updates",
            "--columns=application,name,version,branch,origin",
        ],
        timeout=180,
    )
    if not result.ok:
        return []
    return _parse_updates(result.output)


def update(
    ref: str | None,
    scope: str,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    flag = _scope_flag(scope)
    args = ["flatpak", "update", flag, "-y", "--noninteractive"]
    if ref:
        args.append(ref)
    result = _run(args, timeout=3600, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError(_("flatpak update failed"), result.output)


def _parse_info(output: str) -> dict[str, str]:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return {}
    first = lines[0].strip().split(None, 3)
    if len(first) >= 4 and first[0].startswith("app/"):
        return {
            "ref": first[0],
            "origin": first[1],
            "size": first[2],
            "location": first[3],
            "permissions": "\n".join(lines[1:]),
        }
    if len(lines) >= 4 and " " not in lines[0].strip():
        return {
            "ref": lines[0].strip(),
            "location": lines[1].strip(),
            "size": lines[2].strip(),
            "origin": lines[3].strip(),
            "permissions": "\n".join(lines[4:]),
        }
    return {}


def info(app_id: str, scope: str) -> dict[str, str]:
    flag = _scope_flag(scope)
    result = _run(
        [
            "flatpak",
            "info",
            flag,
            "--show-ref",
            "--show-location",
            "--show-size",
            "--show-origin",
            "--show-permissions",
            app_id,
        ],
        timeout=60,
    )
    if not result.ok:
        return {}
    return _parse_info(result.output)


def permissions(app_id: str, scope: str) -> str:
    flag = _scope_flag(scope)
    result = _run(
        ["flatpak", "info", flag, "--show-permissions", app_id],
        timeout=60,
    )
    if not result.ok:
        return ""
    return result.output


def overrides(app_id: str) -> str:
    result = _run(["flatpak", "override", "--user", "--show", app_id], timeout=60)
    if not result.ok:
        return ""
    return result.output


def set_override(app_id: str, action: str, value: str) -> None:
    if action not in OVERRIDE_ACTIONS:
        raise BackendError(_("unsupported flatpak override action: %s") % action)
    result = _run(
        ["flatpak", "override", "--user", f"--{action}={value}", app_id],
        timeout=60,
    )
    if not result.ok:
        raise BackendError(_("flatpak override failed"), result.output)


def reset_overrides(app_id: str) -> None:
    result = _run(["flatpak", "override", "--user", "--reset", app_id], timeout=60)
    if not result.ok:
        raise BackendError(_("flatpak override reset failed"), result.output)
