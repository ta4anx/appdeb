"""Debian package backend built on dpkg-deb/dpkg-query/apt-get."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import runner
from .runner import BackendError

PROTECTED_PACKAGES = {
    "ubuntu-desktop",
    "ubuntu-desktop-minimal",
    "gnome-shell",
    "gdm3",
    "systemd",
    "apt",
    "dpkg",
}
FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s?(.*)$")


@dataclass
class DebInfo:
    package: str = ""
    version: str = ""
    architecture: str = ""
    installed_size: int = 0
    description: str = ""
    maintainer: str = ""
    depends: str = ""


def _parse_fields(output: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    for line in output.splitlines():
        match = FIELD_RE.match(line)
        if match:
            current = match.group(1)
            fields[current] = match.group(2).strip()
        elif current and line[:1] in (" ", "\t"):
            continuation = line.strip()
            if continuation:
                fields[current] = (fields[current] + " " + continuation).strip()
    return fields


def _to_int(value: str) -> int:
    try:
        return int((value or "").strip())
    except ValueError:
        return 0


def inspect(path: Path) -> DebInfo:
    source = Path(path)
    result = runner.capture(
        [
            "dpkg-deb",
            "-f",
            str(source),
            "Package",
            "Version",
            "Architecture",
            "Installed-Size",
            "Description",
            "Maintainer",
            "Depends",
        ],
        timeout=120,
    )
    if not result.ok:
        raise BackendError("cannot inspect package: " + source.name, result.output)
    fields = _parse_fields(result.output)
    package = fields.get("Package", "")
    if not package:
        raise BackendError("cannot inspect package: " + source.name, result.output)
    return DebInfo(
        package=package,
        version=fields.get("Version", ""),
        architecture=fields.get("Architecture", ""),
        installed_size=_to_int(fields.get("Installed-Size", "")),
        description=fields.get("Description", ""),
        maintainer=fields.get("Maintainer", ""),
        depends=fields.get("Depends", ""),
    )


def install(
    path: Path,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise BackendError("package not found: " + str(source))
    result = runner.run(
        [
            "pkexec",
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "install",
            "-y",
            str(source),
        ],
        timeout=3600,
        progress=progress,
        cancel=cancel,
    )
    if not result.ok:
        raise BackendError("package installation failed", result.output)


def _query_field(package: str, fmt: str) -> str:
    result = runner.capture(
        ["dpkg-query", "-W", f"-f={fmt}", package],
        timeout=60,
    )
    if not result.ok:
        return ""
    return result.output.strip()


def removal_impact(package: str) -> list[str]:
    result = runner.capture(
        ["apt-get", "-s", "remove", "-y", package],
        timeout=120,
    )
    names: list[str] = []
    inside = False
    for line in result.output.splitlines():
        stripped = line.strip()
        if stripped == "The following packages will be REMOVED:":
            inside = True
            continue
        if not inside:
            continue
        if not stripped:
            break
        if not line.startswith((" ", "\t")):
            break
        for token in stripped.split():
            token = token.strip(",").strip()
            if not token:
                continue
            token = token.rstrip("*")
            token = token.split(":")[0]
            if token and token not in names:
                names.append(token)
    return names


def _essential_reason(package: str) -> str:
    output = _query_field(package, "${Essential} ${Priority}")
    tokens = output.lower().split()
    if "yes" in tokens:
        return "essential package"
    if "required" in tokens:
        return "required-priority package"
    return ""


def remove(
    package: str,
    purge: bool,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    package = (package or "").strip()
    if not package:
        raise BackendError("package name is empty")
    reason = _essential_reason(package)
    impact = removal_impact(package)
    protected = sorted(PROTECTED_PACKAGES.intersection(impact))
    if reason:
        raise BackendError(
            f"refusing to remove {reason}: {package}",
            "\n".join(impact),
        )
    if protected:
        raise BackendError(
            f"refusing to remove {package}; it would remove protected packages: "
            + ", ".join(protected),
            "\n".join(impact),
        )
    args = [
        "pkexec",
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "remove",
        "-y",
    ]
    if purge:
        args.append("--purge")
    args.append(package)
    result = runner.run(args, timeout=3600, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError("package removal failed", result.output)


def parse_showhold(output: str) -> list[str]:
    """Parse ``apt-mark showhold`` output into package names."""
    packages: list[str] = []
    for line in (output or "").splitlines():
        name = line.strip()
        if not name or name.startswith(("WARNING:", "warning:")):
            continue
        if name not in packages:
            packages.append(name)
    return packages


def held(package: str) -> bool:
    name = (package or "").strip()
    if not name:
        return False
    result = runner.capture(["apt-mark", "showhold"], timeout=60)
    if not result.ok:
        return False
    return name in parse_showhold(result.output)


def hold(
    package: str,
    enabled: bool,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    name = (package or "").strip()
    if not name:
        raise BackendError("package name is empty")
    args = ["pkexec", "apt-mark", "hold" if enabled else "unhold", name]
    result = runner.run(args, timeout=300, progress=progress, cancel=cancel)
    if not result.ok:
        raise BackendError("package hold failed", result.output)


def package_for_desktop(desktop_path: Path) -> str | None:
    source = Path(desktop_path).expanduser().resolve()
    result = runner.capture(["dpkg-query", "-S", str(source)], timeout=60)
    if not result.ok:
        return None
    for line in result.output.splitlines():
        stripped = line.strip()
        if not stripped or ": " not in stripped:
            continue
        package = stripped.split(": ", 1)[0].strip()
        if package:
            return package
    return None


def details(package: str) -> DebInfo | None:
    result = runner.capture(
        [
            "dpkg-query",
            "-W",
            "-f=${Version}\t${Installed-Size}\t${Status}\t${Maintainer}\t${Description}\n",
            package,
        ],
        timeout=60,
    )
    if not result.ok:
        return None
    line = result.output.splitlines()[0] if result.output.splitlines() else ""
    parts = line.split("\t", 4)
    if len(parts) < 5:
        return None
    version, size, status, maintainer, description = parts
    if "install ok installed" not in status:
        return None
    return DebInfo(
        package=package,
        version=version,
        architecture="",
        installed_size=_to_int(size),
        description=description,
        maintainer=maintainer,
        depends="",
    )
