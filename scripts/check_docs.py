#!/usr/bin/env python3
"""Check that every relative link and image in docs/guide points at a real file."""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINK = re.compile(r"\]\(([^)#]+)(?:#[^)]*)?\)")
ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    guide = ROOT / "docs" / "guide"
    failures: list[str] = []
    checked = 0
    for page in sorted(guide.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        for target in LINK.findall(text):
            if "://" in target or target.startswith("mailto:"):
                continue
            checked += 1
            resolved = (page.parent / target).resolve()
            if not resolved.exists():
                failures.append(f"{page.name}: missing {target}")
    print(f"checked {checked} relative links in {len(list(guide.glob('*.md')))} pages")
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
