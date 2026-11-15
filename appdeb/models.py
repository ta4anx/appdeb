"""Data records shared by the backends, services and UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class DesktopEntry:
    id: str
    path: Path
    name: str
    comment: str
    exec_line: str
    icon: str
    terminal: bool
    categories: list[str] = field(default_factory=list)
    startup_wm_class: str = ""
    no_display: bool = False
    source: str = "user"


@dataclass(slots=True)
class InstalledApp:
    entry: DesktopEntry
    kind: str = "system"
    version: str = ""
    size_bytes: int = 0
    scope: str = "user"
    source_ref: str = ""
    install_path: Path | None = None
    db: dict = field(default_factory=dict)

    @property
    def desktop_id(self) -> str:
        return self.entry.id

    @property
    def display_name(self) -> str:
        db_name = (self.db or {}).get("display_name")
        return db_name or self.entry.name


@dataclass(slots=True)
class FlatpakApp:
    application: str
    name: str
    version: str
    branch: str
    arch: str
    origin: str
    size_bytes: int
    scope: str


@dataclass(slots=True)
class FlatpakResult:
    application: str
    name: str
    description: str = ""
    version: str = ""
    branch: str = ""
    remotes: str = ""


@dataclass(slots=True)
class TarballMetadata:
    name: str
    version: str = ""
    executable_rel: str = ""
    executable_candidates: list[str] = field(default_factory=list)
    icon_rel: str = ""
    desktop_rel: str = ""
    comment: str = ""
    categories: str = "Utility;"
    wm_class: str = ""


@dataclass(slots=True)
class Issue:
    code: str
    path: str
    desktop_id: str
    severity: str
    message: str
    fixable: bool = False


@dataclass(slots=True)
class AppResourceStats:
    pid_count: int = 0
    cpu_percent: float = 0.0
    rss_bytes: int = 0
    read_bytes: int = 0
    write_bytes: int = 0
    threads: int = 0
    started_at: float = 0.0


@dataclass(slots=True)
class SystemSnapshot:
    cpu_percent: float = 0.0
    per_cpu: list[float] = field(default_factory=list)
    mem_total: int = 0
    mem_used: int = 0
    mem_percent: float = 0.0
    swap_total: int = 0
    swap_used: int = 0
    swap_percent: float = 0.0
    disk_total: int = 0
    disk_used: int = 0
    disk_percent: float = 0.0
    net_down_rate: float = 0.0
    net_up_rate: float = 0.0
    load_avg: list[float] = field(default_factory=list)
    uptime_seconds: float = 0.0
    top_processes: list[tuple[str, float, int]] = field(default_factory=list)
