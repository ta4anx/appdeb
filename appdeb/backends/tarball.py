"""Portable ``.tar.gz`` backend with safe extraction and one-click installs."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from .. import config, db, desktop, icons  # noqa: E402
from ..models import InstalledApp, TarballMetadata  # noqa: E402
from .runner import BackendError  # noqa: E402

GROUP = "Desktop Entry"
MAX_MEMBERS = 20_000
MAX_TOTAL_BYTES = 4 * 1024**3
MIN_FALLBACK_EXECUTABLE = 100 * 1024
TARBALL_SUFFIXES = (".tar.gz", ".tgz")
SKIPPED_DIRS = {"lib", "lib64", "resources"}
ICON_SUFFIXES = (".png", ".svg", ".xpm")


def _get(kf: GLib.KeyFile, key: str) -> str:
    try:
        return kf.get_string(GROUP, key) or ""
    except GLib.Error:
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]
    return value or "tarball"


def _sanitize_categories(text: str) -> str:
    tokens: list[str] = []
    for token in (text or "").replace(",", ";").split(";"):
        token = token.strip()
        if not token or not re.fullmatch(r"[A-Za-z0-9+.-]+", token):
            continue
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        tokens = ["Utility"]
    return ";".join(tokens) + ";"


def _exec_value(path: Path) -> str:
    text = str(path)
    if any(character.isspace() for character in text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise BackendError("cancelled")


def is_tarball(path: Path) -> bool:
    path = Path(path)
    name = path.name.lower()
    if not name.endswith(TARBALL_SUFFIXES):
        return False
    try:
        return tarfile.is_tarfile(path)
    except (OSError, tarfile.TarError):
        return False


def _unsafe(member: str) -> BackendError:
    return BackendError(f"unsafe archive: {member}")


def _validate_members(members: list[tarfile.TarInfo]) -> None:
    if len(members) > MAX_MEMBERS:
        raise BackendError("unsafe archive: too many members")
    total = 0
    for member in members:
        total += max(member.size, 0)
        if total > MAX_TOTAL_BYTES:
            raise BackendError("unsafe archive: archive is larger than 4 GiB")
        name = member.name
        normalized = os.path.normpath(name)
        if not name or name.startswith("/") or os.path.isabs(name):
            raise _unsafe(name)
        if normalized == ".." or normalized.startswith("../") or normalized.startswith(".." + os.sep):
            raise _unsafe(name)
        if member.isdev() or member.isfifo():
            raise _unsafe(name)
        if member.issym():
            link = member.linkname
            if os.path.isabs(link):
                raise _unsafe(name)
            resolved = os.path.normpath(os.path.join(os.path.dirname(normalized), link))
            if resolved == ".." or resolved.startswith("../"):
                raise _unsafe(name)
        if member.islnk():
            link = member.linkname
            if os.path.isabs(link):
                raise _unsafe(name)
            link_normalized = os.path.normpath(link)
            if link_normalized == ".." or link_normalized.startswith("../"):
                raise _unsafe(name)


def _assert_contained(destination: Path) -> None:
    root = destination.resolve()
    for dirpath, dirnames, filenames in os.walk(destination):
        for name in list(dirnames) + list(filenames):
            candidate = Path(dirpath) / name
            if candidate.is_symlink():
                continue
            try:
                candidate.resolve().relative_to(root)
            except (OSError, ValueError):
                raise _unsafe(str(candidate)) from None


def _extract(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(source, "r:gz") as archive:
            members = archive.getmembers()
            _validate_members(members)
            if hasattr(tarfile, "data_filter"):
                archive.extractall(destination, filter="data")
            else:
                archive.extractall(destination)
        _assert_contained(destination)
    except BackendError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    except (tarfile.TarError, OSError, ValueError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise BackendError(f"unsafe archive: {exc}") from exc


def _content_root(extracted: Path) -> Path:
    try:
        entries = list(extracted.iterdir())
    except OSError:
        return extracted
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
        return entries[0]
    return extracted


def _archive_stem(source: Path) -> str:
    name = source.name
    for suffix in (".tar.gz", ".tgz"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return source.stem


def _name_from_stem(source: Path) -> str:
    stem = _archive_stem(source)
    patterns = (
        r"-[0-9]+(?:\.[0-9]+)*$",
        r"_x86_64$",
        r"-linux-.*$",
        r"-amd64$",
    )
    cleaned = stem
    for _ in range(6):
        before = cleaned
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        if cleaned == before:
            break
    cleaned = cleaned.strip(" ._-")
    return cleaned or "Application"


def _version_from_stem(source: Path) -> str:
    matches = re.findall(r"(?:^|[-_])v?([0-9]+(?:\.[0-9]+)+)", _archive_stem(source))
    return matches[-1] if matches else ""


def _find_desktop(root: Path) -> Path | None:
    candidates: list[Path] = []
    try:
        candidates.extend(
            sorted(
                child
                for child in root.iterdir()
                if child.is_file() and child.suffix.lower() == ".desktop"
            )
        )
    except OSError:
        pass
    share = root / "share" / "applications"
    if share.is_dir():
        try:
            candidates.extend(sorted(share.glob("*.desktop")))
        except OSError:
            pass
    for candidate in candidates:
        kf = desktop.load_keyfile(candidate)
        if _get(kf, "Name"):
            return candidate
    return None


def _is_executable(path: Path, root: Path) -> bool:
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
    except OSError:
        return False
    name = path.name
    lowered = name.lower()
    if lowered.endswith((".so", ".py", ".pyc")) or ".so." in lowered:
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if any(part in SKIPPED_DIRS for part in relative.parts[:-1]):
        return False
    return True


def _executable_candidates(root: Path) -> list[str]:
    directories = [root, root / "bin", root / "usr" / "bin"]
    seen: set[str] = set()
    found: list[str] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        try:
            children = sorted(directory.iterdir())
        except OSError:
            continue
        for child in children:
            if not _is_executable(child, root):
                continue
            relative = child.relative_to(root).as_posix()
            if relative not in seen:
                seen.add(relative)
                found.append(relative)
    return found


def _name_variants(name: str) -> set[str]:
    lowered = name.lower()
    variants = {
        lowered,
        lowered.replace(" ", ""),
        re.sub(r"[^a-z0-9]+", "-", lowered).strip("-"),
        re.sub(r"[^a-z0-9]+", "_", lowered).strip("_"),
    }
    return {variant for variant in variants if variant}


def _exec_tokens(exec_line: str) -> list[str]:
    text = (exec_line or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _resolve_desktop_exec(root: Path, exec_line: str) -> str:
    tokens = _exec_tokens(exec_line)
    if not tokens:
        return ""
    first = tokens[0]
    candidates = _executable_candidates(root)
    if os.path.isabs(first):
        try:
            relative = Path(first).relative_to(root).as_posix()
        except ValueError:
            relative = ""
        if relative in candidates:
            return relative
    elif "/" in first:
        relative = os.path.normpath(first)
        if relative.startswith("./"):
            relative = relative[2:]
        if relative in candidates:
            return relative
    else:
        for relative in candidates:
            if Path(relative).name == first:
                return relative
    return ""


def _rank(relative: str, name: str) -> int:
    filename = Path(relative).name
    lowered = filename.lower()
    if filename == "AppRun":
        return 0
    if filename == "run.sh":
        return 1
    variants = _name_variants(name)
    if lowered in variants:
        return 2
    if lowered.endswith(".bin") and Path(lowered).stem in variants:
        return 3
    return 4


def _choose_executable(root: Path, kf: GLib.KeyFile, name: str) -> tuple[str, list[str]]:
    candidates = _executable_candidates(root)
    candidates.sort(key=lambda item: (_rank(item, name), item))
    chosen = _resolve_desktop_exec(root, _get(kf, "Exec"))
    if not chosen:
        for candidate in candidates:
            if _rank(candidate, name) < 4:
                chosen = candidate
                break
    if not chosen:
        largest: Path | None = None
        largest_size = 0
        try:
            top_level = list(root.iterdir())
        except OSError:
            top_level = []
        for child in top_level:
            if not _is_executable(child, root):
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            if size > MIN_FALLBACK_EXECUTABLE and size >= largest_size:
                largest = child
                largest_size = size
        if largest is not None:
            chosen = largest.relative_to(root).as_posix()
    return chosen, candidates


def _find_icon(root: Path, kf: GLib.KeyFile, name: str) -> str:
    icon_value = _get(kf, "Icon")
    if icon_value:
        if os.path.isabs(icon_value):
            try:
                relative = Path(icon_value).relative_to(root)
            except ValueError:
                relative = None
            if relative is not None and (root / relative).is_file():
                return relative.as_posix()
        else:
            direct = root / icon_value
            if direct.is_file() and direct.suffix.lower() in ICON_SUFFIXES:
                return direct.relative_to(root).as_posix()
        stem = Path(icon_value).stem
        for directory in (root, root / "share" / "icons", root / "usr" / "share" / "icons"):
            if not directory.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(directory):
                dirnames[:] = [item for item in dirnames if item not in SKIPPED_DIRS]
                for filename in filenames:
                    candidate = Path(dirpath) / filename
                    if Path(filename).stem == stem and filename.lower().endswith(ICON_SUFFIXES):
                        return candidate.relative_to(root).as_posix()

    dir_icon = root / ".DirIcon"
    if dir_icon.is_symlink():
        target = dir_icon.readlink()
        candidate = (dir_icon.parent / target).resolve()
        try:
            candidate.relative_to(root.resolve())
            if candidate.is_file():
                return candidate.relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            pass
    elif dir_icon.is_file():
        return ".DirIcon"

    images: list[Path] = []
    try:
        images = [
            child
            for child in root.iterdir()
            if child.is_file() and child.suffix.lower() in ICON_SUFFIXES
        ]
    except OSError:
        images = []
    variants = _name_variants(name)
    for image in sorted(images):
        if image.stem.lower() in variants:
            return image.relative_to(root).as_posix()
    if images:
        return sorted(images)[0].relative_to(root).as_posix()
    return ""


def _metadata_from_root(root: Path, source: Path, fallback_name: str = "") -> TarballMetadata:
    desktop_file = _find_desktop(root)
    if desktop_file is None:
        name = fallback_name or _name_from_stem(source)
        empty = GLib.KeyFile()
        executable_rel, candidates = _choose_executable(root, empty, name)
        return TarballMetadata(
            name=name,
            version=_version_from_stem(source),
            executable_rel=executable_rel,
            executable_candidates=candidates,
            icon_rel=_find_icon(root, empty, name),
            categories="Utility;",
        )
    kf = desktop.load_keyfile(desktop_file)
    name = fallback_name or _get(kf, "Name") or _name_from_stem(source)
    executable_rel, candidates = _choose_executable(root, kf, name)
    version = _get(kf, "X-AppImage-Version") or _get(kf, "Version") or _version_from_stem(source)
    return TarballMetadata(
        name=name,
        version=version,
        executable_rel=executable_rel,
        executable_candidates=candidates,
        icon_rel=_find_icon(root, kf, name),
        desktop_rel=desktop_file.relative_to(root).as_posix(),
        comment=_get(kf, "Comment"),
        categories=_get(kf, "Categories") or "Utility;",
        wm_class=_get(kf, "StartupWMClass"),
    )


def _within_root(root: Path, relative: str) -> Path | None:
    if not relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute():
        return None
    normalized = os.path.normpath(relative)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        return None
    path = root / normalized
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return path


def _archive_root(source: Path) -> tuple[Path, tempfile.TemporaryDirectory]:
    temp = tempfile.TemporaryDirectory(prefix="appdeb-inspect-")
    try:
        extracted = Path(temp.name) / "extract"
        _extract(source, extracted)
        return _content_root(extracted), temp
    except BaseException:
        temp.cleanup()
        raise


def inspect(path: Path) -> TarballMetadata:
    source = Path(path)
    if not is_tarball(source):
        raise BackendError("not a .tar.gz archive: " + str(source))
    root, temp = _archive_root(source)
    try:
        return _metadata_from_root(root, source)
    finally:
        temp.cleanup()


def _unique_target(parent: Path, slug: str) -> Path:
    candidate = parent / slug
    counter = 2
    while candidate.exists():
        candidate = parent / f"{slug}-{counter}"
        counter += 1
    return candidate


def _embedded_desktop(root: Path, desktop_rel: str) -> Path | None:
    if desktop_rel:
        candidate = root / desktop_rel
        if candidate.is_file():
            return candidate
    return _find_desktop(root)


def _desktop_exec_arguments(exec_line: str, executable: Path) -> list[str]:
    tokens = _exec_tokens(exec_line)
    if len(tokens) <= 1:
        return []
    first = tokens[0]
    if Path(first).name == executable.name or first in {"AppRun", "run.sh"}:
        return tokens[1:]
    return []


def _write_desktop(
    path: Path,
    *,
    name: str,
    comment: str,
    exec_line: str,
    icon: str,
    categories: str,
    wm_class: str,
    version: str,
    source_name: str,
    install_dir: Path,
    executable_rel: str,
    embedded: Path | None,
) -> None:
    kf = GLib.KeyFile()
    kf.set_string(GROUP, "Type", "Application")
    kf.set_string(GROUP, "Version", "1.5")
    kf.set_string(GROUP, "Name", name)
    if comment:
        kf.set_string(GROUP, "Comment", comment)
    kf.set_string(GROUP, "Exec", exec_line)
    kf.set_string(GROUP, "Path", str(install_dir))
    if icon:
        kf.set_string(GROUP, "Icon", icon)
    kf.set_string(GROUP, "Terminal", "false")
    kf.set_string(GROUP, "Categories", _sanitize_categories(categories))
    kf.set_string(GROUP, "StartupNotify", "true")
    if wm_class:
        kf.set_string(GROUP, "StartupWMClass", wm_class)
    kf.set_string(GROUP, "NoDisplay", "false")
    if embedded is not None:
        embedded_kf = desktop.load_keyfile(embedded)
        mime = _get(embedded_kf, "MimeType")
        if mime:
            kf.set_string(GROUP, "MimeType", mime)
    kf.set_string(GROUP, "X-Appdeb-Managed", "true")
    kf.set_string(GROUP, "X-Appdeb-Kind", "tarball")
    kf.set_string(GROUP, "X-Appdeb-Source", source_name)
    kf.set_string(GROUP, "X-Appdeb-InstallPath", str(install_dir))
    kf.set_string(GROUP, "X-Appdeb-Executable", executable_rel)
    if version:
        kf.set_string(GROUP, "X-Appdeb-Version", version)
    desktop.save_keyfile(kf, path)


def _installed(
    desktop_path: Path,
    *,
    desktop_id: str,
    install_dir: Path,
    source: Path,
    version: str,
) -> InstalledApp:
    entry = desktop.load(desktop_path)
    if entry is None:
        raise BackendError("failed to write desktop entry", str(desktop_path))
    return InstalledApp(
        entry=entry,
        kind="tarball",
        version=version,
        size_bytes=source.stat().st_size if source.exists() else 0,
        scope="user",
        source_ref=str(source),
        install_path=install_dir,
        db=db.get(desktop_id) or {},
    )


def _destination_parent(dest: Path | None) -> Path:
    if dest is None:
        parent = config.tarball_store()
    else:
        parent = Path(dest).expanduser()
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackendError(f"destination is not writable: {parent}") from exc
    if not os.access(parent, os.W_OK | os.X_OK):
        raise BackendError(f"destination is not writable: {parent}")
    return parent


def install_spec(
    source: Path,
    *,
    name: str,
    executable_rel: str,
    icon_source: Path | None,
    dest: Path | None,
    comment: str = "",
    categories: str = "Utility;",
    wm_class: str = "",
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> InstalledApp:
    source = Path(source)
    if not is_tarball(source):
        raise BackendError("not a .tar.gz archive: " + str(source))
    if not executable_rel:
        raise BackendError("no executable found; use install_spec with an executable")
    _check_cancel(cancel)
    config.ensure_dirs()
    db.init()

    display_name = (name or "").strip() or _name_from_stem(source)
    slug = _slug(display_name)
    parent = _destination_parent(dest)
    final_dir = _unique_target(parent, slug)
    temp = Path(tempfile.mkdtemp(prefix=".appdeb-", dir=str(parent)))
    extraction = temp / "extract"
    try:
        _check_cancel(cancel)
        _extract(source, extraction)
        root = _content_root(extraction)
        executable_path = _within_root(root, executable_rel)
        if executable_path is None or not executable_path.is_file():
            raise BackendError(f"executable not found: {executable_rel}")
        metadata = _metadata_from_root(root, source, fallback_name=display_name)
        embedded = _embedded_desktop(root, metadata.desktop_rel)
        embedded_kf = desktop.load_keyfile(embedded) if embedded is not None else GLib.KeyFile()
        if not comment:
            comment = _get(embedded_kf, "Comment")
        if not wm_class:
            wm_class = _get(embedded_kf, "StartupWMClass")
        if categories == "Utility;" and _get(embedded_kf, "Categories"):
            categories = _get(embedded_kf, "Categories")
        os.chmod(executable_path, 0o755)

        try:
            os.replace(root, final_dir)
        except OSError as exc:
            raise BackendError(f"cannot install to {final_dir}: {exc}") from exc
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    installed_executable = final_dir / executable_rel
    os.chmod(installed_executable, 0o755)
    desktop_id = f"appdeb-{final_dir.name}.desktop"
    desktop_path = config.user_applications_dir() / desktop_id

    icon_name = ""
    if icon_source is not None:
        icon_name = icons.install_icon(Path(icon_source), desktop_id)
    elif metadata.icon_rel:
        icon_path = _within_root(final_dir, metadata.icon_rel)
        if icon_path is not None and icon_path.is_file():
            icon_name = icons.install_icon(icon_path, desktop_id)

    embedded_final = _embedded_desktop(final_dir, metadata.desktop_rel)
    args = []
    if embedded_final is not None:
        args = _desktop_exec_arguments(
            _get(desktop.load_keyfile(embedded_final), "Exec"), installed_executable
        )
    exec_line = _exec_value(installed_executable)
    if args:
        exec_line = exec_line + " " + " ".join(args)

    _write_desktop(
        desktop_path,
        name=display_name,
        comment=comment,
        exec_line=exec_line,
        icon=icon_name or "application-x-executable",
        categories=categories,
        wm_class=wm_class,
        version=metadata.version,
        source_name=source.name,
        install_dir=final_dir,
        executable_rel=executable_rel,
        embedded=embedded_final,
    )
    db.upsert(
        desktop_id,
        kind="tarball",
        display_name=display_name,
        icon_name=icon_name or None,
        version=metadata.version,
        source_ref=str(source),
        install_path=str(final_dir),
        install_date=_now(),
    )
    desktop.update_desktop_database()
    if progress is not None:
        try:
            progress(f"installed {display_name}")
        except Exception:  # noqa: BLE001
            pass
    return _installed(
        desktop_path,
        desktop_id=desktop_id,
        install_dir=final_dir,
        source=source,
        version=metadata.version,
    )


def install(
    source: Path,
    *,
    name: str | None = None,
    icon_source: Path | None = None,
    dest: Path | None = None,
    executable_rel: str | None = None,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> InstalledApp:
    source = Path(source)
    metadata = inspect(source)
    chosen_executable = executable_rel or metadata.executable_rel
    if not chosen_executable:
        raise BackendError("no executable found; use install_spec with an executable")
    return install_spec(
        source,
        name=name or metadata.name,
        executable_rel=chosen_executable,
        icon_source=icon_source,
        dest=dest,
        comment=metadata.comment,
        categories=metadata.categories or "Utility;",
        wm_class=metadata.wm_class,
        progress=progress,
        cancel=cancel,
    )


def _install_dir_for(app: InstalledApp) -> Path | None:
    candidates: list[Path] = []
    if app.install_path:
        candidates.append(Path(app.install_path))
    row = app.db or db.get(app.desktop_id) or {}
    if row.get("install_path"):
        candidates.append(Path(row["install_path"]))
    kf = desktop.load_keyfile(app.entry.path)
    install_path = _get(kf, "X-Appdeb-InstallPath")
    if install_path:
        candidates.append(Path(install_path))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def replace(app: InstalledApp, new_source: Path) -> InstalledApp:
    new_source = Path(new_source)
    if not is_tarball(new_source):
        raise BackendError("not a .tar.gz archive: " + str(new_source))
    install_dir = _install_dir_for(app)
    if install_dir is None or not install_dir.is_dir():
        raise BackendError("installed directory not found")
    metadata = inspect(new_source)
    kf = desktop.load_keyfile(app.entry.path)
    stored_executable = _get(kf, "X-Appdeb-Executable")
    backup = Path(str(install_dir) + f".bak-{_timestamp()}")
    shutil.copytree(install_dir, backup)

    temp = Path(tempfile.mkdtemp(prefix=".appdeb-", dir=str(install_dir.parent)))
    extraction = temp / "extract"
    try:
        _extract(new_source, extraction)
        root = _content_root(extraction)
        executable_rel = ""
        stored_path = _within_root(root, stored_executable) if stored_executable else None
        if stored_path is not None and stored_path.is_file():
            executable_rel = stored_executable
        if not executable_rel:
            executable_rel = metadata.executable_rel
        executable_path = _within_root(root, executable_rel)
        if executable_path is None or not executable_path.is_file():
            raise BackendError("no executable found; use install_spec with an executable")
        os.chmod(executable_path, 0o755)
        shutil.rmtree(install_dir)
        try:
            os.replace(root, install_dir)
        except OSError as exc:
            raise BackendError(f"cannot replace {install_dir}: {exc}") from exc
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    installed_executable = install_dir / executable_rel
    os.chmod(installed_executable, 0o755)

    desktop_path = Path(app.entry.path)
    if not desktop_path.exists():
        desktop_path = config.user_applications_dir() / app.desktop_id
    current = desktop.load_keyfile(desktop_path) if desktop_path.exists() else GLib.KeyFile()
    if _get(current, "Name") == "":
        current.set_string(GROUP, "Type", "Application")
        current.set_string(GROUP, "Version", "1.5")
        current.set_string(GROUP, "Name", app.display_name)
        current.set_string(GROUP, "Terminal", "false")
        current.set_string(GROUP, "Categories", _sanitize_categories("Utility;"))
        current.set_string(GROUP, "StartupNotify", "true")
    args = _desktop_exec_arguments(_get(current, "Exec"), installed_executable)
    exec_line = _exec_value(installed_executable)
    if args:
        exec_line = exec_line + " " + " ".join(args)
    current.set_string(GROUP, "Exec", exec_line)
    current.set_string(GROUP, "Path", str(install_dir))
    current.set_string(GROUP, "X-Appdeb-Managed", "true")
    current.set_string(GROUP, "X-Appdeb-Kind", "tarball")
    current.set_string(GROUP, "X-Appdeb-Source", new_source.name)
    current.set_string(GROUP, "X-Appdeb-InstallPath", str(install_dir))
    current.set_string(GROUP, "X-Appdeb-Executable", executable_rel)
    if metadata.version:
        current.set_string(GROUP, "X-Appdeb-Version", metadata.version)
    desktop.save_keyfile(current, desktop_path)

    db.upsert(
        app.desktop_id,
        version=metadata.version,
        source_ref=str(new_source),
        install_path=str(install_dir),
        install_date=_now(),
    )
    desktop.update_desktop_database()
    fresh_entry = desktop.load(desktop_path) or app.entry
    return InstalledApp(
        entry=fresh_entry,
        kind="tarball",
        version=metadata.version,
        size_bytes=new_source.stat().st_size if new_source.exists() else 0,
        scope=app.scope or "user",
        source_ref=str(new_source),
        install_path=install_dir,
        db=db.get(app.desktop_id) or {},
    )


def _is_dangerous(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    forbidden = {
        Path("/"),
        Path.home().resolve(),
        config.tarball_store().resolve(),
    }
    if resolved in forbidden:
        return True
    return len(resolved.parts) <= 2


def uninstall(app: InstalledApp) -> None:
    config.ensure_dirs()
    db.init()
    desktop_id = app.desktop_id
    entry = app.entry
    icon_name = entry.icon
    row = app.db or db.get(desktop_id) or {}
    if not icon_name:
        icon_name = row.get("icon_name") or ""
    if icon_name.startswith("appdeb-"):
        icons.remove_icon(icon_name)

    install_dir = _install_dir_for(app)
    if (
        install_dir is not None
        and install_dir.exists()
        and install_dir.is_dir()
        and not _is_dangerous(install_dir)
    ):
        shutil.rmtree(install_dir, ignore_errors=True)

    try:
        desktop_path = Path(entry.path)
        desktop_path.resolve().relative_to(config.user_applications_dir().resolve())
        if desktop_path.exists():
            desktop_path.unlink()
    except (OSError, ValueError):
        pass

    db.delete(desktop_id)
    desktop.update_desktop_database()
