from __future__ import annotations

import os
import sqlite3
from pathlib import Path

FIXTURES_DIR = Path(
    os.environ.get("FIXTURES_DIR", Path(__file__).resolve().parents[2] / "fixtures")
)


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


def db_path() -> Path:
    return data_dir() / "app.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]
