"""Dialogs used by the AppDeb UI."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..models import TarballMetadata  # noqa: E402
from .widgets import file_filter, file_filters, icon_image  # noqa: E402


class OperationDialog(Adw.Dialog):
    """Run a blocking backend callable on a worker thread with a live log."""

    def __init__(
        self,
        title: str,
        worker: Callable,
        *,
        on_success: Callable | None = None,
        on_error: Callable | None = None,
        parent: Gtk.Widget | None = None,
    ) -> None:
        super().__init__()
        self.set_title(title)
        self.set_content_width(600)
        self.set_content_height(380)
        self._title_text = title
        self._worker = worker
        self._on_success = on_success
        self._on_error = on_error
        self._finished = False
        self._cancel = threading.Event()

        self._title_widget = Adw.WindowTitle(title=title)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_title_widget(self._title_widget)
        self._cancel_button = Gtk.Button(label="Cancel")
        self._cancel_button.connect("clicked", self._on_cancel_clicked)
        header.pack_end(self._cancel_button)

        self._progress = Gtk.ProgressBar()
        self._progress.set_pulse_step(0.05)
        self._buffer = Gtk.TextBuffer()
        self._view = Gtk.TextView(buffer=self._buffer)
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)
        self._view.set_monospace(True)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._view.add_css_class("console-view")
        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_child(self._view)
        self._scroller.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(self._progress)
        box.append(self._scroller)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(box)
        self.set_child(toolbar)

        self.connect("notify::visible", self._on_visible_changed)
        GLib.idle_add(self._start)

    def _on_visible_changed(self, *_args) -> None:
        if not self.get_visible() and not self._finished:
            self._cancel.set()

    def _on_cancel_clicked(self, _button: Gtk.Button) -> None:
        if self._finished:
            self.close()
            return
        self._cancel.set()
        self._cancel_button.set_sensitive(False)
        self._cancel_button.set_label("Cancelling…")

    def _start(self) -> bool:
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        return GLib.SOURCE_REMOVE

    def _run(self) -> None:
        try:
            result = self._worker(self._progress_callback, self._cancel)
        except BaseException as exc:  # noqa: BLE001 - reported in the dialog
            GLib.idle_add(self._failed, exc)
        else:
            GLib.idle_add(self._succeeded, result)

    def _progress_callback(self, payload) -> None:
        GLib.idle_add(self._append_progress, payload)

    def _append_progress(self, payload) -> bool:
        if isinstance(payload, tuple) and len(payload) == 2:
            done, total = payload
            try:
                done = float(done)
                total = float(total)
            except (TypeError, ValueError):
                return GLib.SOURCE_REMOVE
            if total > 0:
                self._progress.set_fraction(min(max(done / total, 0.0), 1.0))
            else:
                self._progress.pulse()
            return GLib.SOURCE_REMOVE
        text = str(payload)
        if not text:
            return GLib.SOURCE_REMOVE
        end = self._buffer.get_end_iter()
        self._buffer.insert(end, text + "\n")
        adjustment = self._scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_upper())
        self._progress.pulse()
        return GLib.SOURCE_REMOVE

    def _succeeded(self, result) -> bool:
        if self._finished:
            return GLib.SOURCE_REMOVE
        self._finished = True
        self.close()
        if self._on_success is not None:
            self._on_success(result)
        return GLib.SOURCE_REMOVE

    def _failed(self, exc: BaseException) -> bool:
        if self._finished:
            return GLib.SOURCE_REMOVE
        self._finished = True
        if self._on_error is not None:
            self.close()
            self._on_error(exc)
            return GLib.SOURCE_REMOVE
        self._title_widget.set_title(f"{self._title_text} failed")
        text = str(exc) or exc.__class__.__name__
        output = getattr(exc, "output", "") or ""
        end = self._buffer.get_end_iter()
        self._buffer.insert(end, f"\n{text}\n")
        if output:
            self._buffer.insert(self._buffer.get_end_iter(), f"\n{output}\n")
        adjustment = self._scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_upper())
        self._cancel_button.set_label("Close")
        self._cancel_button.set_sensitive(True)
        self._progress.set_fraction(0.0)
        return GLib.SOURCE_REMOVE


def confirm(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    *,
    confirm_label: str = "Continue",
    destructive: bool = False,
    on_confirm: Callable | None = None,
    extra_child: Gtk.Widget | None = None,
) -> Adw.AlertDialog:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("confirm", confirm_label)
    dialog.set_close_response("cancel")
    dialog.set_default_response("confirm")
    if destructive:
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
    if extra_child is not None:
        dialog.set_extra_child(extra_child)

    def _on_response(_dialog: Adw.AlertDialog, response: str) -> None:
        if response == "confirm" and on_confirm is not None:
            on_confirm()

    dialog.connect("response", _on_response)
    dialog.present(parent)
    return dialog


class AppImageInstallDialog(Adw.Dialog):
    """Collect AppImage metadata before installing into the managed store."""

    def __init__(
        self,
        source: Path,
        metadata: object | None,
        on_install: Callable[[dict], None],
        *,
        initial_name: str = "",
        parent: Gtk.Window | None = None,
    ) -> None:
        super().__init__()
        self._parent = parent
        self._source = Path(source)
        self._metadata = metadata
        self._on_install = on_install
        self._icon_path: Path | None = None
        self._file_dialog: Gtk.FileDialog | None = None

        if metadata is not None and metadata.icon:
            candidate = Path(metadata.icon)
            if candidate.is_file():
                self._icon_path = candidate

        self.set_title("Install AppImage")
        self.set_content_width(620)
        self.set_content_height(560)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        install = Gtk.Button(label="Install")
        install.add_css_class("suggested-action")
        install.connect("clicked", self._on_install_clicked)
        header.pack_end(install)

        warning = Gtk.Label(
            label=(
                "AppImages run outside the Flatpak sandbox and can read your files. "
                "Only install apps you trust."
            )
        )
        warning.set_wrap(True)
        warning.set_xalign(0.0)
        warning.add_css_class("dim-label")

        details = Adw.PreferencesGroup(title="Application details")
        self._name = Adw.EntryRow(title="Name")
        self._name.set_text(initial_name or (metadata.name if metadata else "") or source.stem)
        self._comment = Adw.EntryRow(title="Comment")
        self._comment.set_text(metadata.comment if metadata else "")
        self._categories = Adw.EntryRow(title="Categories")
        self._categories.set_text((metadata.categories if metadata else "") or "Utility;")
        self._wm_class = Adw.EntryRow(title="Window class")
        self._wm_class.set_text(metadata.wm_class if metadata else "")
        for row in (self._name, self._comment, self._categories, self._wm_class):
            details.add(row)

        icon_group = Adw.PreferencesGroup(title="Icon")
        self._icon_row = Adw.ActionRow(title="Application icon")
        if self._icon_path is not None:
            self._icon_row.set_subtitle(str(self._icon_path))
        else:
            self._icon_row.set_subtitle("No embedded icon found")
        self._preview = icon_image(str(self._icon_path) if self._icon_path else "", 48)
        self._icon_row.add_prefix(self._preview)
        choose_button = Gtk.Button(label="Choose…")
        choose_button.connect("clicked", lambda _b: self._choose_icon())
        self._icon_row.add_suffix(choose_button)
        if metadata is not None and metadata.icon:
            extract_button = Gtk.Button(label="Extract icon")
            extract_button.connect("clicked", lambda _b: self._extract_icon())
            self._icon_row.add_suffix(extract_button)
        icon_group.add(self._icon_row)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.append(warning)
        box.append(details)
        box.append(icon_group)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(box)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        self.set_child(toolbar)

    def _choose_icon(self) -> None:
        dialog = Gtk.FileDialog(title="Choose icon")
        filt = file_filter("Images", ["*.png", "*.svg", "*.jpg", "*.jpeg", "*.webp"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(self._parent, None, self._on_icon_chosen)

    def _on_icon_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is None or gfile.get_path() is None:
            return
        self._icon_path = Path(gfile.get_path())
        self._icon_row.set_subtitle(str(self._icon_path))
        self._preview.set_from_file(str(self._icon_path))

    def _extract_icon(self) -> None:
        if self._metadata is None or not self._metadata.icon:
            return
        candidate = Path(self._metadata.icon)
        if candidate.is_file():
            self._icon_path = candidate
            self._icon_row.set_subtitle(str(candidate))
            self._preview.set_from_file(str(candidate))
        else:
            self._icon_row.set_subtitle("The embedded icon is not available; choose one manually")

    def _on_install_clicked(self, _button: Gtk.Button) -> None:
        name = self._name.get_text().strip()
        if not name:
            self._name.add_css_class("error")
            return
        payload = {
            "name": name,
            "comment": self._comment.get_text().strip(),
            "categories": self._categories.get_text().strip() or "Utility;",
            "wm_class": self._wm_class.get_text().strip(),
            "icon_source": self._icon_path,
        }
        self.close()
        self._on_install(payload)


class TarballInstallDialog(Adw.Dialog):
    """Advanced tarball install: choose the executable, icon and destination."""

    def __init__(
        self,
        source: Path,
        metadata: TarballMetadata,
        on_install: Callable[[dict], None],
        *,
        destination: Path | None = None,
        parent: Gtk.Window | None = None,
    ) -> None:
        super().__init__()
        self._parent = parent
        self._source = Path(source)
        self._metadata = metadata
        self._on_install = on_install
        self._icon_path: Path | None = None
        self._dest: Path | None = destination
        self._file_dialog: Gtk.FileDialog | None = None

        self.set_title("Install .tar.gz application")
        self.set_content_width(640)
        self.set_content_height(560)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        install = Gtk.Button(label="Install")
        install.add_css_class("suggested-action")
        install.connect("clicked", self._on_install_clicked)
        header.pack_end(install)

        warning = Gtk.Label(
            label=(
                "Tarballs run outside a sandbox and can read your files. "
                "Only install apps you trust."
            )
        )
        warning.set_wrap(True)
        warning.set_xalign(0.0)
        warning.add_css_class("dim-label")

        details = Adw.PreferencesGroup(title="Application details")
        self._name = Adw.EntryRow(title="Name")
        self._name.set_text(metadata.name or source.stem)
        details.add(self._name)

        candidates = list(metadata.executable_candidates)
        if metadata.executable_rel and metadata.executable_rel not in candidates:
            candidates.insert(0, metadata.executable_rel)
        self._executables = candidates
        model = Gtk.StringList.new(candidates or ["(no executable found)"])
        self._exec_row = Adw.ComboRow(title="Executable", model=model)
        if metadata.executable_rel in candidates:
            self._exec_row.set_selected(candidates.index(metadata.executable_rel))
        details.add(self._exec_row)

        self._comment = Adw.EntryRow(title="Comment")
        self._comment.set_text(metadata.comment or "")
        details.add(self._comment)

        destination_group = Adw.PreferencesGroup(
            title="Destination",
            description=(
                "Leave empty to install into the managed AppDeb store. "
                "Choose a folder to install into a subfolder of it."
            ),
        )
        self._dest_row = Adw.EntryRow(title="Folder")
        if destination is not None:
            self._dest_row.set_text(str(destination))
        browse = Gtk.Button(label="Browse…")
        browse.connect("clicked", lambda _b: self._choose_folder())
        self._dest_row.add_suffix(browse)
        destination_group.add(self._dest_row)

        icon_group = Adw.PreferencesGroup(title="Icon")
        self._icon_row = Adw.ActionRow(title="Application icon", subtitle="Optional")
        self._preview = icon_image("", 48)
        self._icon_row.add_prefix(self._preview)
        choose = Gtk.Button(label="Choose…")
        choose.connect("clicked", lambda _b: self._choose_icon())
        self._icon_row.add_suffix(choose)
        icon_group.add(self._icon_row)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.append(warning)
        box.append(details)
        box.append(destination_group)
        box.append(icon_group)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(box)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        self.set_child(toolbar)

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Choose destination folder")
        if self._dest is not None:
            try:
                dialog.set_initial_folder(Gio.File.new_for_path(str(self._dest)))
            except Exception:  # noqa: BLE001 - best effort
                pass
        self._file_dialog = dialog
        dialog.select_folder(self._parent, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if gfile is None or gfile.get_path() is None:
            return
        self._dest = Path(gfile.get_path())
        self._dest_row.set_text(str(self._dest))

    def _choose_icon(self) -> None:
        dialog = Gtk.FileDialog(title="Choose icon")
        filt = file_filter("Images", ["*.png", "*.svg", "*.jpg", "*.jpeg", "*.webp"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(self._parent, None, self._on_icon_chosen)

    def _on_icon_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is None or gfile.get_path() is None:
            return
        self._icon_path = Path(gfile.get_path())
        self._icon_row.set_subtitle(str(self._icon_path))
        self._preview.set_from_file(str(self._icon_path))

    def _on_install_clicked(self, _button: Gtk.Button) -> None:
        name = self._name.get_text().strip()
        if not name:
            self._name.add_css_class("error")
            return
        selected = self._exec_row.get_selected()
        if not self._executables or selected >= len(self._executables):
            self._exec_row.add_css_class("error")
            return
        executable_rel = self._executables[selected]
        destination = self._dest
        if destination is None:
            text = self._dest_row.get_text().strip()
            if text:
                destination = Path(text).expanduser()
        payload = {
            "name": name,
            "executable_rel": executable_rel,
            "icon_source": self._icon_path,
            "dest": destination,
            "comment": self._comment.get_text().strip(),
            "categories": self._metadata.categories or "Utility;",
            "wm_class": self._metadata.wm_class or "",
        }
        self.close()
        self._on_install(payload)


class UrlDownloadDialog(Adw.Dialog):
    """Ask for an https URL, optional checksum and destination folder."""

    def __init__(
        self,
        on_download: Callable[[str, str | None, Path | None], None],
        *,
        title: str = "Download from URL",
        show_destination: bool = False,
        parent: Gtk.Window | None = None,
    ) -> None:
        super().__init__()
        self._parent = parent
        self._on_download = on_download
        self._dest: Path | None = None
        self._file_dialog: Gtk.FileDialog | None = None

        self.set_title(title)
        self.set_content_width(600)
        self.set_content_height(420 if show_destination else 340)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        download = Gtk.Button(label="Download")
        download.add_css_class("suggested-action")
        download.connect("clicked", self._on_download_clicked)
        header.pack_end(download)

        details = Adw.PreferencesGroup(title="Download")
        self._url = Adw.EntryRow(title="URL (https://)")
        details.add(self._url)
        self._sha = Adw.EntryRow(title="SHA-256 (optional)")
        details.add(self._sha)

        group = Adw.PreferencesGroup(
            title="Install",
            description=(
                "Downloads are installed from the download cache. "
                "Only install software you trust."
            ),
        )
        if show_destination:
            self._dest_row = Adw.EntryRow(title="Destination folder (optional)")
            browse = Gtk.Button(label="Browse…")
            browse.connect("clicked", lambda _b: self._choose_folder())
            self._dest_row.add_suffix(browse)
            group.add(self._dest_row)
        else:
            self._dest_row = None

        self._error = Gtk.Label()
        self._error.set_wrap(True)
        self._error.set_xalign(0.0)
        self._error.add_css_class("error")
        self._error.set_visible(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.append(details)
        box.append(group)
        box.append(self._error)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(box)
        self.set_child(toolbar)

    def _choose_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Choose destination folder")
        self._file_dialog = dialog
        dialog.select_folder(self._parent, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if gfile is None or gfile.get_path() is None:
            return
        self._dest = Path(gfile.get_path())
        if self._dest_row is not None:
            self._dest_row.set_text(str(self._dest))

    def _on_download_clicked(self, _button: Gtk.Button) -> None:
        url = self._url.get_text().strip()
        if not url.startswith("https://"):
            self._error.set_text("Only https:// URLs are supported.")
            self._error.set_visible(True)
            return
        sha = self._sha.get_text().strip() or None
        destination = self._dest
        if destination is None and self._dest_row is not None:
            text = self._dest_row.get_text().strip()
            if text:
                destination = Path(text).expanduser()
        self.close()
        self._on_download(url, sha, destination)
