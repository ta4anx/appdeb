"""Snap backend: aligned-column parsing, search, info and privileged actions.

Read-only commands run as the user. Install, remove and refresh require root
and therefore run through ``pkexec`` so the desktop receives a polkit prompt.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Callable

from ..i18n import _
from . import runner
from .runner import BackendError

_SNAP = "snap"
_LIST_COLUMNS = ("Name", "Version", "Rev", "Tracking", "Publisher", "Notes")
_FIND_COLUMNS = ("Name", "Version", "Publisher", "Notes", "Summary")


@dataclass
class SnapPackage:
    name: str
    version: str = ""
    revision: str = ""
    tracking: str = ""
    publisher: str = ""
    notes: str = ""


def _run(
    args: list[str],
    *,
    timeout: float | None = 300,
    progress: Callable[[str], None] | None = None,
    cancel=None,
):
    return runner.run(args, timeout=timeout, progress=progress, cancel=cancel)


def available() -> bool:
    return shutil.which(_SNAP) is not None


def version() -> str:
    if not available():
        return ""
    result = _run([_SNAP, "version"], timeout=30)
    if not result.ok:
        return ""
    first = result.output.strip().splitlines()[0].strip() if result.output.strip() else ""
    parts = first.split()
    return parts[1] if len(parts) >= 2 else (parts[0] if parts else "")


def _column_starts(header: str, columns: tuple[str, ...]) -> list[int] | None:
    starts: list[int] = []
    cursor = 0
    for column in columns:
        index = header.find(column, cursor)
        if index < 0:
            return None
        starts.append(index)
        cursor = index + len(column)
    return starts


def _split_columns(line: str, starts: list[int]) -> list[str]:
    values: list[str] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else None
        values.append(line[start:end].strip() if end is not None else line[start:].strip())
    return values


def _clean_publisher(value: str) -> str:
    return value.rstrip("*").strip()


def _data_lines(output: str) -> list[str]:
    lines: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("WARNING:") or stripped.startswith("warning:"):
            continue
        lines.append(line.rstrip("\n"))
    return lines


def parse_list(output: str) -> list[SnapPackage]:
    lines = _data_lines(output)
    if not lines:
        return []
    starts = _column_starts(lines[0], _LIST_COLUMNS)
    if starts is None:
        return []
    packages: list[SnapPackage] = []
    for line in lines[1:]:
        if line.lstrip().startswith(("No snaps", "error:")):
            continue
        fields = _split_columns(line, starts)
        if len(fields) < len(_LIST_COLUMNS):
            continue
        name, version, revision, tracking, publisher, notes = fields
        if not name:
            continue
        packages.append(
            SnapPackage(
                name=name,
                version=version,
                revision=revision,
                tracking=tracking,
                publisher=_clean_publisher(publisher),
                notes=notes,
            )
        )
    return packages


def parse_find(output: str) -> list[SnapPackage]:
    lines = _data_lines(output)
    if not lines:
        return []
    starts = _column_starts(lines[0], _FIND_COLUMNS)
    if starts is None:
        return []
    packages: list[SnapPackage] = []
    seen: set[str] = set()
    for line in lines[1:]:
        fields = _split_columns(line, starts)
        if len(fields) < len(_FIND_COLUMNS):
            continue
        name, version, publisher, notes, _summary = fields
        if not name or name in seen:
            continue
        seen.add(name)
        packages.append(
            SnapPackage(
                name=name,
                version=version,
                publisher=_clean_publisher(publisher),
                notes=notes,
            )
        )
    return packages


def _block(lines: list[str], index: int) -> tuple[list[str], int]:
    """Collect an indented block starting at ``index``; return (lines, next)."""
    collected: list[str] = []
    while index < len(lines):
        current = lines[index]
        if current.strip() and not current[:1].isspace():
            break
        collected.append(current)
        index += 1
    return collected, index


def _strip_indent(line: str) -> str:
    if line.startswith("  "):
        return line[2:]
    return line.lstrip()


def parse_info(output: str) -> dict[str, str]:
    lines = output.splitlines()
    data: dict[str, str] = {}
    channels: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line[:1].isspace():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if value == "|":
            block, index = _block(lines, index)
            data[key] = "\n".join(_strip_indent(item) for item in block).strip()
            continue
        if key == "channels" and not value:
            block, index = _block(lines, index)
            channels = [item.strip() for item in block if item.strip()]
            continue
        data[key] = value

    stable_version = ""
    for entry in channels:
        if entry.startswith("latest/stable:"):
            rest = entry.split(":", 1)[1].strip()
            token = rest.split()[0] if rest.split() else ""
            if token and token not in ("^", "-"):
                stable_version = token
            break

    result = {
        "name": data.get("name", ""),
        "version": data.get("version") or stable_version,
        "summary": data.get("summary", ""),
        "description": data.get("description", ""),
        "publisher": _clean_publisher(data.get("publisher", "")),
        "store_url": data.get("store-url", ""),
        "license": data.get("license", ""),
        "confinement": data.get("confinement", ""),
    }
    if data.get("tracking"):
        result["tracking"] = data["tracking"]
    return result


def parse_channels(output: str) -> dict[str, str]:
    """Return ``{channel: version}`` from the ``channels:`` block of ``snap info``."""
    lines = output.splitlines()
    result: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line[:1].isspace():
            continue
        key, separator, value = line.partition(":")
        if not separator or key.strip() != "channels" or value.strip():
            continue
        while index < len(lines):
            current = lines[index]
            if current.strip() and not current[:1].isspace():
                break
            index += 1
            stripped = current.strip()
            if not stripped:
                continue
            if stripped.startswith(("WARNING:", "warning:")):
                continue
            channel, sep, rest = stripped.partition(":")
            if not sep:
                continue
            channel = channel.strip()
            if not channel:
                continue
            tokens = rest.split()
            version = tokens[0] if tokens else ""
            if version in ("^", "-"):
                version = ""
            result[channel] = version
        break
    return result


def parse_held(output: str) -> bool:
    """Return whether the Notes column of a ``snap list`` row contains ``held``."""
    lines = _data_lines(output)
    if len(lines) < 2:
        return False
    notes_start = lines[0].find("Notes")
    if notes_start < 0:
        return False
    for line in lines[1:]:
        if "held" in line[notes_start:].split():
            return True
    return False


def channels(name: str) -> dict[str, str]:
    snap_name = (name or "").strip()
    if not snap_name or not available():
        return {}
    result = _run([_SNAP, "info", snap_name], timeout=60)
    if not result.ok:
        raise BackendError(_("snap info failed"), result.output)
    return parse_channels(result.output)


def held(name: str) -> bool:
    snap_name = (name or "").strip()
    if not snap_name or not available():
        return False
    result = _run([_SNAP, "list", snap_name], timeout=60)
    if not result.ok:
        return False
    return parse_held(result.output)


def list_installed() -> list[SnapPackage]:
    if not available():
        return []
    result = _run([_SNAP, "list"], timeout=60)
    if not result.ok:
        return []
    return parse_list(result.output)


def search(query: str) -> list[SnapPackage]:
    text = (query or "").strip()
    if len(text) < 2 or not available():
        return []
    result = _run([_SNAP, "find", "--color=never", text], timeout=120)
    if not result.ok:
        raise BackendError(_("snap find failed"), result.output)
    return parse_find(result.output)


def info(name: str) -> dict[str, str]:
    snap_name = (name or "").strip()
    if not snap_name or not available():
        return {}
    result = _run([_SNAP, "info", snap_name], timeout=60)
    if not result.ok:
        return {}
    parsed = parse_info(result.output)
    return parsed if parsed.get("name") else {}


def install(
    name: str,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    args = ["pkexec", _SNAP, "install", name]
    result = _run(args, timeout=3600, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError(_("snap install failed"), result.output)


def uninstall(
    name: str,
    purge: bool = False,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    args = ["pkexec", _SNAP, "remove"]
    if purge:
        args.append("--purge")
    args.append(name)
    result = _run(args, timeout=1800, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError(_("snap remove failed"), result.output)


def refresh(
    name: str | None = None,
    progress: Callable[[str], None] | None = None,
    cancel=None,
    *,
    channel: str | None = None,
) -> None:
    args = ["pkexec", _SNAP, "refresh"]
    selected = (channel or "").strip()
    if selected:
        args.append(f"--channel={selected}")
    if name:
        args.append(name)
    result = _run(args, timeout=3600, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError(_("snap refresh failed"), result.output)


def hold(
    name: str,
    enabled: bool,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    snap_name = (name or "").strip()
    if not snap_name:
        raise BackendError(_("snap name is empty"))
    args = ["pkexec", _SNAP, "refresh", "--hold" if enabled else "--unhold", snap_name]
    result = _run(args, timeout=300, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError(_("snap hold failed"), result.output)
