"""First-run welcome wizard: a short guided tour of what AppDeb can do."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import APP_ID, db  # noqa: E402
from ..i18n import _  # noqa: E402

SETTING_KEY = "onboarding_done"

PAGES = [
    (
        "io.github.ta4anx.AppDeb",
        _("Welcome to AppDeb"),
        _(
            "AppDeb puts every kind of Linux application in one window: Flatpak, Snap, "
            "Homebrew, AppImage, .deb packages and portable .tar.gz archives."
        ),
    ),
    (
        "package-x-generic-symbolic",
        _("Check your package managers"),
        _(
            "The Managers page shows which package managers exist on this computer, their "
            "versions and how many packages each one owns. You can install a missing "
            "manager there, or remove one once it no longer manages any packages."
        ),
    ),
    (
        "folder-download-symbolic",
        _("Install by searching or by typing"),
        _(
            "Search Flathub, the Snap Store or Homebrew, or type an exact package name. "
            "AppImages, .deb packages and .tar.gz archives can be installed from a file or "
            "an https URL, and every install is checked before it touches your system."
        ),
    ),
    (
        "preferences-system-symbolic",
        _("Fix icons and desktop entries"),
        _(
            "If a running window shows a generic gear instead of its real icon, the entry is "
            "missing a window-class association. The Tools page finds those problems and "
            "repairs them, with a backup of every file it changes."
        ),
    ),
    (
        "emblem-ok-symbolic",
        _("You are ready"),
        _(
            "Pick a page from the sidebar to start. Settings holds the Flatpak install scope, "
            "the interface language and a button to show this guide again at any time."
        ),
    ),
]


class WelcomeDialog(Adw.Dialog):
    """Multi-page introduction shown on first launch."""

    def __init__(self, on_finish=None) -> None:
        super().__init__()
        self._on_finish = on_finish
        self._index = 0
        self._finished = False
        self.set_title(_("Welcome to AppDeb"))
        self.set_content_width(640)
        self.set_content_height(520)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        self._title = Adw.WindowTitle(title=_("Welcome to AppDeb"), subtitle="")
        header.set_title_widget(self._title)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_vexpand(True)
        for icon, title, body in PAGES:
            self._stack.add_named(self._page(icon, title, body), title)

        self._back = Gtk.Button(label=_("Back"))
        self._back.connect("clicked", lambda _b: self._step(-1))
        self._next = Gtk.Button(label=_("Next"))
        self._next.add_css_class("suggested-action")
        self._next.connect("clicked", lambda _b: self._step(1))
        self._skip = Gtk.Button(label=_("Skip"))
        self._skip.add_css_class("flat")
        self._skip.connect("clicked", lambda _b: self._finish())

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls.set_margin_top(12)
        controls.set_margin_bottom(18)
        controls.set_margin_start(18)
        controls.set_margin_end(18)
        self._dots = Gtk.Label()
        self._dots.add_css_class("dim-label")
        controls.append(self._dots)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        controls.append(spacer)
        controls.append(self._skip)
        controls.append(self._back)
        controls.append(self._next)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._stack)
        toolbar.add_bottom_bar(controls)
        self.set_child(toolbar)

        self.connect("closed", self._on_closed)
        self._update()

    # ------------------------------------------------------------------ pages
    def _page(self, icon: str, title: str, body: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(36)
        box.set_margin_end(36)

        image = Gtk.Image.new_from_icon_name(icon)
        image.set_pixel_size(96)
        box.append(image)

        heading = Gtk.Label(label=title)
        heading.add_css_class("title-1")
        heading.set_wrap(True)
        heading.set_justify(Gtk.Justification.CENTER)
        box.append(heading)

        text = Gtk.Label(label=body)
        text.add_css_class("body")
        text.set_wrap(True)
        text.set_justify(Gtk.Justification.CENTER)
        text.set_max_width_chars(52)
        box.append(text)
        return box

    # ---------------------------------------------------------------- controls
    def _update(self) -> None:
        total = len(PAGES)
        self._stack.set_visible_child_name(PAGES[self._index][1])
        self._dots.set_text(_("Step %d of %d") % (self._index + 1, total))
        self._back.set_sensitive(self._index > 0)
        self._skip.set_visible(self._index < total - 1)
        self._next.set_label(_("Finish") if self._index == total - 1 else _("Next"))

    def _step(self, delta: int) -> None:
        new_index = self._index + delta
        if new_index < 0:
            return
        if new_index >= len(PAGES):
            self._finish()
            return
        self._index = new_index
        self._update()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        db.set_setting(SETTING_KEY, "1")
        self.close()
        if self._on_finish is not None:
            self._on_finish()

    def _on_closed(self, *_args) -> None:
        if not self._finished:
            self._finished = True
            db.set_setting(SETTING_KEY, "1")


def should_show() -> bool:
    return db.setting(SETTING_KEY) != "1"


def show_welcome(parent: Gtk.Widget | None = None, *, force: bool = False) -> WelcomeDialog | None:
    """Present the guided tour unless it was already completed (or forced)."""
    if not force and not should_show():
        return None
    dialog = WelcomeDialog()
    dialog.present(parent)
    return dialog
