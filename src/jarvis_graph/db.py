"""SQLite connection helper.

Single function `connect(repo_path)` that:
  - resolves `<repo>/.jarvis_graph/index.db`
  - creates parent dirs
  - applies the schema if missing
  - migrates older databases forward
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


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _migrate(conn: sqlite3.Connection, current: int) -> None:
    """Forward-only schema migrations.

    Each step bumps the database from version N → N+1. Migrations must be
    idempotent so a partial run can be replayed cleanly. We never DROP a
    column or rewrite data — additive only.
    """
    # 1 → 2: add complexity + line_count to symbol.
    if current < 2:
        if not _column_exists(conn, "symbol", "complexity"):
            conn.execute("ALTER TABLE symbol ADD COLUMN complexity INTEGER NOT NULL DEFAULT 0")
        if not _column_exists(conn, "symbol", "line_count"):
            conn.execute("ALTER TABLE symbol ADD COLUMN line_count INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_symbol_complexity ON symbol(complexity)"
        )


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
        # Fresh DB: DDL just created the symbol table with current columns,
        # so the v2 column-add path is a no-op but we still need the index.
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        _migrate(conn, 0)
    else:
        current = int(row["value"])
        if current < SCHEMA_VERSION:
            _migrate(conn, current)
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
    conn.commit()
    return conn
