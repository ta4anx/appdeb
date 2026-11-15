"""Command line interface and GUI launcher for AppDeb."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from . import APP_NAME, __version__, config, db
from .i18n import _, ngettext, setup as setup_i18n

SUBCOMMANDS = ("list", "install", "remove", "fix-icons", "scan", "managers", "search")

VERSION_RE = re.compile(r"\d+(?:\.\d+)+(?:[+~][0-9A-Za-z.+~-]+)?")


def _normalize_version(line: str) -> str:
    text = " ".join((line or "").split())
    match = VERSION_RE.search(text)
    return match.group(0) if match else text


def _print_error(message: str) -> None:
    print(f"AppDeb: {message}", file=sys.stderr)


def _stream(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return True
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _report_exception(exc: BaseException) -> None:
    _print_error(str(exc) or exc.__class__.__name__)
    output = getattr(exc, "output", "") or ""
    if output:
        print(output, file=sys.stderr)


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="appdeb",
        description=_(
            "Install and manage Flatpak, AppImage, .deb and .tar.gz applications."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser._positionals.title = _("positional arguments")
    parser._optionals.title = _("options")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    list_parser = sub.add_parser("list", help=_("print installed desktop entries"))
    list_parser.add_argument("--json", action="store_true", help=_("print JSON"))

    install_parser = sub.add_parser(
        "install", help=_("install a file or package-manager package")
    )
    install_parser.add_argument(
        "file",
        metavar="FILE",
        help=_(
            "AppImage, .deb, .flatpak, .flatpakref, .tar.gz, .tgz, or package name with --from"
        ),
    )
    install_parser.add_argument("--name", help=_("application name"))
    install_parser.add_argument("--icon", metavar="PATH", help=_("icon file to install"))
    install_parser.add_argument(
        "--wm-class", dest="wm_class", help=_("StartupWMClass / window class")
    )
    install_parser.add_argument("--comment", help=_("desktop entry comment"))
    install_parser.add_argument("--categories", help=_("semicolon separated categories"))
    install_parser.add_argument(
        "--scope", choices=["user", "system"], default="user", help=_("Flatpak scope")
    )
    install_parser.add_argument(
        "--dest", metavar="DIR", help=_("destination folder for tarball installs")
    )
    install_parser.add_argument(
        "--exec",
        metavar="PATH",
        dest="executable",
        help=_("tarball executable, relative to the archive root"),
    )
    install_parser.add_argument(
        "--from",
        dest="from_manager",
        choices=["brew", "snap", "flatpak"],
        help=_("install a formula/snap/app ID with the given package manager"),
    )
    install_parser.add_argument(
        "--yes", action="store_true", help=_("skip the confirmation prompt")
    )

    remove_parser = sub.add_parser(
        "remove", help=_("uninstall an application by desktop ID")
    )
    remove_parser.add_argument("desktop_id", metavar="DESKTOP_ID")
    remove_parser.add_argument(
        "--yes", action="store_true", help=_("skip the confirmation prompt")
    )
    remove_parser.add_argument(
        "--purge",
        action="store_true",
        help=_("purge configuration for .deb packages"),
    )

    fix_parser = sub.add_parser(
        "fix-icons",
        help=_("apply every automatically fixable desktop entry issue"),
    )
    fix_parser.add_argument(
        "--yes", action="store_true", help=_("skip the confirmation prompt")
    )

    scan_parser = sub.add_parser("scan", help=_("scan desktop entries for problems"))
    scan_parser.add_argument("--json", action="store_true", help=_("print JSON"))

    managers_parser = sub.add_parser(
        "managers", help=_("show package manager availability and counts")
    )
    managers_parser.add_argument("--json", action="store_true", help=_("print JSON"))

    search_parser = sub.add_parser(
        "search", help=_("search Flatpak, Snap and Homebrew packages")
    )
    search_parser.add_argument(
        "query", metavar="QUERY", help=_("search text (at least 2 characters)")
    )
    search_parser.add_argument(
        "--manager",
        choices=["flatpak", "snap", "brew"],
        help=_("limit the search to one manager (default: all available)"),
    )
    search_parser.add_argument("--json", action="store_true", help=_("print JSON"))

    return parser


def _cmd_list(args) -> int:
    from . import apps

    rows = apps.scan_installed(include_hidden=(db.setting("show_hidden", "0") or "0") == "1")
    if args.json:
        payload = [
            {
                "desktop_id": app.desktop_id,
                "name": app.display_name,
                "kind": app.kind,
                "version": app.version,
                "scope": app.scope,
                "size_bytes": app.size_bytes,
            }
            for app in rows
        ]
        print(json.dumps(payload, indent=2))
        return 0
    for app in rows:
        print(f"{app.desktop_id}\t{app.display_name}\t{app.kind}\t{app.version}")
    return 0


def _cmd_scan(args) -> int:
    from . import doctor

    issues = doctor.scan()
    if args.json:
        print(json.dumps([asdict(issue) for issue in issues], indent=2))
        return 0
    for issue in issues:
        print(f"{issue.severity}\t{issue.code}\t{issue.path}\t{issue.message}")
    return 0


def _manager_available(manager: str) -> bool:
    if manager == "apt":
        return shutil.which("dpkg") is not None
    try:
        module = __import__(f"appdeb.backends.{manager}", fromlist=["available"])
        return bool(module.available())
    except Exception:  # noqa: BLE001 - fall back to PATH detection
        if manager == "brew" and Path("/home/linuxbrew/.linuxbrew/bin/brew").is_file():
            return True
        return shutil.which(manager) is not None


def _manager_binary(manager: str) -> str:
    found = shutil.which(manager)
    if found:
        return found
    if manager == "brew":
        fallback = Path("/home/linuxbrew/.linuxbrew/bin/brew")
        if fallback.is_file():
            return str(fallback)
    if manager == "apt":
        return shutil.which("dpkg") or "dpkg"
    return manager


def _manager_version(manager: str) -> str:
    if manager == "snap":
        args = ["snap", "version"]
    elif manager == "apt":
        args = ["dpkg", "--version"]
    else:
        args = [_manager_binary(manager), "--version"]
    lines = _command_lines(args, timeout=10)
    return _normalize_version(lines[0]) if lines else ""


def _command_lines(args: list[str], *, timeout: float = 60) -> list[str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _manager_counts() -> dict[str, int]:
    snap_lines = _command_lines(["snap", "list"])
    snap_count = sum(
        1
        for line in snap_lines[1:]
        if not line.lower().startswith(("no snaps", "warning:"))
    )
    apt_count = sum(
        1
        for line in _command_lines(["dpkg-query", "-W", "-f=${Status}\\n"])
        if line == "install ok installed"
    )
    return {
        "flatpak": len(_command_lines(["flatpak", "list", "--app", "--columns=application"])),
        "snap": snap_count,
        "brew": len(_command_lines([_manager_binary("brew"), "list", "--formula"])),
        "apt": apt_count,
    }


def _cmd_managers(args) -> int:
    counts = _manager_counts()
    payload = []
    for manager, name in (
        ("flatpak", "Flatpak"),
        ("snap", "Snap"),
        ("brew", "Homebrew"),
        ("apt", "APT / dpkg"),
    ):
        available = _manager_available(manager)
        payload.append(
            {
                "name": name,
                "available": available,
                "version": _manager_version(manager) if available else "",
                "installed_count": counts[manager],
            }
        )
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for row in payload:
        print(
            f"{row['name']}\t{_('available') if row['available'] else _('missing')}\t"
            f"{row['version']}\t{row['installed_count']}"
        )
    return 0


def _clean_field(value) -> str:
    return " ".join(str(value or "").split())


def _search_manager(manager: str, query: str) -> list[dict]:
    rows: list[dict] = []
    if manager == "flatpak":
        from .backends import flatpak

        for result in flatpak.search(query):
            rows.append(
                {
                    "manager": "flatpak",
                    "name": _clean_field(getattr(result, "name", "") or getattr(result, "application", "")),
                    "version": _clean_field(getattr(result, "version", "")),
                    "description": _clean_field(getattr(result, "description", "")),
                }
            )
    elif manager == "snap":
        from .backends import snap

        for result in snap.search(query):
            rows.append(
                {
                    "manager": "snap",
                    "name": _clean_field(getattr(result, "name", "")),
                    "version": _clean_field(getattr(result, "version", "")),
                    "description": _clean_field(
                        getattr(result, "publisher", "") or getattr(result, "notes", "")
                    ),
                }
            )
    else:
        from .backends import brew

        for result in brew.search(query):
            rows.append(
                {
                    "manager": "brew",
                    "name": _clean_field(getattr(result, "name", "")),
                    "version": _clean_field(getattr(result, "version", "")),
                    "description": _clean_field(getattr(result, "description", "")),
                }
            )
    return rows


def _cmd_search(args) -> int:
    query = (args.query or "").strip()
    if len(query) < 2:
        _print_error(_("the search query must be at least 2 characters"))
        return 1
    if args.manager:
        managers = [args.manager]
    else:
        managers = [name for name in ("flatpak", "snap", "brew") if _manager_available(name)]
    results: list[dict] = []
    errors: list[str] = []
    for manager in managers:
        try:
            results.extend(_search_manager(manager, query))
        except Exception as exc:  # noqa: BLE001 - report and continue with other managers
            errors.append(f"{manager}: {str(exc) or exc.__class__.__name__}")
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    for row in results:
        print(f"{row['manager']}\t{row['name']}\t{row['version']}\t{row['description']}")
    if errors:
        for message in errors:
            _print_error(message)
        if not results:
            return 1
    return 0


def _manager_install(args) -> int:
    manager = args.from_manager
    spec = (args.file or "").strip()
    if not spec:
        _print_error(_("a package name is required with --from"))
        return 1
    if not _manager_available(manager):
        _print_error(_("%s is not installed") % manager)
        return 1
    if not args.yes and sys.stdin.isatty():
        if not _confirm(_("Install %s with %s? [y/N] ") % (spec, manager)):
            print(_("Aborted"))
            return 1
    try:
        if manager == "brew":
            from .backends import brew

            brew.install(spec, progress=_stream)
            print(spec)
        elif manager == "snap":
            from .backends import snap

            snap.install(spec, progress=_stream)
            print(spec)
        else:
            from .backends import flatpak

            flatpak.install(spec, args.scope, progress=_stream)
            print(_desktop_id_for_flatpak(spec))
    except Exception as exc:  # noqa: BLE001
        _report_exception(exc)
        return 1
    return 0


def _cmd_fix_icons(args) -> int:
    from . import doctor

    issues = doctor.scan()
    fixable = [issue for issue in issues if issue.fixable]
    if not args.yes and sys.stdin.isatty():
        if not _confirm(
            ngettext("Apply %d fix? [y/N] ", "Apply %d fixes? [y/N] ", len(fixable))
            % len(fixable)
        ):
            print(_("Aborted"))
            return 1
    fixed = 0
    for issue in fixable:
        try:
            changed = doctor.fix(issue)
        except Exception as exc:  # noqa: BLE001 - continue with other issues
            print(_("failed\t%s\t%s\t%s") % (issue.code, issue.path, exc))
            continue
        if changed:
            fixed += 1
            print(_("fixed\t%s\t%s") % (issue.code, issue.path))
        else:
            print(_("skipped\t%s\t%s") % (issue.code, issue.path))
    remaining = doctor.scan()
    errors = [issue for issue in remaining if issue.severity == "error"]
    print(_("fixed %d/%d") % (fixed, len(fixable)))
    return 1 if errors else 0


def _appimage_install(args, path: Path) -> int:
    from .backends import appimage
    from .backends.runner import BackendError

    try:
        if not appimage.is_appimage(path):
            _print_error(_("not an AppImage: %s") % path)
            return 1
        if not args.name:
            _print_error(_("--name is required for AppImage installs"))
            return 1
        metadata = None
        try:
            metadata = appimage.extract_metadata(path)
        except Exception:  # noqa: BLE001 - metadata is optional
            metadata = None
        icon: Path | None = Path(args.icon).expanduser() if args.icon else None
        if icon is None and metadata is not None and metadata.icon:
            candidate = Path(metadata.icon)
            if candidate.is_file():
                icon = candidate
        wm_class = args.wm_class or (metadata.wm_class if metadata else "")
        comment = args.comment or (metadata.comment if metadata else "")
        categories = args.categories or (metadata.categories if metadata else "Utility;")
        app = appimage.install(
            path,
            args.name,
            icon,
            wm_class,
            comment,
            categories,
            progress=_stream,
        )
    except BackendError as exc:
        _report_exception(exc)
        return 1
    except (OSError, ValueError) as exc:
        _report_exception(exc)
        return 1
    print(app.desktop_id)
    return 0


def _tarball_install(args, path: Path) -> int:
    from .backends import tarball
    from .backends.runner import BackendError

    destination = Path(args.dest).expanduser() if args.dest else None
    icon = Path(args.icon).expanduser() if args.icon else None
    try:
        if args.executable:
            metadata = tarball.inspect(path)
            app = tarball.install_spec(
                path,
                name=args.name or metadata.name,
                executable_rel=args.executable,
                icon_source=icon,
                dest=destination,
                comment=args.comment or metadata.comment,
                categories=args.categories or metadata.categories or "Utility;",
                wm_class=args.wm_class or metadata.wm_class,
                progress=_stream,
            )
        else:
            app = tarball.install(
                path,
                name=args.name,
                icon_source=icon,
                dest=destination,
                progress=_stream,
            )
    except BackendError as exc:
        _report_exception(exc)
        if "no executable found" in str(exc):
            _print_error(_("choose an executable with --exec"))
        return 1
    except (OSError, ValueError) as exc:
        _report_exception(exc)
        return 1
    print(app.desktop_id)
    return 0


def _desktop_id_for_flatpak(ref: str) -> str:
    from . import apps

    for candidate in apps.scan_installed(include_hidden=True):
        if candidate.kind != "flatpak":
            continue
        if candidate.desktop_id == f"{ref}.desktop":
            return candidate.desktop_id
        if candidate.source_ref == ref:
            return candidate.desktop_id
    return ref


def _flatpak_install(args, path: Path) -> int:
    from .backends import flatpak
    from .backends.runner import BackendError

    try:
        flatpak.install(str(path), args.scope, progress=_stream)
    except BackendError as exc:
        _report_exception(exc)
        return 1
    print(_desktop_id_for_flatpak(path.stem))
    return 0


def _deb_install(args, path: Path) -> int:
    from .backends import deb
    from .backends.runner import BackendError

    try:
        info = deb.inspect(path)
        deb.install(path, progress=_stream)
    except BackendError as exc:
        _report_exception(exc)
        return 1
    except (OSError, ValueError) as exc:
        _report_exception(exc)
        return 1
    print(info.package)
    return 0


def _cmd_install(args) -> int:
    if getattr(args, "from_manager", None):
        return _manager_install(args)
    path = Path(args.file).expanduser()
    if not path.is_file():
        _print_error(_("file not found: %s") % path)
        return 1
    if not args.yes and sys.stdin.isatty():
        if not _confirm(_("Install %s? [y/N] ") % path.name):
            print(_("Aborted"))
            return 1

    from .backends import appimage, tarball

    suffix = path.name.lower()
    if appimage.is_appimage(path):
        return _appimage_install(args, path)
    if suffix.endswith(".deb"):
        return _deb_install(args, path)
    if suffix.endswith(".flatpak") or suffix.endswith(".flatpakref"):
        return _flatpak_install(args, path)
    if tarball.is_tarball(path):
        return _tarball_install(args, path)
    _print_error(_("unsupported file type: %s") % path.name)
    return 1


def _flatpak_app_id(app) -> str:
    from gi.repository import GLib

    from . import desktop

    try:
        kf = desktop.load_keyfile(app.entry.path)
        return kf.get_string("Desktop Entry", "X-Flatpak")
    except (GLib.Error, OSError):
        return ""


def _cmd_remove(args) -> int:
    from . import apps

    app = apps.get_app(args.desktop_id)
    if app is None:
        _print_error(_("no application with desktop ID %s") % args.desktop_id)
        return 1
    if not args.yes and sys.stdin.isatty():
        if not _confirm(_("Remove %s? [y/N] ") % app.display_name):
            print(_("Aborted"))
            return 1
    try:
        if app.kind == "flatpak":
            from .backends import flatpak

            app_id = app.source_ref or _flatpak_app_id(app)
            if not app_id:
                _print_error(_("the Flatpak application ID is unknown"))
                return 1
            flatpak.uninstall(app_id, app.scope, progress=_stream)
        elif app.kind == "appimage":
            from .backends import appimage

            appimage.uninstall(app)
        elif app.kind == "tarball":
            from .backends import tarball

            tarball.uninstall(app)
        elif app.kind == "deb":
            from .backends import deb

            package = app.source_ref or deb.package_for_desktop(Path(app.entry.path))
            if not package:
                _print_error(_("the Debian package name is unknown"))
                return 1
            deb.remove(package, args.purge, progress=_stream)
        else:
            _print_error(
                _("removal is not supported for %s entries")
                % apps.kind_label(app.kind)
            )
            return 1
    except Exception as exc:  # noqa: BLE001
        _report_exception(exc)
        return 1
    print(_("removed %s") % args.desktop_id)
    return 0


def _run_gui(files: list[str]) -> int:
    if not _has_display():
        _print_error(_("no graphical display; use a CLI subcommand"))
        return 1
    from .ui.application import main as gui_main

    return gui_main(files or None)


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    config.ensure_dirs()
    db.init()
    setup_i18n(db.setting("language"))
    if args_list and not args_list[0].startswith("-") and args_list[0] not in SUBCOMMANDS:
        return _run_gui(args_list)

    parser = build_parser()
    args = parser.parse_args(args_list)

    if args.command is None:
        return _run_gui([])
    handlers = {
        "list": _cmd_list,
        "install": _cmd_install,
        "remove": _cmd_remove,
        "fix-icons": _cmd_fix_icons,
        "scan": _cmd_scan,
        "managers": _cmd_managers,
        "search": _cmd_search,
    }
    try:
        return handlers[args.command](args)
    except BrokenPipeError:
        # `appdeb list | head` closes the pipe early; exit quietly.
        try:
            sys.stdout.close()
        finally:
            os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
