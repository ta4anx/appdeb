"""Homebrew backend: list, search, install, upgrade and remove formulas.

Homebrew is a user-level package manager, so no privilege escalation is used.
All commands run through :mod:`appdeb.backends.runner`; nothing is executed
through a shell.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from typing import Callable

from . import runner
from .runner import BackendError

_BREW = "brew"
_FORMULA_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9@+_.-]*")
_OUTDATED_LINE = re.compile(r"^(\S+)\s+\(([^)]*)\)\s*<\s*(.+?)\s*$")


@dataclass
class BrewPackage:
    name: str
    version: str = ""
    description: str = ""
    installed: bool = False


@dataclass
class BrewResult:
    name: str
    description: str = ""
    version: str = ""
    homepage: str = ""


def _run(
    args: list[str],
    *,
    timeout: float | None = 300,
    progress: Callable[[str], None] | None = None,
    cancel=None,
):
    return runner.run(args, timeout=timeout, progress=progress, cancel=cancel)


def available() -> bool:
    return shutil.which(_BREW) is not None


def version() -> str:
    if not available():
        return ""
    result = _run([_BREW, "--version"], timeout=30)
    if not result.ok:
        return ""
    first = result.output.strip().splitlines()[0].strip() if result.output.strip() else ""
    parts = first.split()
    if len(parts) >= 2 and parts[0].lower() == "homebrew":
        return parts[1]
    return parts[-1] if parts else ""


def parse_list(output: str) -> list[BrewPackage]:
    """Parse ``brew list --formula --versions`` (``name version`` per line)."""
    packages: list[BrewPackage] = []
    seen: set[str] = set()
    for line in output.splitlines():
        clean = line.strip()
        if not clean:
            continue
        parts = clean.split(None, 1)
        name = parts[0]
        if not _FORMULA_NAME.fullmatch(name) or name in seen:
            continue
        seen.add(name)
        packages.append(
            BrewPackage(
                name=name,
                version=parts[1].strip() if len(parts) > 1 else "",
                installed=True,
            )
        )
    return packages


def _load_items(output: str) -> list[dict]:
    """Decode ``brew info --json=v2`` into the formula dictionaries."""
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("formulae")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _item_version(item: dict) -> str:
    versions = item.get("versions") or {}
    if not isinstance(versions, dict):
        return ""
    return str(versions.get("stable") or versions.get("head") or "")


def _item_installed_version(item: dict) -> str:
    installed = item.get("installed") or []
    if isinstance(installed, list):
        for entry in installed:
            if isinstance(entry, dict) and entry.get("version"):
                return str(entry["version"])
    return ""


def parse_info_json(output: str) -> list[BrewPackage]:
    """Parse ``brew info --json=v2 --formula`` output into packages."""
    packages: list[BrewPackage] = []
    for item in _load_items(output):
        name = str(item.get("name") or item.get("full_name") or "")
        if not name:
            continue
        packages.append(
            BrewPackage(
                name=name,
                version=_item_version(item),
                description=str(item.get("desc") or ""),
                installed=bool(item.get("installed")),
            )
        )
    return packages


def parse_search(output: str) -> list[str]:
    """Parse ``brew search --formula`` output into formula names.

    Homebrew may append timing/progress lines; only a line that is exactly one
    formula token is accepted.
    """
    names: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("==>"):
            continue
        if not _FORMULA_NAME.fullmatch(clean):
            continue
        if clean not in seen:
            seen.add(clean)
            names.append(clean)
    return names


def _capture_output(args: list[str], *, timeout: float) -> str:
    """Run a read-only command and keep every line.

    ``runner.run`` retains only a 200-line tail for progress streaming; large
    ``brew info --json=v2`` payloads exceed that. The per-line progress callback
    sees the whole stream, so collect it there instead of editing the runner.
    """
    lines: list[str] = []
    try:
        result = _run(args, timeout=timeout, progress=lines.append)
    except Exception:
        return ""
    if lines:
        return "\n".join(lines)
    return result.output


def _details_for(names: list[str]) -> dict[str, dict]:
    """Return formula metadata keyed by name, empty when info is unavailable."""
    if not names or not available():
        return {}
    output = _capture_output(
        [_BREW, "info", "--json=v2", "--formula", *names], timeout=180
    )
    details: dict[str, dict] = {}
    for item in _load_items(output):
        name = str(item.get("name") or item.get("full_name") or "")
        if name:
            details[name] = item
    return details


def list_installed() -> list[BrewPackage]:
    if not available():
        return []
    result = _run([_BREW, "list", "--formula", "--versions"], timeout=120)
    if not result.ok:
        return []
    packages = parse_list(result.output)
    if not packages:
        return []
    details = _details_for([package.name for package in packages])
    for package in packages:
        item = details.get(package.name)
        if item:
            package.version = package.version or _item_version(item)
            package.description = str(item.get("desc") or "")
        package.installed = True
    return packages


def search(query: str) -> list[BrewResult]:
    text = (query or "").strip()
    if len(text) < 2 or not available():
        return []
    result = _run([_BREW, "search", "--formula", text], timeout=120)
    if not result.ok:
        raise BackendError("brew search failed", result.output)
    names = parse_search(result.output)
    if not names:
        return []
    details = _details_for(names)
    results: list[BrewResult] = []
    for name in names:
        item = details.get(name, {})
        results.append(
            BrewResult(
                name=name,
                description=str(item.get("desc") or ""),
                version=_item_version(item) if item else "",
                homepage=str(item.get("homepage") or ""),
            )
        )
    return results


def info(formula: str) -> dict[str, str]:
    name = (formula or "").strip()
    if not name or not available():
        return {}
    output = _capture_output([_BREW, "info", "--json=v2", "--formula", name], timeout=120)
    items = _load_items(output)
    if not items:
        return {}
    item = items[0]
    return {
        "name": str(item.get("name") or name),
        "description": str(item.get("desc") or ""),
        "homepage": str(item.get("homepage") or ""),
        "version": _item_version(item),
        "caveats": str(item.get("caveats") or ""),
        "installed_version": _item_installed_version(item),
    }


def install(
    formula: str,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    args = [_BREW, "install", "--formula", formula]
    result = _run(args, timeout=3600, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError("brew install failed", result.output)


def uninstall(
    formula: str,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    args = [_BREW, "uninstall", "--formula", formula]
    result = _run(args, timeout=1800, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError("brew uninstall failed", result.output)


def _parse_outdated(output: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = _OUTDATED_LINE.match(line.strip())
        if not match:
            continue
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        pairs.append((name, match.group(3).strip()))
    return pairs


def outdated() -> list[BrewResult]:
    if not available():
        return []
    result = _run([_BREW, "outdated", "--formula", "--verbose"], timeout=300)
    if not result.ok:
        return []
    pairs = _parse_outdated(result.output)
    if not pairs:
        return []
    details = _details_for([name for name, _new in pairs])
    results: list[BrewResult] = []
    for name, new_version in pairs:
        item = details.get(name, {})
        results.append(
            BrewResult(
                name=name,
                description=str(item.get("desc") or ""),
                version=new_version,
                homepage=str(item.get("homepage") or ""),
            )
        )
    return results


def update(
    formula: str | None,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    args = [_BREW, "upgrade"]
    if formula:
        args.extend(["--formula", formula])
    result = _run(args, timeout=3600, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError("brew upgrade failed", result.output)
