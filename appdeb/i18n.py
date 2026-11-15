"""Gettext internationalisation for AppDeb.

Catalogue locations
-------------------
Compiled catalogues live at ``appdeb/locale/<code>/LC_MESSAGES/appdeb.mo`` in
source checkouts and at ``/usr/share/locale/<code>/LC_MESSAGES/appdeb.mo`` when
AppDeb is installed as a package.  :func:`locale_dir` prefers the in-package
locale directory whenever it holds a catalogue and otherwise falls back to the
system locale directory, so both layouts work without configuration.
"""

from __future__ import annotations

import gettext
import os
from pathlib import Path

DOMAIN = "appdeb"
LANGUAGES = [
    ("system", "System"),
    ("en", "English"),
    ("tr", "Türkçe"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("zh_CN", "中文（简体）"),
    ("es", "Español"),
    ("ko", "한국어"),
    ("pl", "Polski"),
    ("sv", "Svenska"),
    ("it", "Italiano"),
    ("az", "Azərbaycanca"),
    ("da", "Dansk"),
]

_TRANSLATION: gettext.NullTranslations | None = None


def locale_dir() -> Path:
    """Return the directory that holds the AppDeb catalogues."""
    package = Path(__file__).resolve().parent / "locale"
    if package.is_dir():
        for pattern in ("*.mo", "*.po", "*/*/*.mo", "*/*/*.po"):
            if any(package.glob(pattern)):
                return package
    system = Path("/usr/share/locale")
    if system.is_dir():
        return system
    return package


def setup(language: str | None = None) -> str:
    """Bind the AppDeb domain and return the active language code.

    ``"system"`` (and ``None``) follow the locale environment.  Any other value
    is exported as ``LANGUAGE`` so every gettext call in the process uses it.
    Missing catalogues are tolerated and fall back to the untranslated source
    strings.
    """
    global _TRANSLATION
    code = (language or "system").strip() or "system"
    if code in ("system", "C", "POSIX"):
        code = "system"
        languages = None
    else:
        os.environ["LANGUAGE"] = code
        languages = [code]

    directory = str(locale_dir())
    gettext.bindtextdomain(DOMAIN, directory)
    gettext.textdomain(DOMAIN)
    try:
        translation = gettext.translation(
            DOMAIN,
            localedir=directory,
            languages=languages,
            fallback=True,
        )
    except Exception:  # noqa: BLE001 - a broken locale must never crash AppDeb
        translation = gettext.NullTranslations()
    _TRANSLATION = translation
    translation.install()
    return code


def _(message: str) -> str:
    if _TRANSLATION is None:
        return gettext.dgettext(DOMAIN, message)
    return _TRANSLATION.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    if _TRANSLATION is None:
        return gettext.dngettext(DOMAIN, singular, plural, n)
    return _TRANSLATION.ngettext(singular, plural, n)


def N_(message: str) -> str:
    """Mark a string for extraction without translating it yet."""
    return message


def language_name(code: str) -> str:
    for value, label in LANGUAGES:
        if value == code:
            return label
    return code or "System"
