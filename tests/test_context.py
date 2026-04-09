"""Context engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_sample_repo

from jarvis_graph.context_engine import context


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_sample_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_context_for_function(self) -> None:
        res = context(self.repo, "format_greeting")
        self.assertEqual(res.kind, "symbol")
        self.assertEqual(res.rel_path, "helpers.py")
        self.assertTrue(res.qualified_name and res.qualified_name.endswith("format_greeting"))
        self.assertEqual(res.signature, "(text)")
        self.assertGreaterEqual(len(res.imports_in), 2)

    def test_context_for_class(self) -> None:
        res = context(self.repo, "GreetingService")
        self.assertEqual(res.kind, "symbol")
        self.assertEqual(res.rel_path, "service.py")
        sibling_names = {name for name, _qn, _ln in res.siblings}
        self.assertIn("greet", sibling_names)
        self.assertIn("shout", sibling_names)

    def test_context_for_file(self) -> None:
        res = context(self.repo, "helpers.py")
        self.assertEqual(res.kind, "file")
        self.assertEqual(res.rel_path, "helpers.py")
        names = {name for name, _qn, _ln in res.siblings}
        self.assertTrue({"load_config", "format_greeting", "DEFAULT_PREFIX"} <= names)

    def test_context_not_found(self) -> None:
        res = context(self.repo, "no_such_thing_xyz_12345")
        self.assertEqual(res.kind, "not_found")


if __name__ == "__main__":
    unittest.main()
