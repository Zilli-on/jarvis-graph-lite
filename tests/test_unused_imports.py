"""find_unused_imports engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.unused_imports_engine import find_unused_imports


class UnusedImportsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()
        self.report = find_unused_imports(self.repo)

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_total_imports_nonzero(self) -> None:
        self.assertGreater(self.report.total_imports, 0)

    def test_unused_os_flagged_in_unused_import_module(self) -> None:
        unused_in_file = [
            u for u in self.report.unused if u.rel_path == "unused_import.py"
        ]
        bindings = {u.binding for u in unused_in_file}
        self.assertIn("os", bindings)
        self.assertIn("load_config", bindings)

    def test_used_format_greeting_not_flagged(self) -> None:
        # `format_greeting` IS used in unused_import.py — must not appear
        for u in self.report.unused:
            if u.rel_path == "unused_import.py":
                self.assertNotEqual(u.binding, "format_greeting")

    def test_app_py_imports_clean(self) -> None:
        # All imports in app.py should be used.
        unused_in_app = [u for u in self.report.unused if u.rel_path == "app.py"]
        self.assertEqual(unused_in_app, [], f"unexpected unused in app.py: {unused_in_app}")


if __name__ == "__main__":
    unittest.main()
