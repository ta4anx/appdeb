"""Store metadata for installed apps: Flathub, Snap and Homebrew.

The module is deliberately forgiving: a missing network, unknown app or broken
JSON never raises. Callers get ``None`` when no metadata is available.

Flathub responses are cached for 24 hours under
``$XDG_CACHE_HOME/appdeb/metadata``. When the network is unavailable (or
``allow_network=False``), the server-side AppStream catalog shipped by Flatpak
is used instead.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config, desktop
from .backends import brew, snap
from .models import InstalledApp

CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
USER_AGENT = "AppDeb/0.2.0"
_TAG = re.compile(r"<[^>]+>")


@dataclass
class StoreScreenshot:
    url: str
    caption: str = ""
    local_path: Path | None = None


@dataclass
class AppDetails:
    source: str = "none"
    app_id: str = ""
    name: str = ""
    summary: str = ""
    description: str = ""
    developer: str = ""
    homepage: str = ""
    license: str = ""
    version: str = ""
    categories: list[str] = field(default_factory=list)
    screenshots: list[StoreScreenshot] = field(default_factory=list)
    updated: str = ""


def _cache_root() -> Path:
    return config.cache_home() / "appdeb"


def _metadata_dir() -> Path:
    return _cache_root() / "metadata"


def _screenshot_dir() -> Path:
    return _cache_root() / "screenshots"


def _cache_file(app_id: str) -> Path:
    digest = hashlib.sha1(app_id.encode("utf-8")).hexdigest()
    return _metadata_dir() / f"{digest}.json"


def _read_cache(app_id: str) -> dict | None:
    path = _cache_file(app_id)
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_cache(app_id: str, data: dict) -> None:
    path = _cache_file(app_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


# -- HTTP helpers (module level so tests can monkeypatch them) -------------


def _http_get_json(url: str, timeout: float = 8.0, headers: dict[str, str] | None = None) -> dict:
    """Fetch a JSON object over HTTPS. Raises on transport errors."""
    if not url.startswith("https://"):
        return {}
    import requests

    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    response = requests.get(
        url,
        timeout=timeout,
        headers=request_headers,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def _http_get_bytes(url: str, timeout: float = 15.0, limit: int = MAX_SCREENSHOT_BYTES) -> bytes | None:
    """Download at most ``limit`` bytes over HTTPS. Returns ``None`` on error."""
    if not url.startswith("https://"):
        return None
    import requests

    try:
        response = requests.get(
            url,
            timeout=timeout,
            stream=True,
            headers={"User-Agent": USER_AGENT},
        )
        if response.status_code != 200:
            return None
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > limit:
            return None
        data = bytearray()
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                return None
        return bytes(data) if data else None
    except Exception:
        return None


# -- Flathub ---------------------------------------------------------------


def _plain_text(value: str) -> str:
    if not value:
        return ""
    text = html.unescape(_TAG.sub(" ", str(value)))
    return " ".join(text.split())


def _screenshot_url(shot: dict) -> str:
    sizes = shot.get("sizes") or []
    best: tuple[int, str] | None = None
    for entry in sizes:
        if not isinstance(entry, dict):
            continue
        src = str(entry.get("src") or "")
        if not src:
            continue
        try:
            width = int(entry.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        if width >= 500 and (best is None or width < best[0]):
            best = (width, src)
    if best:
        return best[1]
    for entry in sizes:
        if isinstance(entry, dict):
            src = str(entry.get("src") or "")
            if src and "_orig" in src:
                return src
    largest: tuple[int, str] | None = None
    for entry in sizes:
        if not isinstance(entry, dict):
            continue
        src = str(entry.get("src") or "")
        if not src:
            continue
        try:
            width = int(entry.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        if largest is None or width > largest[0]:
            largest = (width, src)
    return largest[1] if largest else ""


def _release_info(data: dict) -> tuple[str, str]:
    releases = data.get("releases") or []
    for release in releases:
        if not isinstance(release, dict):
            continue
        version = str(release.get("version") or "")
        updated = ""
        raw = release.get("timestamp") or release.get("date") or ""
        try:
            timestamp = int(raw)
            updated = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            updated = str(raw)
        if version:
            return version, updated
    return "", ""


def _details_from_flathub(data: dict, app_id: str) -> AppDetails:
    urls = data.get("urls") or {}
    name = str(data.get("name") or app_id)
    version, updated = _release_info(data)
    screenshots: list[StoreScreenshot] = []
    for shot in data.get("screenshots") or []:
        if not isinstance(shot, dict):
            continue
        url = _screenshot_url(shot)
        if url:
            screenshots.append(
                StoreScreenshot(url=url, caption=str(shot.get("caption") or ""))
            )
    return AppDetails(
        source="flatpak",
        app_id=str(data.get("id") or app_id),
        name=name,
        summary=str(data.get("summary") or ""),
        description=_plain_text(data.get("description") or ""),
        developer=str(data.get("developer_name") or ""),
        homepage=str(urls.get("homepage") or ""),
        license=str(data.get("project_license") or data.get("license") or ""),
        version=version,
        categories=[str(item) for item in (data.get("categories") or [])],
        screenshots=screenshots,
        updated=updated,
    )


def _appstream_paths() -> list[Path]:
    root = Path("/var/lib/flatpak/appstream")
    try:
        if not root.is_dir():
            return []
        return sorted(root.glob("*/*/active/appstream.xml.gz"))
    except OSError:
        return []


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _preferred_element(component: ET.Element, tag: str) -> ET.Element | None:
    """Return the untranslated child for ``tag``, else the first one."""
    first: ET.Element | None = None
    for child in component:
        if _local_tag(child.tag) != tag:
            continue
        if first is None:
            first = child
        if not child.get("{http://www.w3.org/XML/1998/namespace}lang"):
            return child
    return first


def _component_text(component: ET.Element, path: str) -> str:
    element = component.find(path)
    if element is None:
        return ""
    return " ".join(" ".join(element.itertext()).split())


def _direct_text(component: ET.Element, tag: str) -> str:
    element = _preferred_element(component, tag)
    if element is None:
        return ""
    return " ".join(" ".join(element.itertext()).split())


def _component_details(component: ET.Element, app_id: str) -> AppDetails:
    categories: list[str] = []
    categories_element = component.find("categories")
    if categories_element is not None:
        for child in categories_element:
            if _local_tag(child.tag) == "category" and child.text:
                categories.append(child.text.strip())

    screenshots: list[StoreScreenshot] = []
    screenshots_element = component.find("screenshots")
    if screenshots_element is not None:
        for child in screenshots_element:
            if _local_tag(child.tag) != "screenshot":
                continue
            image = child.find("image")
            url = (image.text or "").strip() if image is not None else ""
            if not url:
                continue
            caption = child.findtext("caption") or ""
            screenshots.append(StoreScreenshot(url=url, caption=caption.strip()))

    homepage = ""
    urls_element = component.find("urls")
    if urls_element is not None:
        for child in urls_element:
            if _local_tag(child.tag) == "url" and child.get("type") == "homepage":
                homepage = (child.text or "").strip()
                break

    developer = _direct_text(component, "developer_name")
    if not developer:
        developer = _component_text(component, "developer/name")

    version = ""
    updated = ""
    releases_element = component.find("releases")
    if releases_element is not None:
        for child in releases_element:
            if _local_tag(child.tag) != "release":
                continue
            version = str(child.get("version") or "")
            updated = str(child.get("date") or child.get("timestamp") or "")
            if version:
                break

    name = _direct_text(component, "name")

    return AppDetails(
        source="flatpak",
        app_id=app_id,
        name=name or app_id,
        summary=_direct_text(component, "summary"),
        description=_direct_text(component, "description"),
        developer=developer,
        homepage=homepage,
        license=_direct_text(component, "project_license"),
        version=version,
        categories=categories,
        screenshots=screenshots,
        updated=updated,
    )


def _search_appstream(path: Path, app_id: str, max_components: int = 20000) -> AppDetails | None:
    scanned = 0
    try:
        with gzip.open(path, "rb") as handle:
            for event, element in ET.iterparse(handle, events=("end",)):
                if _local_tag(element.tag) != "component":
                    continue
                scanned += 1
                component_id = ""
                id_element = element.find("id")
                if id_element is not None and id_element.text:
                    component_id = id_element.text.strip()
                if component_id.startswith(app_id):
                    details = _component_details(element, app_id)
                    element.clear()
                    return details
                element.clear()
                if scanned >= max_components:
                    break
    except (OSError, EOFError, ET.ParseError, ValueError):
        return None
    return None


def _offline_flathub(app_id: str) -> AppDetails | None:
    for path in _appstream_paths():
        if not path.is_file():
            continue
        details = _search_appstream(path, app_id)
        if details is not None:
            return details
    return None


def _flathub_details(app_id: str, *, timeout: float, allow_network: bool) -> AppDetails | None:
    if allow_network:
        cached = _read_cache(app_id)
        if cached is not None:
            return _details_from_flathub(cached, app_id)
        url = f"https://flathub.org/api/v2/appstream/{app_id}"
        try:
            data = _http_get_json(url, timeout)
        except Exception:
            data = {}
        if data:
            _write_cache(app_id, data)
            return _details_from_flathub(data, app_id)
    return _offline_flathub(app_id)


# -- Snap Store ------------------------------------------------------------

SNAP_API_FIELDS = "title,summary,description,website,contact,license,publisher,media,store-url"


def _details_from_snap_api(data: dict, name: str) -> AppDetails | None:
    snap_data = data.get("snap")
    if not isinstance(snap_data, dict):
        snap_data = data
    if not snap_data:
        return None
    publisher = snap_data.get("publisher")
    developer = ""
    if isinstance(publisher, dict):
        developer = str(publisher.get("display-name") or publisher.get("username") or "")
    screenshots: list[StoreScreenshot] = []
    for media in snap_data.get("media") or []:
        if not isinstance(media, dict) or media.get("type") != "screenshot":
            continue
        url = str(media.get("url") or "")
        if url.startswith("https://"):
            screenshots.append(StoreScreenshot(url=url, caption=""))
    return AppDetails(
        source="snap",
        app_id=str(snap_data.get("name") or name),
        name=str(snap_data.get("title") or snap_data.get("name") or name),
        summary=str(snap_data.get("summary") or ""),
        description=str(snap_data.get("description") or ""),
        developer=developer,
        homepage=str(snap_data.get("website") or snap_data.get("store-url") or ""),
        license=str(snap_data.get("license") or ""),
        version=str(snap_data.get("version") or ""),
        screenshots=screenshots,
    )


def _snap_details(name: str, *, timeout: float, allow_network: bool) -> AppDetails | None:
    cache_key = f"snap:{name}"
    if allow_network:
        cached = _read_cache(cache_key)
        if cached is not None:
            return _details_from_snap_api(cached, name)
        url = f"https://api.snapcraft.io/v2/snaps/info/{name}?fields={SNAP_API_FIELDS}"
        try:
            data = _http_get_json(url, timeout, headers={"Snap-Device-Series": "16"})
        except Exception:
            data = {}
        if data:
            _write_cache(cache_key, data)
            details = _details_from_snap_api(data, name)
            if details is not None:
                if not details.version:
                    cli_meta = snap.info(name)
                    if cli_meta:
                        details.version = cli_meta.get("version", "")
                        details.summary = details.summary or cli_meta.get("summary", "")
                        details.homepage = details.homepage or cli_meta.get("store_url", "")
                return details

    meta = snap.info(name)
    if not meta:
        return None
    return AppDetails(
        source="snap",
        app_id=name,
        name=meta.get("name") or name,
        summary=meta.get("summary", ""),
        description=meta.get("description", ""),
        developer=meta.get("publisher", ""),
        homepage=meta.get("store_url", ""),
        license=meta.get("license", ""),
        version=meta.get("version", ""),
    )


# -- Dispatch --------------------------------------------------------------


def _looks_like_app_id(value: str) -> bool:
    if not value or "/" in value or " " in value:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", value))


def _desktop_stem(app: InstalledApp) -> str:
    app_id = app.entry.id or ""
    return app_id[:-8] if app_id.endswith(".desktop") else app_id


def _key(app: InstalledApp, name: str) -> str:
    try:
        keyfile = desktop.load_keyfile(app.entry.path)
    except Exception:
        return ""
    return desktop._get(keyfile, name)


def _minimal_from_url(app: InstalledApp, store_url: str) -> AppDetails:
    return AppDetails(
        source=app.kind or "appimage",
        app_id=_desktop_stem(app),
        name=app.display_name,
        summary=app.entry.comment,
        description=app.entry.comment,
        homepage=store_url,
        version=app.version,
        categories=list(app.entry.categories),
    )


def details(
    app: InstalledApp,
    *,
    timeout: float = 8.0,
    allow_network: bool = True,
) -> AppDetails | None:
    kind = (app.kind or "").lower()
    stem = _desktop_stem(app)
    source_ref = (app.source_ref or "").strip()
    flatpak_id = _key(app, "X-Flatpak")
    snap_name = _key(app, "X-SnapInstanceName")
    store_url = _key(app, "X-Appdeb-Store-Url")

    try:
        if kind == "flatpak" or flatpak_id or app.entry.source == "flatpak":
            app_id = flatpak_id or (source_ref if _looks_like_app_id(source_ref) else "") or stem
            if not app_id:
                return None
            return _flathub_details(app_id, timeout=timeout, allow_network=allow_network)

        if kind == "snap" or snap_name:
            name = snap_name or source_ref or stem.split("_")[0]
            if not name:
                return None
            return _snap_details(name, timeout=timeout, allow_network=allow_network)

        if kind == "brew":
            name = source_ref or stem
            meta = brew.info(name)
            if not meta:
                return None
            return AppDetails(
                source="brew",
                app_id=name,
                name=meta.get("name") or name,
                summary=meta.get("description", ""),
                description=meta.get("caveats", ""),
                homepage=meta.get("homepage", ""),
                version=meta.get("version", ""),
            )

        if kind in ("appimage", "tarball"):
            if store_url:
                return _minimal_from_url(app, store_url)
            return None

        if store_url:
            return _minimal_from_url(app, store_url)

        if _looks_like_app_id(source_ref) and kind not in ("deb",):
            return _flathub_details(source_ref, timeout=timeout, allow_network=allow_network)
    except Exception:
        return None
    return None


def download_screenshot(
    app_id: str,
    shot: StoreScreenshot,
    *,
    timeout: float = 15.0,
) -> Path | None:
    if shot.local_path:
        try:
            local = Path(shot.local_path)
            if local.is_file() and local.stat().st_size > 0:
                return local
        except OSError:
            pass
    url = shot.url or ""
    if not url.startswith("https://"):
        return None
    directory = _screenshot_dir()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    path = directory / f"{hashlib.sha1(app_id.encode('utf-8')).hexdigest()}-{digest}.png"
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path
    except OSError:
        pass
    try:
        data = _http_get_bytes(url, timeout, MAX_SCREENSHOT_BYTES)
    except Exception:
        return None
    if not data:
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        temporary.write_bytes(data)
        temporary.replace(path)
    except OSError:
        return None
    return path


def clear_cache() -> None:
    for directory in (_metadata_dir(), _screenshot_dir()):
        shutil.rmtree(directory, ignore_errors=True)
