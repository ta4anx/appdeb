"""Desktop entry doctor and maintenance tools page."""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import desktop, doctor, icons  # noqa: E402
from ..models import Issue  # noqa: E402
from .dialogs import OperationDialog  # noqa: E402
from .widgets import escape_markup, show_toast  # noqa: E402

SEVERITY_ICONS = {
    "error": "dialog-error-symbolic",
    "warning": "dialog-warning-symbolic",
    "info": "dialog-information-symbolic",
}


class ToolsPage(Adw.NavigationPage):
    def __init__(self) -> None:
        super().__init__()
        self.set_title("Tools")
        self._issue_rows: list[Adw.PreferencesRow] = []
        self._scanning = False

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Tools"))
        scan_button = Gtk.Button(icon_name="view-refresh-symbolic")
        scan_button.set_tooltip_text("Scan desktop entries")
        scan_button.connect("clicked", lambda _b: self.scan())
        header.pack_end(scan_button)

        page = Adw.PreferencesPage()

        actions = Adw.PreferencesGroup(
            title="Maintenance",
            description="Repair desktop entries and refresh application metadata.",
        )
        for title, subtitle, handler in (
            ("Scan desktop entries", "Look for invalid entries, missing icons and unmatched window classes", self.scan),
            ("Rebuild icon cache", "Refresh the user hicolor icon cache", self._rebuild_icons),
            ("Refresh application database", "Regenerate desktop-file-utils caches", self._refresh_database),
            ("Back up desktop entries", "Copy user desktop entries and the database to the backup folder", self._backup),
        ):
            row = Adw.ActionRow(title=escape_markup(title), subtitle=escape_markup(subtitle), activatable=True)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _r, h=handler: h())
            actions.add(row)
        page.add(actions)

        self._issues_group = Adw.PreferencesGroup(title="Issues")
        self._status_row = Adw.ActionRow(
            title="No scan has run yet",
            subtitle="Scan to inspect all desktop entries",
        )
        self._issues_group.add(self._status_row)
        page.add(self._issues_group)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)

    # ------------------------------------------------------------------ scan
    def scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._status_row.set_title("Scanning desktop entries…")
        self._status_row.set_subtitle("")
        self._status_row.set_visible(True)
        self._clear_issues()

        def worker() -> None:
            try:
                issues = doctor.scan()
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self._scan_failed, str(exc))
                return
            GLib.idle_add(self._apply_issues, issues)

        threading.Thread(target=worker, daemon=True).start()

    def _scan_failed(self, message: str) -> bool:
        self._scanning = False
        self._status_row.set_title("Scan failed")
        self._status_row.set_subtitle(escape_markup(message))
        self._status_row.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _clear_issues(self) -> None:
        for row in self._issue_rows:
            self._issues_group.remove(row)
        self._issue_rows = []

    def _apply_issues(self, issues: list[Issue]) -> bool:
        self._scanning = False
        self._clear_issues()
        if not issues:
            self._status_row.set_title("No issues found")
            self._status_row.set_subtitle("All scanned desktop entries look healthy")
            self._status_row.set_visible(True)
            return GLib.SOURCE_REMOVE
        self._status_row.set_title(f"{len(issues)} issue" + ("" if len(issues) == 1 else "s"))
        self._status_row.set_subtitle(
            f"{sum(1 for issue in issues if issue.fixable)} can be fixed automatically"
        )
        self._status_row.set_visible(True)
        for issue in issues:
            row = Adw.ActionRow(title=issue.message, subtitle=issue.path)
            row.set_subtitle_selectable(True)
            row.add_prefix(
                Gtk.Image.new_from_icon_name(SEVERITY_ICONS.get(issue.severity, "dialog-information-symbolic"))
            )
            if issue.fixable:
                fix = Gtk.Button(label="Fix")
                fix.add_css_class("suggested-action")
                fix.connect("clicked", lambda _b, i=issue: self._fix(i))
                row.add_suffix(fix)
            self._issues_group.add(row)
            self._issue_rows.append(row)
        return GLib.SOURCE_REMOVE

    def _fix(self, issue: Issue) -> None:
        def worker(progress, cancel) -> bool:
            return doctor.fix(issue)

        dialog = OperationDialog(
            f"Fixing {issue.code}",
            worker,
            on_success=self._on_fixed,
            parent=self.get_root(),
        )
        dialog.present(self.get_root())

    def _on_fixed(self, changed: bool) -> None:
        show_toast(self, "Issue fixed" if changed else "Could not fix this issue")
        self.scan()

    # --------------------------------------------------------------- actions
    def _rebuild_icons(self) -> None:
        ok = icons.rebuild_cache()
        show_toast(self, "Icon cache rebuilt" if ok else "Could not rebuild the icon cache")

    def _refresh_database(self) -> None:
        desktop.update_desktop_database()
        show_toast(self, "Application database refreshed")

    def _backup(self) -> None:
        try:
            target = doctor.backup_desktop_entries()
        except OSError as exc:
            show_toast(self, f"Backup failed: {exc}")
            return
        show_toast(self, f"Backup created in {target}")
