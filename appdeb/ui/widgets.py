"""Small reusable widgets and formatting helpers for the AppDeb UI."""

from __future__ import annotations

from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import icons  # noqa: E402

ACCENT = (0.208, 0.518, 0.894)


class Sparkline(Gtk.DrawingArea):
    """A tiny line chart keeping the most recent 60 samples."""

    def __init__(
        self,
        *,
        maximum: float | None = None,
        content_width: int = 140,
        content_height: int = 40,
    ) -> None:
        super().__init__()
        self._values: deque[float] = deque(maxlen=60)
        self._maximum = maximum
        self.set_content_width(content_width)
        self.set_content_height(content_height)
        self.set_draw_func(self._draw)

    def push(self, value: float, maximum: float | None = None) -> None:
        if maximum is not None:
            self._maximum = maximum
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        self._values.append(max(number, 0.0))
        self.queue_draw()

    def clear(self) -> None:
        self._values.clear()
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        values = list(self._values)
        if len(values) < 2:
            return
        top = self._maximum if self._maximum else max(values)
        top = max(top, 1.0)
        cr.set_line_width(1.5)
        cr.set_source_rgba(*ACCENT, 1.0)
        step = width / max(len(values) - 1, 1)
        for index, value in enumerate(values):
            x = index * step
            y = height - min(value / top, 1.0) * (height - 2) - 1
            if index == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()


def icon_image(icon_value: str, size: int = 48) -> Gtk.Image:
    image = Gtk.Image.new_from_gicon(icons.resolve(icon_value))
    image.set_pixel_size(size)
    image.add_css_class("app-icon")
    return image


def badge(text: str, tooltip: str | None = None) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class("badge-muted")
    if tooltip:
        label.set_tooltip_text(tooltip)
    return label


def chevron() -> Gtk.Image:
    image = Gtk.Image.new_from_icon_name("go-next-symbolic")
    image.add_css_class("dim-label")
    return image


def show_toast(widget: Gtk.Widget, message: str) -> None:
    root = widget.get_root()
    overlay = getattr(root, "toast_overlay", None)
    if overlay is not None:
        overlay.add_toast(Adw.Toast.new(message))


def format_rate(bytes_per_second: float) -> str:
    value = max(float(bytes_per_second or 0.0), 0.0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} MB/s"
    if value >= 1_000:
        return f"{value / 1_000:.1f} kB/s"
    return f"{value:.0f} B/s"


def format_duration(seconds: float) -> str:
    total = int(max(seconds or 0.0, 0.0))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def monospace_view(text: str, *, height: int = 160) -> Gtk.ScrolledWindow:
    view = Gtk.TextView()
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_monospace(True)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.add_css_class("permissions-view")
    view.get_buffer().set_text(text or "")
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.set_min_content_height(height)
    scroller.set_child(view)
    return scroller


def file_filter(name: str, patterns: list[str], suffixes: list[str] | None = None) -> Gtk.FileFilter:
    filt = Gtk.FileFilter()
    filt.set_name(name)
    for pattern in patterns:
        filt.add_pattern(pattern)
    for suffix in suffixes or []:
        filt.add_suffix(suffix)
    return filt


def file_filters(filters: list[Gtk.FileFilter]) -> object:
    store = None
    try:
        from gi.repository import Gio

        store = Gio.ListStore.new(Gtk.FileFilter)
        for filt in filters:
            store.append(filt)
    except Exception:  # noqa: BLE001 - older bindings
        store = None
    return store
