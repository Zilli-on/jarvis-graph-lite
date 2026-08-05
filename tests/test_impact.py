"""Impact engine smoke tests."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from conftest import cleanup, prepare_sample_repo

from jarvis_graph.impact_engine import impact
from jarvis_graph.indexer import index_repo


class ImpactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_sample_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_impact_for_widely_used_helper(self) -> None:
        res = impact(self.repo, "format_greeting")
        self.assertEqual(res.kind, "symbol")
        self.assertGreaterEqual(len(res.direct_importers), 2)
        self.assertIn(res.risk, {"low", "medium", "high"})
        self.assertTrue(res.why)

    def test_impact_for_file(self) -> None:
        res = impact(self.repo, "helpers.py")
        self.assertEqual(res.kind, "file")
        self.assertGreaterEqual(len(res.direct_importers), 2)

    def test_impact_for_unused_symbol(self) -> None:
        res = impact(self.repo, "shout")
        self.assertEqual(res.kind, "symbol")
        self.assertEqual(res.risk, "low")

    def test_impact_not_found(self) -> None:
        res = impact(self.repo, "nope_nope_nope_xyz")
        self.assertEqual(res.kind, "not_found")

    def test_impact_for_method_via_dotted_name(self) -> None:
        """`GreetingService.greet` must resolve via parent_qname suffix
        and report the method's actual call sites."""
        res = impact(self.repo, "GreetingService.greet")
        self.assertEqual(res.kind, "symbol")
        self.assertTrue(
            res.qualified_name.endswith("GreetingService.greet"),
            f"expected GreetingService.greet, got {res.qualified_name}",
        )
        # app.py:main calls svc.greet → after parser rewrite → resolved
        caller_qnames = {q for q, _, _ in res.direct_callers}
        self.assertTrue(
            any("main" in q for q in caller_qnames),
            f"expected main as a caller; got {caller_qnames}",
        )

    def test_impact_for_method_with_module_prefix(self) -> None:
        res = impact(self.repo, "service.GreetingService.greet")
        self.assertEqual(res.kind, "symbol")
        self.assertEqual(res.qualified_name, "service.GreetingService.greet")


class ImpactViaFromPackageImportModuleTests(unittest.TestCase):
    """`from <pkg> import <module>` + `module.func()` must produce a call edge.

    Regression guard for the resolution gap fixed in `_resolve_calls` path
    (a2): those import edges carry `imported_name = '<module>'` and resolve to
    the package's `__init__.py`, so neither path (a) (plain `import X` only)
    nor path (c) (undotted callees only) matched. `impact` then answered
    "no resolved callers found - may be unused" for functions that ARE called
    - the worst possible direction for a "what breaks if I change this" tool.
    """

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="jgl_pkgmod_"))
        self.repo = self.tmp_root / "repo"
        pkg = self.repo / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "settings.py").write_text(
            "def load_settings() -> dict:\n    return {}\n",
            encoding="utf-8",
        )
        (pkg / "runner.py").write_text(
            "from pkg import settings\n\n\n"
            "def run() -> dict:\n"
            "    return settings.load_settings()\n",
            encoding="utf-8",
        )
        index_repo(self.repo, full=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_module_qualified_call_is_resolved(self) -> None:
        res = impact(self.repo, "load_settings")
        self.assertEqual(res.kind, "symbol")
        caller_qnames = {q for q, _, _ in res.direct_callers}
        self.assertTrue(
            any(q.endswith("runner.run") for q in caller_qnames),
            f"expected pkg.runner.run as a direct caller; got {caller_qnames}",
        )


if __name__ == "__main__":
    unittest.main()
