"""Process discovery and resource accounting for installed applications."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import psutil

from . import apps, desktop
from .models import AppResourceStats, InstalledApp, SystemSnapshot


def _exec_token(exec_line: str) -> str:
    return apps._first_exec_token(exec_line)


def _cgroup_text(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cgroup").read_text(errors="replace")
    except OSError:
        return ""


def _appimage_match_targets(app: InstalledApp) -> list[str]:
    targets: list[str] = []
    bundle = apps.executable_path(app)
    if bundle is not None:
        targets.append(bundle.name)
        targets.append(str(bundle))
        stem = bundle.name.replace(".AppImage", "")
        if stem:
            targets.append(stem)
    return targets


def app_processes(app: InstalledApp) -> list[psutil.Process]:
    kind = app.kind
    flatpak_id = app.source_ref
    if kind == "flatpak" and not flatpak_id:
        flatpak_id = desktop._get(desktop.load_keyfile(app.entry.path), "X-Flatpak")

    targets = _appimage_match_targets(app) if kind == "appimage" else []
    exec_path = apps.executable_path(app)
    exec_name = exec_path.name if exec_path else ""
    cmdline_token = _exec_token(app.entry.exec_line)
    cmdline_base = os.path.basename(cmdline_token) if cmdline_token.startswith("/") else ""

    matches: list[psutil.Process] = []
    seen: set[int] = set()
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        pid = proc.pid
        if pid in seen:
            continue
        try:
            if kind == "flatpak" and flatpak_id:
                cgroup = _cgroup_text(pid)
                if f"app-flatpak-{flatpak_id}-" in cgroup or f"app-{flatpak_id}-" in cgroup:
                    matches.append(proc)
                    seen.add(pid)
                    continue
            cmdline = proc.info.get("cmdline") or []
            joined = " ".join(cmdline)
            if kind == "appimage" and targets:
                if any(t and (t in joined) for t in targets):
                    matches.append(proc)
                    seen.add(pid)
                    continue
                if "/.mount_" in joined:
                    for target in targets:
                        if target and target.replace(".AppImage", "") in joined:
                            matches.append(proc)
                            seen.add(pid)
                            break
                    continue
            name = proc.info.get("name") or ""
            exe = proc.info.get("exe") or ""
            wanted = exec_name or cmdline_base
            if wanted and (name == wanted or os.path.basename(exe) == wanted):
                matches.append(proc)
                seen.add(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def app_stats(app: InstalledApp) -> AppResourceStats:
    procs = app_processes(app)
    stats = AppResourceStats(pid_count=len(procs))
    earliest = None
    for proc in procs:
        try:
            stats.cpu_percent += proc.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        try:
            stats.rss_bytes += proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        try:
            io = proc.io_counters()
            stats.read_bytes += io.read_bytes
            stats.write_bytes += io.write_bytes
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            pass
        try:
            stats.threads += proc.num_threads()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        try:
            created = proc.create_time()
            earliest = created if earliest is None else min(earliest, created)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    stats.started_at = earliest or 0.0
    return stats


def terminate(app: InstalledApp) -> int:
    procs = app_processes(app)
    count = 0
    for proc in procs:
        try:
            proc.send_signal(signal.SIGTERM)
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in procs):
            break
        time.sleep(0.2)
    for proc in procs:
        try:
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return count


class SystemMonitor:
    """Stateful sampler; the second call yields meaningful CPU rates."""

    def __init__(self) -> None:
        self._prev_net: tuple[float, int, int] | None = None
        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)

    def sample(self) -> SystemSnapshot:
        snapshot = SystemSnapshot()
        snapshot.cpu_percent = psutil.cpu_percent(None)
        snapshot.per_cpu = psutil.cpu_percent(None, percpu=True)
        memory = psutil.virtual_memory()
        snapshot.mem_total = memory.total
        snapshot.mem_used = memory.total - memory.available
        snapshot.mem_percent = memory.percent
        swap = psutil.swap_memory()
        snapshot.swap_total = swap.total
        snapshot.swap_used = swap.used
        snapshot.swap_percent = swap.percent
        try:
            usage = psutil.disk_usage(str(Path.home()))
            snapshot.disk_total = usage.total
            snapshot.disk_used = usage.used
            snapshot.disk_percent = usage.percent
        except OSError:
            pass
        try:
            counters = psutil.net_io_counters()
            now = time.monotonic()
            if self._prev_net is not None:
                prev_time, prev_recv, prev_sent = self._prev_net
                elapsed = max(now - prev_time, 0.001)
                snapshot.net_down_rate = max(counters.bytes_recv - prev_recv, 0) / elapsed
                snapshot.net_up_rate = max(counters.bytes_sent - prev_sent, 0) / elapsed
            self._prev_net = (now, counters.bytes_recv, counters.bytes_sent)
        except (OSError, AttributeError):
            pass
        try:
            snapshot.load_avg = list(os.getloadavg())
        except OSError:
            snapshot.load_avg = [0.0, 0.0, 0.0]
        snapshot.uptime_seconds = time.time() - psutil.boot_time()

        tops: list[tuple[str, float, int]] = []
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                cpu = proc.cpu_percent(None)
                rss = proc.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            tops.append((proc.info.get("name") or str(proc.pid), cpu, rss))
        tops.sort(key=lambda item: (item[1], item[2]), reverse=True)
        snapshot.top_processes = tops[:5]
        return snapshot
