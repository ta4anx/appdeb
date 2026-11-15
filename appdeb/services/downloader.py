"""HTTPS downloader with streaming progress, size cap and checksum support."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

from .. import __version__, config
from ..i18n import _

MAX_BYTES = 4 * 1024**3
CHUNK = 64 * 1024
ALLOWED_SUFFIXES = (".AppImage", ".flatpak", ".flatpakref", ".deb", ".tar.gz", ".tgz")


def default_destination(url: str) -> Path:
    name = unquote(Path(urlparse(url).path).name) or "download.bin"
    name = "".join(ch for ch in name if ch.isalnum() or ch in "._-+() ").strip() or "download.bin"
    if not name.lower().endswith(tuple(s.lower() for s in ALLOWED_SUFFIXES)):
        name = name + ".bin" if "." not in name else name
    return config.download_dir() / name


def download(
    url: str,
    dest: Path,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    sha256: str | None = None,
) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(_("only https:// URLs are supported"))
    if not parsed.hostname:
        raise ValueError(_("URL has no host"))

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    with requests.get(
        url,
        stream=True,
        timeout=(10, 60),
        headers={"User-Agent": f"AppDeb/{__version__}"},
    ) as response:
        if response.status_code != 200:
            raise ValueError(_("download failed: HTTP %s") % response.status_code)
        declared = response.headers.get("Content-Length")
        total = int(declared) if declared and declared.isdigit() else 0
        if total > MAX_BYTES:
            raise ValueError(_("download too large"))

        digest = hashlib.sha256()
        written = 0
        try:
            with part.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise ValueError(_("download cancelled"))
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise ValueError(_("download too large"))
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress is not None:
                        progress(written, total)
        except Exception:
            part.unlink(missing_ok=True)
            raise

    if sha256 and digest.hexdigest().lower() != sha256.strip().lower():
        part.unlink(missing_ok=True)
        raise ValueError(_("SHA-256 mismatch"))
    part.replace(dest)
    return dest
