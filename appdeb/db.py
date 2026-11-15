"""SQLite storage for AppDeb's per-app overrides and settings."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
  desktop_id TEXT PRIMARY KEY,
  kind TEXT,
  display_name TEXT,
  original_name TEXT,
  icon_name TEXT,
  original_icon TEXT,
  original_wm_class TEXT,
  version TEXT,
  source_ref TEXT,
  install_path TEXT,
  install_date TEXT,
  last_launched TEXT,
  favorite INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

FIELDS = {
    "kind",
    "display_name",
    "original_name",
    "icon_name",
    "original_icon",
    "original_wm_class",
    "version",
    "source_ref",
    "install_path",
    "install_date",
    "last_launched",
    "favorite",
    "notes",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with closing(connect()) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def get(desktop_id: str) -> dict | None:
    with closing(connect()) as conn, conn:
        row = conn.execute("SELECT * FROM apps WHERE desktop_id = ?", (desktop_id,)).fetchone()
    return dict(row) if row else None


def all_rows() -> dict[str, dict]:
    with closing(connect()) as conn, conn:
        rows = conn.execute("SELECT * FROM apps").fetchall()
    return {row["desktop_id"]: dict(row) for row in rows}


def upsert(desktop_id: str, **fields) -> None:
    values = {k: v for k, v in fields.items() if k in FIELDS}
    if not values:
        values = {}
    existing = get(desktop_id)
    with closing(connect()) as conn, conn:
        if existing is None:
            columns = ["desktop_id", *values.keys()]
            params = [desktop_id, *values.values()]
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO apps ({', '.join(columns)}) VALUES ({placeholders})",
                params,
            )
        elif values:
            assignments = ", ".join(f"{key} = ?" for key in values)
            conn.execute(
                f"UPDATE apps SET {assignments} WHERE desktop_id = ?",
                [*values.values(), desktop_id],
            )


def delete(desktop_id: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("DELETE FROM apps WHERE desktop_id = ?", (desktop_id,))


def setting(key: str, default: str | None = None) -> str | None:
    with closing(connect()) as conn, conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def touch_launched(desktop_id: str) -> None:
    upsert(desktop_id, last_launched=_now())


def ensure_row(desktop_id: str, **fields) -> None:
    if get(desktop_id) is None:
        upsert(desktop_id, **fields)


def db_file() -> Path:
    return config.db_path()
