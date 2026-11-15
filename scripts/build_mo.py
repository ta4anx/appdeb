#!/usr/bin/env python3
"""Compile gettext .po catalogues into .mo files without external tools.

Usage:
    python3 scripts/build_mo.py --in-place           # write <locale>/<code>/LC_MESSAGES/appdeb.mo
    python3 scripts/build_mo.py --dest DIR           # write DIR/share/locale/<code>/LC_MESSAGES/appdeb.mo
    python3 scripts/build_mo.py --check              # compile in memory and report untranslated counts

The compiler supports msgctxt, msgid_plural/msgstr[N], fuzzy skipping and the
standard escape sequences, so no gettext package is needed on build machines.
"""

from __future__ import annotations

import argparse
import gettext
import io
import struct
import sys
from pathlib import Path

ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "\\": "\\",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


def unquote(text: str) -> str:
    text = text.strip()
    if len(text) < 2 or not text.startswith('"') or not text.endswith('"'):
        raise ValueError(f"bad PO string: {text!r}")
    body = text[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append(ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


class Entry:
    __slots__ = ("ctxt", "msgid", "plural", "strs", "fuzzy")

    def __init__(self) -> None:
        self.ctxt = ""
        self.msgid = ""
        self.plural = ""
        self.strs: dict[int, str] = {}
        self.fuzzy = False

    def key(self) -> str:
        return f"{self.ctxt}\x04{self.msgid}" if self.ctxt else self.msgid


def parse_po(text: str) -> list[Entry]:
    entries: list[Entry] = []
    current: Entry | None = None
    field: str | None = None
    index = 0
    fuzzy = False

    def start() -> Entry:
        nonlocal current, fuzzy
        current = Entry()
        current.fuzzy = fuzzy
        fuzzy = False
        entries.append(current)
        return current

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            current = None
            field = None
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                fuzzy = True
            continue
        if line.startswith("msgctxt "):
            start().ctxt = unquote(line[len("msgctxt ") :])
            field = "ctxt"
            continue
        if line.startswith("msgid_plural "):
            if current is None:
                start()
            current.plural = unquote(line[len("msgid_plural ") :])
            field = "plural"
            continue
        if line.startswith("msgid "):
            if current is None or current.msgid or current.strs or not current.ctxt:
                current = start()
            current.msgid = unquote(line[len("msgid ") :])
            field = "msgid"
            continue
        if line.startswith("msgstr["):
            if current is None:
                start()
            head, _, rest = line.partition("]")
            index = int(head[len("msgstr[") :])
            current.strs[index] = unquote(rest.strip())
            field = f"str{index}"
            continue
        if line.startswith("msgstr "):
            if current is None:
                start()
            current.strs[0] = unquote(line[len("msgstr ") :])
            field = "str0"
            continue
        if line.startswith('"'):
            if current is None:
                continue
            chunk = unquote(line)
            if field == "ctxt":
                current.ctxt += chunk
            elif field == "msgid":
                current.msgid += chunk
            elif field == "plural":
                current.plural += chunk
            elif field and field.startswith("str"):
                idx = int(field[3:])
                current.strs[idx] = current.strs.get(idx, "") + chunk
            continue
    return entries


def build_mo(entries: list[Entry], *, strict: bool = False) -> tuple[bytes, int, int]:
    translated: list[tuple[str, str]] = []
    untranslated = 0
    for entry in entries:
        if entry.fuzzy:
            continue
        values = [entry.strs.get(i, "") for i in range(len(entry.strs) or 1)]
        if entry.msgid == "":
            # Header entry: metadata lives in msgstr; it is never "untranslated".
            translated.append(("", values[0]))
            continue
        if not any(values):
            untranslated += 1
            if strict:
                raise SystemExit(f"untranslated: {entry.msgid!r}")
            continue
        if entry.plural:
            key = f"{entry.key()}\x00{entry.plural}"
            translated.append((key, "\x00".join(values)))
        else:
            translated.append((entry.key(), values[0]))

    translated.sort(key=lambda item: item[0])
    ids = b""
    strs = b""
    offsets: list[tuple[int, int]] = []
    for key, value in translated:
        index = len(ids)
        key_bytes = key.encode("utf-8")
        ids += key_bytes + b"\x00"
        offsets.append((index, len(strs)))
        strs += value.encode("utf-8") + b"\x00"

    count = len(offsets)
    orig_table = 7 * 4
    trans_table = orig_table + count * 8
    keys_start = trans_table + count * 8
    values_start = keys_start + len(ids)
    header = struct.pack("Iiiiiii", 0x950412DE, 0, count, orig_table, trans_table, 0, 0)
    key_table = b"".join(
        struct.pack("II", len(key.encode("utf-8")), keys_start + start)
        for (start, _), (key, _v) in zip(offsets, translated)
    )
    value_table = b"".join(
        struct.pack("II", len(value.encode("utf-8")), values_start + start)
        for (_k, start), (_key, value) in zip(offsets, translated)
    )
    data = bytearray()
    data += header
    data += key_table
    data += value_table
    data += ids
    data += strs
    return bytes(data), len(translated), untranslated


def verify(data: bytes) -> None:
    catalog = gettext.GNUTranslations(io.BytesIO(data))
    catalog.gettext("AppDeb")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="appdeb/locale")
    parser.add_argument("--dest", default="")
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    po_files = sorted(source.glob("*/LC_MESSAGES/*.po"))
    if not po_files:
        print(f"no .po files under {source}", file=sys.stderr)
        return 1

    failed = 0
    for po in po_files:
        code = po.parent.parent.name
        entries = parse_po(po.read_text(encoding="utf-8"))
        try:
            data, count, untranslated = build_mo(entries, strict=args.strict)
            verify(data)
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            print(f"{code}: FAILED ({exc})", file=sys.stderr)
            failed += 1
            continue
        if args.check:
            if not args.quiet:
                print(f"{code}: {count} translated, {untranslated} untranslated")
            continue
        if args.in_place:
            target = po.parent / po.with_suffix(".mo").name
        elif args.dest:
            target = Path(args.dest) / "share" / "locale" / code / "LC_MESSAGES" / po.with_suffix(".mo").name
        else:
            print("nothing to do: pass --in-place, --dest or --check", file=sys.stderr)
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if not args.quiet:
            print(f"{code}: wrote {target} ({count} messages, {untranslated} untranslated)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
