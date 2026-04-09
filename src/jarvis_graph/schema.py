"""SQLite schema. Single source of truth — db.connect() runs this on creation.

Four tables, deliberately. No JSON columns, no triggers, no views. Indexes
exist only where the four CLI commands actually probe.

Schema versions:
  1 — initial v0.1
  2 — v0.3: added `symbol.complexity` and `symbol.line_count`
"""

from __future__ import annotations

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS file (
    file_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path    TEXT UNIQUE NOT NULL,
    abs_path    TEXT NOT NULL,
    module_path TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    mtime       INTEGER NOT NULL,
    indexed_at  INTEGER NOT NULL,
    parse_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_file_module ON file(module_path);
CREATE INDEX IF NOT EXISTS idx_file_relpath ON file(rel_path);

CREATE TABLE IF NOT EXISTS symbol (
    symbol_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id        INTEGER NOT NULL REFERENCES file(file_id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind           TEXT NOT NULL,
    parent_qname   TEXT,
    lineno         INTEGER NOT NULL,
    end_lineno     INTEGER,
    col            INTEGER,
    docstring      TEXT,
    signature      TEXT,
    is_private     INTEGER NOT NULL DEFAULT 0,
    complexity     INTEGER NOT NULL DEFAULT 0,
    line_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_symbol_name       ON symbol(name);
CREATE INDEX IF NOT EXISTS idx_symbol_qname      ON symbol(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbol_file       ON symbol(file_id);
CREATE INDEX IF NOT EXISTS idx_symbol_kind       ON symbol(kind);
-- idx_symbol_complexity is created post-migration in db._migrate, so older
-- DBs that haven't been upgraded yet don't fail to open here.

CREATE TABLE IF NOT EXISTS import_edge (
    edge_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id          INTEGER NOT NULL REFERENCES file(file_id) ON DELETE CASCADE,
    imported_module  TEXT NOT NULL,
    imported_name    TEXT,
    alias            TEXT,
    lineno           INTEGER NOT NULL,
    resolved_file_id INTEGER REFERENCES file(file_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_import_file     ON import_edge(file_id);
CREATE INDEX IF NOT EXISTS idx_import_module   ON import_edge(imported_module);
CREATE INDEX IF NOT EXISTS idx_import_resolved ON import_edge(resolved_file_id);

CREATE TABLE IF NOT EXISTS call_edge (
    edge_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_symbol_id   INTEGER NOT NULL REFERENCES symbol(symbol_id) ON DELETE CASCADE,
    callee_name        TEXT NOT NULL,
    resolved_symbol_id INTEGER REFERENCES symbol(symbol_id) ON DELETE SET NULL,
    lineno             INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_call_caller   ON call_edge(caller_symbol_id);
CREATE INDEX IF NOT EXISTS idx_call_callee   ON call_edge(callee_name);
CREATE INDEX IF NOT EXISTS idx_call_resolved ON call_edge(resolved_symbol_id);
"""
