#!/usr/bin/env python3
"""Verify that AppDeb is registered as a handler for its supported file types.

Run with an XDG_DATA_HOME that contains AppDeb's desktop entry, e.g.::

    XDG_DATA_HOME=/tmp/mimecheck python3 scripts/check_mime.py

The check fails when a supported MIME type does not list
``io.github.ta4anx.AppDeb.desktop`` among its handlers, which is what makes
"Open with AppDeb" appear in file managers.
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

DESKTOP_ID = "io.github.ta4anx.AppDeb.desktop"
SUPPORTED = (
    "application/vnd.appimage",
    "application/vnd.debian.binary-package",
    "application/vnd.flatpak",
    "application/vnd.flatpak.ref",
    "application/x-compressed-tar",
    "application/gzip",
)


def main() -> int:
    failures = 0
    for mime in SUPPORTED:
        handlers = [info.get_id() for info in Gio.AppInfo.get_all_for_type(mime)]
        registered = DESKTOP_ID in handlers
        default = Gio.AppInfo.get_default_for_type(mime, False)
        default_id = default.get_id() if default is not None else None
        state = "ok" if registered else "MISSING"
        print(f"{mime}: {state} (default={default_id})")
        if not registered:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
