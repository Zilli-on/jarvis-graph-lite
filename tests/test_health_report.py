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
        self.assertGreaterEqual(s["dead_code_count"], 1)
        self.assertGreaterEqual(s["cycle_count"], 1)  # cycle_a/cycle_b fixture

    def test_cycle_section_lists_known_cycle(self) -> None:
        rep = health_report(self.repo, complexity_threshold=1, long_threshold=10, top_n=5)
        # Both cycle files must appear under the cycles section.
        self.assertIn("cycle_a.py", rep.markdown)
        self.assertIn("cycle_b.py", rep.markdown)


if __name__ == "__main__":
    unittest.main()
