"""System resource overview page."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import apps, processes  # noqa: E402
from ..i18n import _  # noqa: E402
from . import settings  # noqa: E402
from .widgets import Sparkline, format_duration, format_rate  # noqa: E402

TOP_COUNT = 5


def _level(value: float) -> Gtk.LevelBar:
    bar = Gtk.LevelBar()
    bar.set_min_value(0.0)
    bar.set_max_value(100.0)
    bar.set_value(min(max(value, 0.0), 100.0))
    bar.set_size_request(140, -1)
    bar.set_valign(Gtk.Align.CENTER)
    return bar


class SystemPage(Adw.NavigationPage):
    def __init__(self) -> None:
        super().__init__()
        self.set_title(_("System"))
        self._monitor = processes.SystemMonitor()
        self._timer_id = 0

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=_("System")))

        page = Adw.PreferencesPage()

        self._cpu_label = Gtk.Label()
        self._cpu_spark = Sparkline(content_width=150, content_height=36)
        self._cpu_bar = _level(0.0)
        cpu_group = Adw.PreferencesGroup(title=_("CPU"))
        cpu_row = Adw.ActionRow(title=_("CPU usage"))
        self._cpu_label.add_css_class("stat-value")
        cpu_row.add_suffix(self._cpu_label)
        cpu_row.add_suffix(self._cpu_bar)
        cpu_group.add(cpu_row)
        cpu_history = Adw.ActionRow(title=_("Usage history"))
        cpu_history.add_suffix(self._cpu_spark)
        cpu_group.add(cpu_history)
        page.add(cpu_group)

        self._mem_label = Gtk.Label()
        self._mem_label.add_css_class("stat-value")
        self._mem_bar = _level(0.0)
        memory_group = Adw.PreferencesGroup(title=_("Memory"))
        mem_row = Adw.ActionRow(title=_("Memory usage"))
        mem_row.add_suffix(self._mem_label)
        mem_row.add_suffix(self._mem_bar)
        memory_group.add(mem_row)
        self._swap_label = Gtk.Label()
        self._swap_label.add_css_class("stat-value")
        self._swap_bar = _level(0.0)
        swap_row = Adw.ActionRow(title=_("Swap usage"))
        swap_row.add_suffix(self._swap_label)
        swap_row.add_suffix(self._swap_bar)
        memory_group.add(swap_row)
        page.add(memory_group)

        self._disk_label = Gtk.Label()
        self._disk_label.add_css_class("stat-value")
        self._disk_bar = _level(0.0)
        disk_group = Adw.PreferencesGroup(title=_("Disk"))
        disk_row = Adw.ActionRow(title=_("Home disk usage"))
        disk_row.add_suffix(self._disk_label)
        disk_row.add_suffix(self._disk_bar)
        disk_group.add(disk_row)
        page.add(disk_group)

        self._down_label = Gtk.Label()
        self._down_label.add_css_class("stat-value")
        self._up_label = Gtk.Label()
        self._up_label.add_css_class("stat-value")
        self._net_spark = Sparkline(content_width=150, content_height=36)
        network_group = Adw.PreferencesGroup(title=_("Network"))
        down_row = Adw.ActionRow(title=_("Download"))
        down_row.add_suffix(self._down_label)
        network_group.add(down_row)
        up_row = Adw.ActionRow(title=_("Upload"))
        up_row.add_suffix(self._up_label)
        network_group.add(up_row)
        history_row = Adw.ActionRow(title=_("Download history"))
        history_row.add_suffix(self._net_spark)
        network_group.add(history_row)
        page.add(network_group)

        self._load_label = Gtk.Label()
        self._load_label.add_css_class("stat-value")
        self._uptime_label = Gtk.Label()
        self._uptime_label.add_css_class("stat-value")
        misc_group = Adw.PreferencesGroup(title=_("Load"))
        load_row = Adw.ActionRow(title=_("Load average"))
        load_row.add_suffix(self._load_label)
        misc_group.add(load_row)
        uptime_row = Adw.ActionRow(title=_("Uptime"))
        uptime_row.add_suffix(self._uptime_label)
        misc_group.add(uptime_row)
        page.add(misc_group)

        self._process_group = Adw.PreferencesGroup(title=_("Top processes"))
        self._process_rows: list[Adw.ActionRow] = []
        for _index in range(TOP_COUNT):
            row = Adw.ActionRow(title="—", subtitle="")
            value = Gtk.Label(label="—")
            value.add_css_class("stat-value")
            row.add_suffix(value)
            row._value = value  # type: ignore[attr-defined]
            self._process_group.add(row)
            self._process_rows.append(row)
        page.add(self._process_group)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    def refresh(self) -> None:
        self._sample()

    def _on_map(self, *_args) -> None:
        self._sample()
        self._start_timer()

    def _on_unmap(self, *_args) -> None:
        self._stop_timer()

    def _start_timer(self) -> None:
        self._stop_timer()
        interval = settings.refresh_interval()
        self._timer_id = GLib.timeout_add_seconds(interval, self._tick)

    def _stop_timer(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    def _tick(self) -> bool:
        self._sample()
        return GLib.SOURCE_CONTINUE

    def _sample(self) -> None:
        try:
            snapshot = self._monitor.sample()
        except Exception:  # noqa: BLE001 - monitoring must never crash the UI
            return
        self._cpu_label.set_text(_("%.0f %%") % snapshot.cpu_percent)
        self._cpu_bar.set_value(min(max(snapshot.cpu_percent, 0.0), 100.0))
        self._cpu_spark.push(snapshot.cpu_percent, maximum=100.0)
        self._mem_label.set_text(
            _("%s / %s (%.0f %%)")
            % (
                apps.human_size(snapshot.mem_used),
                apps.human_size(snapshot.mem_total),
                snapshot.mem_percent,
            )
        )
        self._mem_bar.set_value(snapshot.mem_percent)
        self._swap_label.set_text(
            _("%s / %s (%.0f %%)")
            % (
                apps.human_size(snapshot.swap_used),
                apps.human_size(snapshot.swap_total),
                snapshot.swap_percent,
            )
        )
        self._swap_bar.set_value(snapshot.swap_percent)
        self._disk_label.set_text(
            _("%s / %s (%.0f %%)")
            % (
                apps.human_size(snapshot.disk_used),
                apps.human_size(snapshot.disk_total),
                snapshot.disk_percent,
            )
        )
        self._disk_bar.set_value(snapshot.disk_percent)
        self._down_label.set_text(format_rate(snapshot.net_down_rate))
        self._up_label.set_text(format_rate(snapshot.net_up_rate))
        self._net_spark.push(snapshot.net_down_rate)
        load = ", ".join(f"{value:.2f}" for value in snapshot.load_avg)
        self._load_label.set_text(load or "—")
        self._uptime_label.set_text(format_duration(snapshot.uptime_seconds))
        for index, row in enumerate(self._process_rows):
            if index < len(snapshot.top_processes):
                name, cpu, rss = snapshot.top_processes[index]
                row.set_title(name)
                row.set_subtitle(_("%.1f %% CPU") % cpu)
                value = getattr(row, "_value", None)
                if value is not None:
                    value.set_text(apps.human_size(rss) or "—")
            else:
                row.set_title("—")
                row.set_subtitle("")
                value = getattr(row, "_value", None)
                if value is not None:
                    value.set_text("—")
