"""SQLite connection helper.

Single function `connect(repo_path)` that:
  - resolves `<repo>/.jarvis_graph/index.db`
  - creates parent dirs
  - applies the schema if missing
  - records SCHEMA_VERSION in `meta`
  - enables foreign keys + WAL for low-latency reads
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jarvis_graph.schema import DDL, SCHEMA_VERSION
from jarvis_graph.utils import repo_data_dir


def db_path(repo_path: Path) -> Path:
    return repo_data_dir(repo_path) / "index.db"


def connect(repo_path: Path) -> sqlite3.Connection:
    p = db_path(repo_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(DDL)
    cur = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()
    return conn
