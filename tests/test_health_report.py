"""health_report smoke tests."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

from conftest import cleanup, prepare_extended_repo

from jarvis_graph import cli
from jarvis_graph.health_report_engine import health_report


class HealthReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_report_has_all_sections(self) -> None:
        rep = health_report(
            self.repo,
            complexity_threshold=1,
            long_threshold=10,
            top_n=5,
            fan_out_threshold=1,
            coverage_min_complexity=1,
        )
        md = rep.markdown
        for section in (
            "## 1. Headline",
            "## 2. Complexity hotspots",
            "## 3. Long functions",
            "## 4. God files",
            "## 5. Client hubs",
            "## 6. Dead code candidates",
            "## 7. Coverage gaps",
            "## 8. Unused imports",
            "## 9. Circular dependencies",
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
        self.assertIn("## 10. Drift since baseline", rep2.markdown)
        self.assertIn("drift", rep2.summary)
        self.assertEqual(rep2.summary["drift"]["regression_count"], 0)

    def test_cli_save_baseline_then_baseline_diff(self) -> None:
        # End-to-end CLI run: snapshot, then re-run with that snapshot as
        # baseline. The drift section must appear in the produced markdown
        # and report zero regressions.
        snap = self.tmp_root / "snap.json"
        out_md = self.tmp_root / "report.md"
        out_md2 = self.tmp_root / "report2.md"
        buf, errbuf = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(errbuf):
            rc = cli.main([
                "--no-color",
                "health_report",
                str(self.repo),
                "--complexity-threshold", "1",
                "--long-threshold", "10",
                "--top-n", "5",
                "--save-baseline", str(snap),
                "--out", str(out_md),
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(snap.exists())
            # The saved snapshot must round-trip through the loader.
            loaded = json.loads(snap.read_text(encoding="utf-8"))
            self.assertIn("summary", loaded)
            self.assertIn("complexity", loaded["summary"])

            # Re-run with the snapshot as baseline.
            rc2 = cli.main([
                "--no-color",
                "health_report",
                str(self.repo),
                "--complexity-threshold", "1",
                "--long-threshold", "10",
                "--top-n", "5",
                "--baseline", str(snap),
                "--out", str(out_md2),
            ])
            self.assertEqual(rc2, 0)
        md = out_md2.read_text(encoding="utf-8")
        self.assertIn("## 10. Drift since baseline", md)
        self.assertIn("0** regression(s)", md)

    def test_cli_baseline_missing_file_returns_error(self) -> None:
        buf, errbuf = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(errbuf):
            rc = cli.main([
                "--no-color",
                "health_report",
                str(self.repo),
                "--baseline", str(self.tmp_root / "no_such_file.json"),
            ])
        self.assertEqual(rc, 2)
        self.assertIn("cannot read baseline", errbuf.getvalue())

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
