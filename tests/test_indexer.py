"""Indexer smoke tests against the canned sample_repo fixture."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_sample_repo

from jarvis_graph.db import connect
from jarvis_graph.indexer import index_repo


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


class IndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_sample_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_index_creates_jarvis_graph_dir(self) -> None:
        self.assertTrue((self.repo / ".jarvis_graph" / "index.db").exists())
        self.assertTrue((self.repo / ".jarvis_graph" / "config.json").exists())

    def test_index_finds_all_python_files(self) -> None:
        conn = connect(self.repo)
        try:
            # app.py, service.py, helpers.py, lazy_caller.py,
            # package/__init__.py, package/worker.py
            self.assertEqual(_count(conn, "file"), 6)
        finally:
            conn.close()

    def test_symbols_extracted(self) -> None:
        conn = connect(self.repo)
        try:
            names = {r["name"] for r in conn.execute("SELECT name FROM symbol")}
        finally:
            conn.close()
        for expected in ("main", "GreetingService", "greet", "format_greeting", "VERSION", "DEFAULT_PREFIX"):
            self.assertIn(expected, names)

    def test_imports_recorded_and_resolved(self) -> None:
        conn = connect(self.repo)
        try:
            rows = conn.execute(
                "SELECT imported_module, resolved_file_id FROM import_edge"
            ).fetchall()
        finally:
            conn.close()
        modules = [r["imported_module"] for r in rows]
        self.assertIn("helpers", modules)
        self.assertIn("service", modules)
        resolved = [r for r in rows if r["imported_module"] == "helpers" and r["resolved_file_id"]]
        self.assertTrue(resolved, "expected at least one resolved 'helpers' import")

    def test_calls_recorded(self) -> None:
        conn = connect(self.repo)
        try:
            callees = {r["callee_name"] for r in conn.execute("SELECT callee_name FROM call_edge")}
        finally:
            conn.close()
        self.assertIn("format_greeting", callees)
        self.assertIn("load_config", callees)
        self.assertIn("run_worker", callees)

    def test_incremental_skips_unchanged(self) -> None:
        report = index_repo(self.repo, full=False)
        self.assertEqual(report.files_seen, 6)
        self.assertEqual(report.files_skipped_unchanged, 6)
        self.assertEqual(report.files_indexed, 0)

    def test_class_instantiation_call_resolved(self) -> None:
        """`svc = GreetingService(); svc.greet()` in app.py must be rewritten
        to `GreetingService.greet` and resolve to the actual method."""
        conn = connect(self.repo)
        try:
            row = conn.execute(
                """
                SELECT ce.callee_name, ce.resolved_symbol_id, s.qualified_name
                  FROM call_edge ce
                  JOIN symbol caller ON caller.symbol_id = ce.caller_symbol_id
                  JOIN file f        ON f.file_id = caller.file_id
             LEFT JOIN symbol s     ON s.symbol_id = ce.resolved_symbol_id
                 WHERE f.rel_path = 'app.py'
                   AND ce.callee_name = 'GreetingService.greet'
                """
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "expected a greet call in app.py")
        self.assertEqual(row["callee_name"], "GreetingService.greet")
        self.assertIsNotNone(
            row["resolved_symbol_id"],
            "rewritten ClassName.method call must resolve",
        )
        self.assertTrue(
            row["qualified_name"].endswith("GreetingService.greet"),
            f"expected GreetingService.greet, got {row['qualified_name']}",
        )

    def test_self_method_call_resolved(self) -> None:
        """`self.greet(name)` inside `GreetingService.shout` must be
        rewritten to `GreetingService.greet` and resolve same-file."""
        conn = connect(self.repo)
        try:
            row = conn.execute(
                """
                SELECT ce.callee_name, ce.resolved_symbol_id, s.qualified_name
                  FROM call_edge ce
                  JOIN symbol caller ON caller.symbol_id = ce.caller_symbol_id
             LEFT JOIN symbol s     ON s.symbol_id = ce.resolved_symbol_id
                 WHERE caller.qualified_name LIKE '%GreetingService.shout'
                   AND ce.callee_name = 'GreetingService.greet'
                """
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "expected a greet call in shout")
        self.assertEqual(row["callee_name"], "GreetingService.greet")
        self.assertIsNotNone(
            row["resolved_symbol_id"],
            "self.method call must resolve to same-file method",
        )

    def test_function_local_import_recorded_and_resolved(self) -> None:
        """`from helpers import format_greeting` inside a function body
        must produce an import edge AND a resolved cross-module call edge."""
        conn = connect(self.repo)
        try:
            rows = conn.execute(
                """
                SELECT ie.imported_module, ie.imported_name, ie.resolved_file_id
                  FROM import_edge ie
                  JOIN file f ON f.file_id = ie.file_id
                 WHERE f.rel_path = 'lazy_caller.py'
                """
            ).fetchall()
            self.assertTrue(rows, "expected at least one import in lazy_caller.py")
            self.assertTrue(
                any(r["imported_name"] == "format_greeting" and r["resolved_file_id"] for r in rows),
                "function-local 'from helpers import format_greeting' must be recorded and resolved",
            )

            # And the call inside call_lazily must resolve to format_greeting in helpers.py
            call = conn.execute(
                """
                SELECT ce.resolved_symbol_id
                  FROM call_edge ce
                  JOIN symbol s ON s.symbol_id = ce.caller_symbol_id
                  JOIN file f   ON f.file_id   = s.file_id
                 WHERE f.rel_path = 'lazy_caller.py'
                   AND ce.callee_name = 'format_greeting'
                """
            ).fetchone()
            self.assertIsNotNone(call, "expected a format_greeting call in lazy_caller.py")
            self.assertIsNotNone(
                call["resolved_symbol_id"],
                "function-local imported call must resolve cross-module",
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
