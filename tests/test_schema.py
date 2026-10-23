"""Behaviour tests for jarvis_graph.schema, exercised through db.connect.

The DDL is the single source of truth, so these tests pin the resulting
database shape: tables, columns, defaults, unique constraints, foreign-key
actions and indexes. A schema drift breaks at least one assertion.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from conftest import ROOT  # noqa: F401  ensures src/ is on sys.path

from jarvis_graph.db import connect
from jarvis_graph.schema import SCHEMA_VERSION

EXPECTED_TABLES = {"meta", "file", "symbol", "import_edge", "call_edge"}

EXPECTED_SYMBOL_COLUMNS = {
    "symbol_id",
    "file_id",
    "name",
    "qualified_name",
    "kind",
    "parent_qname",
    "lineno",
    "end_lineno",
    "col",
    "docstring",
    "signature",
    "is_private",
    "complexity",
    "line_count",
}

EXPECTED_FILE_COLUMNS = {
    "file_id",
    "rel_path",
    "abs_path",
    "module_path",
    "sha256",
    "size_bytes",
    "mtime",
    "indexed_at",
    "parse_error",
}

EXPECTED_INDEXES = {
    "idx_file_module",
    "idx_file_relpath",
    "idx_symbol_name",
    "idx_symbol_qname",
    "idx_symbol_file",
    "idx_symbol_kind",
    "idx_symbol_complexity",
    "idx_import_file",
    "idx_import_module",
    "idx_import_resolved",
    "idx_call_caller",
    "idx_call_callee",
    "idx_call_resolved",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows} - {"sqlite_sequence"}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
    ).fetchall()
    return {r["name"] for r in rows}


def _insert_file(conn: sqlite3.Connection, rel_path: str) -> int:
    cur = conn.execute(
        "INSERT INTO file (rel_path, abs_path, module_path, sha256, size_bytes,"
        " mtime, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rel_path, f"/abs/{rel_path}", "m." + rel_path.replace("/", ".")[:-3], "hash", 1, 1, 1),
    )
    return cur.lastrowid


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="jgl_schema_test_"))
        # Bare repo dir: connect() must create .jarvis_graph itself.
        self.repo = self._tmp / "repo"
        self.repo.mkdir()
        self.conn = connect(self.repo)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_connect_creates_all_tables(self) -> None:
        self.assertLessEqual(EXPECTED_TABLES, _tables(self.conn))

    def test_connect_creates_dir_and_db_file(self) -> None:
        self.assertTrue((self.repo / ".jarvis_graph" / "index.db").exists())

    def test_fresh_db_records_current_schema_version(self) -> None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["value"], str(SCHEMA_VERSION))

    def test_symbol_columns_exactly_pinned(self) -> None:
        self.assertEqual(_columns(self.conn, "symbol"), EXPECTED_SYMBOL_COLUMNS)
        self.assertEqual(_columns(self.conn, "file"), EXPECTED_FILE_COLUMNS)

    def test_symbol_defaults(self) -> None:
        file_id = _insert_file(self.conn, "a.py")
        self.conn.execute(
            "INSERT INTO symbol (file_id, name, qualified_name, kind, lineno)"
            " VALUES (?, ?, ?, ?, ?)",
            (file_id, "f", "m.f", "function", 1),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM symbol").fetchone()
        self.assertEqual(row["is_private"], 0)
        self.assertEqual(row["complexity"], 0)
        self.assertEqual(row["line_count"], 0)
        self.assertIsNone(row["docstring"])
        self.assertIsNone(row["signature"])

    def test_file_columns_exactly_pinned(self) -> None:
        self.assertEqual(_columns(self.conn, "file"), EXPECTED_FILE_COLUMNS)

    def test_rel_path_is_unique(self) -> None:
        _insert_file(self.conn, "a.py")
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_file(self.conn, "a.py")

    def test_meta_key_is_primary_key(self) -> None:
        self.conn.execute("INSERT INTO meta VALUES ('k', 'v')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO meta VALUES ('k', 'other')")

    def test_all_declared_indexes_exist(self) -> None:
        self.assertEqual(_index_names(self.conn), EXPECTED_INDEXES)

    def test_foreign_keys_pragma_enabled_by_connect(self) -> None:
        mode = self.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        self.assertEqual(mode, 1)

    def test_delete_file_cascades_to_symbol_and_imports(self) -> None:
        f = _insert_file(self.conn, "a.py")
        self.conn.execute(
            "INSERT INTO symbol (file_id, name, qualified_name, kind, lineno)"
            " VALUES (?, 'x', 'm.x', 'function', 1)",
            (f,),
        )
        self.conn.execute(
            "INSERT INTO import_edge (file_id, imported_module, lineno) VALUES (?, 'h', 1)",
            (f,),
        )
        self.conn.commit()
        self.conn.execute("DELETE FROM file WHERE file_id = ?", (f,))
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM symbol").fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM import_edge").fetchone()[0], 0
        )

    def test_resolved_reference_set_to_null_on_delete(self) -> None:
        f1 = _insert_file(self.conn, "a.py")
        f2 = _insert_file(self.conn, "b.py")
        self.conn.execute(
            "INSERT INTO import_edge (file_id, imported_module, resolved_file_id, lineno)"
            " VALUES (?, 'b.py', ?, 1)",
            (f1, f2),
        )
        self.conn.commit()
        self.conn.execute("DELETE FROM file WHERE file_id = ?", (f2,))
        self.conn.commit()
        row = self.conn.execute("SELECT resolved_file_id FROM import_edge").fetchone()
        self.assertIsNone(row["resolved_file_id"])
        # The edge itself survives; only the resolution is cleared.
        self.assertIsNotNone(row)

    def test_resolved_symbol_id_set_null_on_symbol_delete(self) -> None:
        f = _insert_file(self.conn, "a.py")
        cur = self.conn.execute(
            "INSERT INTO symbol (file_id, name, qualified_name, kind, lineno)"
            " VALUES (?, 'target', 'm.target', 'function', 1)",
            (f,),
        )
        target_id = cur.lastrowid
        self.conn.execute(
            "INSERT INTO symbol (file_id, name, qualified_name, kind, lineno)"
            " VALUES (?, 'caller', 'm.caller', 'function', 2)",
            (f,),
        )
        caller_id = self.conn.execute(
            "SELECT symbol_id FROM symbol WHERE name = 'caller'"
        ).fetchone()["symbol_id"]
        self.conn.execute(
            "INSERT INTO call_edge (caller_symbol_id, callee_name,"
            " resolved_symbol_id, lineno) VALUES (?, 'target', ?, 3)",
            (caller_id, target_id),
        )
        self.conn.commit()
        self.conn.execute("DELETE FROM symbol WHERE symbol_id = ?", (target_id,))
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM symbol").fetchone()[0], 1
        )
        row = self.conn.execute("SELECT * FROM call_edge").fetchone()
        self.assertIsNone(row["resolved_symbol_id"])

    def test_connect_is_idempotent_and_reuses_db(self) -> None:
        f = _insert_file(self.conn, "a.py")
        self.conn.commit()
        self.conn.close()
        conn2 = connect(self.repo)
        try:
            # DDL re-run must not error and data must survive.
            self.assertEqual(
                conn2.execute("SELECT COUNT(*) FROM file").fetchone()[0], 1 if f else 0
            )
            self.assertLessEqual(EXPECTED_TABLES, _tables(conn2))
            row = conn2.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            self.assertEqual(row["value"], str(SCHEMA_VERSION))
        finally:
            conn2.close()
            # Reopen for tearDown close().
            self.conn = connect(self.repo)


if __name__ == "__main__":
    unittest.main()
