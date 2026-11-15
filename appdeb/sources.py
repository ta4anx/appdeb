"""Find equivalent packages in other managers and switch an app's source.

The UI uses this for "available from Snap/Brew/Flatpak" suggestions on the
details page and for moving an installed app between package managers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from . import apps, desktop
from .backends import appimage, brew, deb, flatpak, snap, tarball
from .backends.runner import BackendError
from .i18n import _
from .models import InstalledApp

KINDS = ("flatpak", "snap", "brew")
_MAX_PER_KIND = 12
_TLDS = {"org", "com", "io", "net", "dev", "app", "github", "gitlab", "flathub"}
_TAIL_NOISE = {"client", "desktop", "app", "application", "gui", "linux"}


@dataclass
class SourceCandidate:
    kind: str
    ref: str
    name: str
    version: str = ""
    origin: str = ""
    summary: str = ""


def _desktop_stem(app: InstalledApp) -> str:
    app_id = app.entry.id or ""
    return app_id[:-8] if app_id.endswith(".desktop") else app_id


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _loose(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")


def _key(app: InstalledApp, name: str) -> str:
    try:
        return desktop._get(desktop.load_keyfile(app.entry.path), name)
    except Exception:
        return ""


def _search_terms(app: InstalledApp) -> list[str]:
    raw = _desktop_stem(app)
    segments = [part for part in re.split(r"[._\s]+", raw) if part]
    while len(segments) > 1 and segments[0].lower() in _TLDS:
        segments.pop(0)
    while len(segments) > 1 and segments[-1].lower() in _TAIL_NOISE:
        segments.pop()

    terms: list[str] = [app.display_name]
    if segments:
        terms.append(segments[-1])
    if len(segments) >= 2:
        terms.append("-".join(segments[-2:]))
    if " " in app.display_name:
        terms.append(app.display_name.replace(" ", "-"))

    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clean = term.strip()
        if len(clean) < 2:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(clean)
        if len(unique) == 3:
            break
    return unique


def _current_ref(app: InstalledApp) -> str:
    kind = (app.kind or "").lower()
    stem = _desktop_stem(app)
    if kind == "flatpak":
        return _key(app, "X-Flatpak") or app.source_ref or stem
    if kind == "snap":
        return _key(app, "X-SnapInstanceName") or app.source_ref or stem.split("_")[0]
    if kind == "brew":
        return app.source_ref or stem
    return app.source_ref or stem


def _app_tokens(app: InstalledApp) -> set[str]:
    stem = _desktop_stem(app)
    reference = _current_ref(app) or stem
    app_ids = {stem, reference}
    if "_" in stem:
        app_ids.add(stem.split("_", 1)[0])
    if "." in reference:
        app_ids.add(reference.rsplit(".", 1)[-1])

    tokens: set[str] = set()
    for app_id in app_ids:
        if not app_id:
            continue
        tokens.add(app_id.casefold())
        tokens.add(_compact(app_id))
        tokens.add(_loose(app_id))
        segments = [part for part in app_id.split(".") if part]
        if segments:
            tokens.add(segments[-1].casefold())
            tokens.add(_compact(segments[-1]))
            tokens.add(_loose(segments[-1]))
            stripped = segments[1:] if len(segments) > 1 and segments[0].casefold() in _TLDS else segments
            if stripped:
                tokens.add(_loose("-".join(stripped)))
                tokens.add(_loose(".".join(stripped)))
                tokens.add(_compact("".join(stripped)))

    for name in (app.display_name, getattr(app.entry, "name", "")):
        if not name:
            continue
        tokens.add(name.casefold())
        tokens.add(_compact(name))
        tokens.add(_loose(name))

    return {token for token in tokens if len(token) >= 2}


def _matches_candidate(candidate: "SourceCandidate", tokens: set[str]) -> bool:
    values = [value for value in (candidate.ref, candidate.name) if value]
    if not values:
        return False
    for value in values:
        normalized = {value.casefold(), _compact(value), _loose(value)}
        if normalized & tokens:
            return True
    for value in values:
        lowered = value.casefold()
        for token in tokens:
            if lowered.startswith((token + "-", token + "_", token + ".")):
                return True
    return False


def _installed_ids() -> set[str]:
    try:
        return {app.desktop_id for app in apps.scan_installed(include_hidden=True)}
    except Exception:
        return set()


def _already_installed(kind: str, ref: str, ids: set[str]) -> bool:
    if not ids:
        return False
    if kind == "flatpak":
        return f"{ref}.desktop" in ids
    if kind == "snap":
        return any(desktop_id == f"{ref}.desktop" or desktop_id.startswith(f"{ref}_") for desktop_id in ids)
    return f"{ref}.desktop" in ids


def _mark(candidate: SourceCandidate, installed: bool) -> str:
    if not installed:
        return candidate.summary
    if candidate.summary:
        return f"{candidate.summary} · already installed"
    return "already installed"


def _snap_summary(name: str, cache: dict[str, str]) -> str:
    if name in cache:
        return cache[name]
    try:
        summary = snap.info(name).get("summary", "")
    except Exception:
        summary = ""
    cache[name] = summary
    return summary


def _candidates_for(kind: str, term: str) -> list[SourceCandidate]:
    if kind == "flatpak":
        return [
            SourceCandidate(
                kind="flatpak",
                ref=result.application,
                name=result.name or result.application,
                version=result.version,
                origin=result.remotes,
                summary=result.description,
            )
            for result in flatpak.search(term)
        ]
    if kind == "snap":
        return [
            SourceCandidate(
                kind="snap",
                ref=package.name,
                name=package.name,
                version=package.version,
                origin=package.publisher,
            )
            for package in snap.search(term)
        ]
    if kind == "brew":
        return [
            SourceCandidate(
                kind="brew",
                ref=result.name,
                name=result.name,
                version=result.version,
                origin="Homebrew",
                summary=result.description,
            )
            for result in brew.search(term)
        ]
    return []


class _DefaultKinds(tuple):
    """Default ``include`` marker: distinct from an explicit ``KINDS`` tuple."""


_DEFAULT_INCLUDE = _DefaultKinds(KINDS)


def find_sources(
    app: InstalledApp,
    *,
    include: Iterable[str] = _DEFAULT_INCLUDE,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> list[SourceCandidate]:
    current_kind = (app.kind or "").lower()
    enabled = [kind for kind in include if kind in KINDS]
    if include is _DEFAULT_INCLUDE and current_kind:
        enabled = [kind for kind in enabled if kind != current_kind]
    if not enabled:
        return []
    managers = {"flatpak": flatpak.available(), "snap": snap.available(), "brew": brew.available()}
    enabled = [kind for kind in enabled if managers.get(kind)]
    if not enabled:
        return []

    current_ref = _current_ref(app)
    ids = _installed_ids()
    tokens = _app_tokens(app)

    results: list[SourceCandidate] = []
    seen: set[tuple[str, str]] = set()
    snap_summaries: dict[str, str] = {}
    per_kind: dict[str, int] = {kind: 0 for kind in enabled}

    for term in _search_terms(app):
        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            break
        for kind in enabled:
            if per_kind[kind] >= _MAX_PER_KIND:
                continue
            if progress is not None:
                progress(_("Searching %s for %s") % (kind, term))
            try:
                found = _candidates_for(kind, term)
            except (BackendError, OSError):
                continue
            for candidate in found:
                if per_kind[kind] >= _MAX_PER_KIND:
                    break
                key = (candidate.kind, candidate.ref)
                if key in seen:
                    continue
                if kind == current_kind and candidate.ref == current_ref:
                    continue
                if not _matches_candidate(candidate, tokens):
                    continue
                seen.add(key)
                if kind == "snap" and not candidate.summary:
                    candidate.summary = _snap_summary(candidate.ref, snap_summaries)
                candidate.summary = _mark(candidate, _already_installed(kind, candidate.ref, ids))
                results.append(candidate)
                per_kind[kind] += 1
    return results


def install_candidate(
    cand: SourceCandidate,
    *,
    scope: str = "user",
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    if cand.kind == "flatpak":
        flatpak.install(cand.ref, scope, progress=progress, cancel=cancel)
        return
    if cand.kind == "snap":
        snap.install(cand.ref, progress=progress, cancel=cancel)
        return
    if cand.kind == "brew":
        brew.install(cand.ref, progress=progress, cancel=cancel)
        return
    raise BackendError(_("unknown source kind: %s") % cand.kind)


def uninstall_kind(
    app: InstalledApp,
    *,
    scope: str | None = None,
    progress: Callable[[str], None] | None = None,
    cancel=None,
) -> None:
    kind = (app.kind or "").lower()
    if kind == "flatpak":
        app_id = _key(app, "X-Flatpak") or app.source_ref or _desktop_stem(app)
        flatpak.uninstall(app_id, scope or app.scope or "user", progress=progress, cancel=cancel)
        return
    if kind == "snap":
        name = _key(app, "X-SnapInstanceName") or app.source_ref or _desktop_stem(app).split("_")[0]
        snap.uninstall(name, progress=progress, cancel=cancel)
        return
    if kind == "brew":
        brew.uninstall(app.source_ref or _desktop_stem(app), progress=progress, cancel=cancel)
        return
    if kind == "appimage":
        appimage.uninstall(app)
        return
    if kind == "tarball":
        tarball.uninstall(app)
        return
    if kind == "deb":
        package = deb.package_for_desktop(app.entry.path)
        if not package:
            raise BackendError(_("no package found for desktop entry"))
        deb.remove(package, False, progress=progress, cancel=cancel)
        return
    raise BackendError(
        _("no uninstall backend for kind: %s") % (kind or _("unknown"))
    )
