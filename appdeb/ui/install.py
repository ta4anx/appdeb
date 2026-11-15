"""Install page: Flatpak search, AppImage, tarball and .deb workflows."""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import apps  # noqa: E402
from ..services import downloader  # noqa: E402
from . import settings  # noqa: E402
from .dialogs import (  # noqa: E402
    AppImageInstallDialog,
    OperationDialog,
    TarballInstallDialog,
    UrlDownloadDialog,
    confirm,
)
from .widgets import file_filter, file_filters, show_toast  # noqa: E402


class DebInstallDialog(Adw.Dialog):
    def __init__(self, path: Path, info, on_install) -> None:
        super().__init__()
        self._path = Path(path)
        self._on_install = on_install
        self.set_title("Install .deb package")
        self.set_content_width(600)
        self.set_content_height(520)

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
                "Installing a .deb package changes system software and requires "
                "administrator authentication via polkit."
            )
        )
        warning.set_wrap(True)
        warning.set_xalign(0.0)
        warning.add_css_class("dim-label")

        group = Adw.PreferencesGroup(title="Package")
        for title, value in (
            ("Package", info.package),
            ("Version", info.version),
            ("Architecture", info.architecture),
            ("Installed size", apps.human_size(info.installed_size * 1024) or "—"),
            ("Maintainer", info.maintainer),
            ("Depends", info.depends),
            ("Description", info.description),
        ):
            row = Adw.ActionRow(title=title, subtitle=str(value or "—"))
            row.set_subtitle_selectable(True)
            group.add(row)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.append(warning)
        box.append(group)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(box)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        self.set_child(toolbar)

    def _on_install_clicked(self, _button: Gtk.Button) -> None:
        self.close()
        self._on_install(self._path)


class InstallPage(Adw.NavigationPage):
    def __init__(self, on_open_app, on_changed=None) -> None:
        super().__init__()
        self.set_title("Install")
        self._on_open_app = on_open_app
        self._on_changed = on_changed
        self._search_id = 0
        self._search_token: object | None = None
        self._result_rows: list[Adw.PreferencesRow] = []
        self._file_dialog = None

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Install"))

        page = Adw.PreferencesPage()

        try:
            from ..backends import flatpak

            flatpak_ok = flatpak.available()
        except Exception:  # noqa: BLE001
            flatpak_ok = False

        flatpak_group = Adw.PreferencesGroup(
            title="Flatpak",
            description=(
                "Search Flathub and install apps in the scope chosen in Settings."
                if flatpak_ok
                else "Flatpak is not installed on this system."
            ),
        )
        self._search_row = Adw.EntryRow(title="Search Flathub")
        self._search_row.set_sensitive(flatpak_ok)
        self._search_row.connect("changed", self._on_search_changed)
        flatpak_group.add(self._search_row)
        bundle_row = Adw.ButtonRow(title="Choose Flatpak bundle or .flatpakref…")
        bundle_row.connect("activated", lambda _r: self._choose_flatpak_bundle())
        flatpak_group.add(bundle_row)
        page.add(flatpak_group)

        self._results_group = Adw.PreferencesGroup(title="Results")
        self._status_row = Adw.ActionRow(title="Type at least 2 characters to search")
        self._results_group.add(self._status_row)
        page.add(self._results_group)

        appimage_group = Adw.PreferencesGroup(
            title="AppImage",
            description=(
                "AppImages run outside the Flatpak sandbox and can read your files. "
                "Only install apps you trust."
            ),
        )
        choose_appimage = Adw.ButtonRow(title="Choose AppImage file…")
        choose_appimage.connect("activated", lambda _r: self._choose_appimage())
        appimage_group.add(choose_appimage)
        download_appimage = Adw.ButtonRow(title="Download AppImage from URL…")
        download_appimage.connect("activated", lambda _r: self._download_appimage())
        appimage_group.add(download_appimage)
        page.add(appimage_group)

        tarball_group = Adw.PreferencesGroup(
            title="Tarball",
            description=(
                "Tarballs run outside a sandbox and can read your files. "
                "Only install apps you trust."
            ),
        )
        one_click = Adw.ButtonRow(title="Install .tar.gz (one click)")
        one_click.connect("activated", lambda _r: self._choose_tarball(None, False))
        tarball_group.add(one_click)
        to_folder = Adw.ButtonRow(title="Install .tar.gz to folder…")
        to_folder.connect("activated", lambda _r: self._choose_tarball_folder())
        tarball_group.add(to_folder)
        download_tarball = Adw.ButtonRow(title="Download .tar.gz from URL…")
        download_tarball.connect("activated", lambda _r: self._download_tarball())
        tarball_group.add(download_tarball)
        page.add(tarball_group)

        deb_group = Adw.PreferencesGroup(
            title="Debian package",
            description="Install a local .deb package through apt and polkit.",
        )
        choose_deb = Adw.ButtonRow(title="Choose .deb package…")
        choose_deb.connect("activated", lambda _r: self._choose_deb())
        deb_group.add(choose_deb)
        page.add(deb_group)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)

    # ------------------------------------------------------------ public API
    def open_file(self, path: Path) -> None:
        path = Path(path)
        try:
            from ..backends import tarball

            is_tarball = tarball.is_tarball(path)
        except Exception:  # noqa: BLE001
            is_tarball = False
        try:
            from ..backends import appimage

            is_appimage = appimage.is_appimage(path)
        except Exception:  # noqa: BLE001
            is_appimage = False

        if is_tarball:
            self._start_tarball_install(path, None)
        elif is_appimage:
            self._prepare_appimage(path)
        elif path.suffix.lower() == ".deb":
            self._inspect_deb(path)
        elif path.suffix.lower() in (".flatpak", ".flatpakref"):
            self._install_flatpak_bundle(path)
        else:
            show_toast(self, f"Unsupported file type: {path.name}")

    # --------------------------------------------------------------- flatpak
    def _on_search_changed(self, _row: Adw.EntryRow) -> None:
        if self._search_id:
            GLib.source_remove(self._search_id)
        self._search_id = GLib.timeout_add(400, self._start_search)

    def _start_search(self) -> bool:
        self._search_id = 0
        query = self._search_row.get_text().strip()
        self._clear_results()
        if len(query) < 2:
            self._status_row.set_title("Type at least 2 characters to search")
            self._status_row.set_visible(True)
            return GLib.SOURCE_REMOVE
        self._status_row.set_title(f"Searching for “{query}”…")
        self._status_row.set_visible(True)
        token = object()
        self._search_token = token

        def worker() -> None:
            results: list = []
            error = ""
            try:
                from ..backends import flatpak

                results = flatpak.search(query)
            except Exception as exc:  # noqa: BLE001
                error = str(exc) or exc.__class__.__name__
            GLib.idle_add(self._apply_results, token, results, error)

        threading.Thread(target=worker, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _clear_results(self) -> None:
        for row in self._result_rows:
            self._results_group.remove(row)
        self._result_rows = []

    def _apply_results(self, token: object, results: list, error: str) -> bool:
        if token is not self._search_token:
            return GLib.SOURCE_REMOVE
        self._clear_results()
        if error:
            self._status_row.set_title(f"Search failed: {error}")
            self._status_row.set_visible(True)
            return GLib.SOURCE_REMOVE
        if not results:
            self._status_row.set_title("No results")
            self._status_row.set_visible(True)
            return GLib.SOURCE_REMOVE
        self._status_row.set_visible(False)
        for result in results[:40]:
            subtitle_parts = [result.application]
            if result.description:
                subtitle_parts.append(result.description)
            if result.remotes:
                subtitle_parts.append(result.remotes)
            row = Adw.ActionRow(title=result.name or result.application)
            row.set_subtitle(" — ".join(subtitle_parts))
            row.set_subtitle_lines(2)
            if result.version:
                row.add_suffix(Gtk.Label(label=result.version))
            install = Gtk.Button(label="Install")
            install.add_css_class("suggested-action")
            install.connect(
                "clicked",
                lambda _b, r=result: self._install_flatpak_ref(r.application, r.name),
            )
            row.add_suffix(install)
            self._results_group.add(row)
            self._result_rows.append(row)
        return GLib.SOURCE_REMOVE

    def _install_flatpak_ref(self, ref: str, name: str = "") -> None:
        scope = settings.flatpak_scope()

        def worker(progress, cancel) -> str:
            from ..backends import flatpak

            flatpak.install(ref, scope, progress=progress, cancel=cancel)
            return ref

        dialog = OperationDialog(
            f"Installing {name or ref}",
            worker,
            on_success=lambda ref_id: self._flatpak_installed(ref_id, name),
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _flatpak_installed(self, app_id: str, name: str = "") -> None:
        show_toast(self, f"Installed {name or app_id}")
        app = None
        for candidate in apps.scan_installed(include_hidden=True):
            if candidate.kind == "flatpak" and (
                candidate.source_ref == app_id or candidate.desktop_id == f"{app_id}.desktop"
            ):
                app = candidate
                break
        self._finish_install(app)

    def _choose_flatpak_bundle(self) -> None:
        dialog = Gtk.FileDialog(title="Choose Flatpak bundle or .flatpakref")
        filt = file_filter("Flatpak bundles", ["*.flatpak", "*.flatpakref"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(self.get_root(), None, self._on_bundle_chosen)

    def _on_bundle_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if path is not None:
            self._install_flatpak_bundle(path)

    def _install_flatpak_bundle(self, path: Path) -> None:
        scope = settings.flatpak_scope()

        def worker(progress, cancel):
            from ..backends import flatpak

            flatpak.install(str(path), scope, progress=progress, cancel=cancel)
            return str(path)

        dialog = OperationDialog(
            f"Installing {path.name}",
            worker,
            on_success=lambda _r: self._flatpak_installed(path.stem),
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    # -------------------------------------------------------------- AppImage
    def _choose_appimage(self) -> None:
        dialog = Gtk.FileDialog(title="Choose AppImage")
        filt = file_filter("AppImage", ["*.AppImage", "*.appimage"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(self.get_root(), None, self._on_appimage_chosen)

    def _on_appimage_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if path is not None:
            self._prepare_appimage(path)

    def _prepare_appimage(self, path: Path) -> None:
        from ..backends import appimage

        if not appimage.is_appimage(path):
            show_toast(self, "The selected file is not an AppImage")
            return
        show_toast(self, "Inspecting AppImage…")

        def worker() -> None:
            metadata = None
            try:
                metadata = appimage.extract_metadata(path)
            except Exception:  # noqa: BLE001 - dialog works without metadata
                metadata = None
            GLib.idle_add(self._show_appimage_dialog, path, metadata)

        threading.Thread(target=worker, daemon=True).start()

    def _show_appimage_dialog(self, path: Path, metadata) -> bool:
        dialog = AppImageInstallDialog(
            path,
            metadata,
            lambda payload: self._install_appimage(path, payload),
            parent=self.get_root(),
        )
        dialog.present(self.get_root())
        return GLib.SOURCE_REMOVE

    def _install_appimage(self, path: Path, payload: dict) -> None:
        def worker(progress, cancel):
            from ..backends import appimage

            return appimage.install(
                path,
                payload["name"],
                payload.get("icon_source"),
                payload.get("wm_class", ""),
                payload.get("comment", ""),
                payload.get("categories", "Utility;"),
                progress=progress,
                cancel=cancel,
            )

        dialog = OperationDialog(
            f"Installing {payload['name']}",
            worker,
            on_success=self._on_app_installed,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _download_appimage(self) -> None:
        dialog = UrlDownloadDialog(
            self._start_appimage_download,
            title="Download AppImage",
            show_destination=False,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _start_appimage_download(self, url: str, sha: str | None, _dest: Path | None) -> None:
        destination = downloader.default_destination(url)

        def worker(progress, cancel):
            return downloader.download(url, destination, progress=progress, cancel=cancel, sha256=sha)

        dialog = OperationDialog(
            "Downloading AppImage",
            worker,
            on_success=self._on_downloaded_appimage,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _on_downloaded_appimage(self, path: Path) -> None:
        self._prepare_appimage(Path(path))

    # --------------------------------------------------------------- tarball
    def _choose_tarball(self, destination: Path | None, advanced: bool) -> None:
        dialog = Gtk.FileDialog(title="Choose .tar.gz archive")
        filt = file_filter("Tar archives", ["*.tar.gz", "*.tgz"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(
            self.get_root(),
            None,
            lambda d, r: self._on_tarball_chosen(d, r, destination, advanced),
        )

    def _on_tarball_chosen(
        self,
        dialog: Gtk.FileDialog,
        result,
        destination: Path | None,
        advanced: bool,
    ) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if path is None:
            return
        if advanced:
            self._open_tarball_dialog(path, destination)
        else:
            self._start_tarball_install(path, destination)

    def _choose_tarball_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Choose destination folder")
        self._file_dialog = dialog
        dialog.select_folder(self.get_root(), None, self._on_tarball_folder_chosen)

    def _on_tarball_folder_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        folder = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if folder is not None:
            self._choose_tarball(folder, False)

    def _start_tarball_install(self, source: Path, destination: Path | None) -> None:
        def worker(progress, cancel):
            from ..backends import tarball

            return tarball.install(
                source,
                dest=destination,
                progress=progress,
                cancel=cancel,
            )

        def on_error(exc: BaseException) -> None:
            if "no executable found" in str(exc):
                show_toast(self, "No executable detected; choose one")
                self._open_tarball_dialog(source, destination)
            else:
                self._show_error("Tarball installation failed", exc)

        dialog = OperationDialog(
            f"Installing {source.name}",
            worker,
            on_success=self._on_app_installed,
            on_error=on_error,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _open_tarball_dialog(self, source: Path, destination: Path | None) -> None:
        def worker() -> None:
            metadata = None
            try:
                from ..backends import tarball

                metadata = tarball.inspect(source)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self._show_error_idle, "Could not read archive", str(exc))
                return
            GLib.idle_add(self._show_tarball_dialog_idle, source, metadata, destination)

        threading.Thread(target=worker, daemon=True).start()

    def _show_tarball_dialog_idle(self, source: Path, metadata, destination) -> bool:
        if metadata is None:
            show_toast(self, "Could not read the archive")
            return GLib.SOURCE_REMOVE
        dialog = TarballInstallDialog(
            source,
            metadata,
            lambda payload: self._install_tarball_spec(source, payload),
            destination=destination,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())
        return GLib.SOURCE_REMOVE

    def _install_tarball_spec(self, source: Path, payload: dict) -> None:
        def worker(progress, cancel):
            from ..backends import tarball

            return tarball.install_spec(
                source,
                name=payload["name"],
                executable_rel=payload["executable_rel"],
                icon_source=payload.get("icon_source"),
                dest=payload.get("dest"),
                comment=payload.get("comment", ""),
                categories=payload.get("categories", "Utility;"),
                wm_class=payload.get("wm_class", ""),
                progress=progress,
                cancel=cancel,
            )

        dialog = OperationDialog(
            f"Installing {payload['name']}",
            worker,
            on_success=self._on_app_installed,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _download_tarball(self) -> None:
        dialog = UrlDownloadDialog(
            self._start_tarball_download,
            title="Download .tar.gz",
            show_destination=True,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _start_tarball_download(self, url: str, sha: str | None, destination: Path | None) -> None:
        target = downloader.default_destination(url)

        def worker(progress, cancel):
            return downloader.download(url, target, progress=progress, cancel=cancel, sha256=sha)

        dialog = OperationDialog(
            "Downloading archive",
            worker,
            on_success=lambda path: self._on_downloaded_tarball(Path(path), destination),
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _on_downloaded_tarball(self, path: Path, destination: Path | None) -> None:
        self._start_tarball_install(path, destination)

    # ------------------------------------------------------------------- deb
    def _choose_deb(self) -> None:
        dialog = Gtk.FileDialog(title="Choose .deb package")
        filt = file_filter("Debian packages", ["*.deb"])
        store = file_filters([filt])
        if store is not None:
            dialog.set_filters(store)
            dialog.set_default_filter(filt)
        self._file_dialog = dialog
        dialog.open(self.get_root(), None, self._on_deb_chosen)

    def _on_deb_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path()) if gfile and gfile.get_path() else None
        if path is not None:
            self._inspect_deb(path)

    def _inspect_deb(self, path: Path) -> None:
        try:
            from ..backends import deb

            info = deb.inspect(path)
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not read the package", exc)
            return
        dialog = DebInstallDialog(path, info, self._install_deb)
        dialog.present(self.get_root())

    def _install_deb(self, path: Path) -> None:
        def worker(progress, cancel):
            from ..backends import deb

            deb.install(path, progress=progress, cancel=cancel)
            return path

        dialog = OperationDialog(
            f"Installing {path.name}",
            worker,
            on_success=lambda _r: self._deb_installed(path),
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _deb_installed(self, path: Path) -> None:
        show_toast(self, f"Installed {path.stem}")
        if self._on_changed is not None:
            self._on_changed()

    # ---------------------------------------------------------------- helpers
    def _on_app_installed(self, app) -> None:
        self._finish_install(app)

    def _finish_install(self, app) -> None:
        if self._on_changed is not None:
            self._on_changed()
        if app is not None and self._on_open_app is not None:
            self._on_open_app(app)

    def _show_error(self, heading: str, exc: BaseException) -> None:
        confirm(
            self.get_root(),
            heading,
            str(exc) or exc.__class__.__name__,
            confirm_label="Close",
        )

    def _show_error_idle(self, heading: str, message: str) -> bool:
        confirm(
            self.get_root(),
            heading,
            message,
            confirm_label="Close",
        )
        return GLib.SOURCE_REMOVE
