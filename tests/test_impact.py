"""Impact engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_sample_repo

from jarvis_graph.impact_engine import impact


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


if __name__ == "__main__":
    unittest.main()
