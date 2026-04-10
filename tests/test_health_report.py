"""health_report smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.health_report_engine import health_report


class HealthReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_report_has_all_sections(self) -> None:
        rep = health_report(self.repo, complexity_threshold=1, long_threshold=10, top_n=5)
        md = rep.markdown
        for section in (
            "## 1. Headline",
            "## 2. Complexity hotspots",
            "## 3. Long functions",
            "## 4. God files",
            "## 5. Dead code candidates",
            "## 6. Unused imports",
            "## 7. Circular dependencies",
        ):
            self.assertIn(section, md, f"missing section: {section}")

    def test_summary_payload_populated(self) -> None:
        rep = health_report(self.repo, complexity_threshold=1, long_threshold=10, top_n=5)
        s = rep.summary
        self.assertGreater(s["headline"]["files"], 0)
        self.assertGreater(s["headline"]["symbols"], 0)
        self.assertGreaterEqual(s["complexity"]["total"], 1)
        self.assertGreaterEqual(s["dead_code"]["count"], 1)
        self.assertGreaterEqual(s["cycles"]["count"], 1)  # cycle_a/cycle_b fixture
        # Back-compat scalars must still be exposed.
        self.assertEqual(s["dead_code_count"], s["dead_code"]["count"])
        self.assertEqual(s["unused_import_count"], s["unused_imports"]["count"])
        self.assertEqual(s["cycle_count"], s["cycles"]["count"])
        # Enriched lists for v0.5 drift must be present and well-shaped.
        self.assertIn("hotspots", s["complexity"])
        self.assertIsInstance(s["complexity"]["hotspots"], list)
        self.assertIn("symbols", s["dead_code"])
        self.assertIn("groups", s["cycles"])

    def test_cycle_section_lists_known_cycle(self) -> None:
        rep = health_report(self.repo, complexity_threshold=1, long_threshold=10, top_n=5)
        # Both cycle files must appear under the cycles section.
        self.assertIn("cycle_a.py", rep.markdown)
        self.assertIn("cycle_b.py", rep.markdown)

    def test_baseline_unchanged_renders_drift_section(self) -> None:
        rep = health_report(self.repo, complexity_threshold=1, long_threshold=10, top_n=5)
        # Re-run with the first run as the baseline. Nothing has changed,
        # so the drift section must appear and report no regressions.
        rep2 = health_report(
            self.repo,
            complexity_threshold=1,
            long_threshold=10,
            top_n=5,
            baseline=rep.summary,
        )
        self.assertIn("## 8. Drift since baseline", rep2.markdown)
        self.assertIn("drift", rep2.summary)
        self.assertEqual(rep2.summary["drift"]["regression_count"], 0)

    def test_baseline_with_synthetic_regression(self) -> None:
        # Snapshot, then mutate the snapshot to pretend things were better
        # before — drift should report the (now real) numbers as regressions.
        rep = health_report(self.repo, complexity_threshold=1, long_threshold=10, top_n=5)
        baseline = rep.summary
        # Pretend baseline had fewer dead-code symbols and no cycles.
        baseline = {
            **baseline,
            "dead_code": {"count": 0, "symbols": []},
            "cycles": {"count": 0, "groups": []},
        }
        rep2 = health_report(
            self.repo,
            complexity_threshold=1,
            long_threshold=10,
            top_n=5,
            baseline=baseline,
        )
        drift = rep2.summary["drift"]
        self.assertGreater(drift["regression_count"], 0)
        # The dead-code and cycles set diffs should each list at least one
        # new entry.
        names = {s["name"] for s in drift["sets"]}
        self.assertIn("dead code symbols", names)
        self.assertIn("import cycles", names)


if __name__ == "__main__":
    unittest.main()
