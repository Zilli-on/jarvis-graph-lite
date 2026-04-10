"""Unit tests for the drift engine. Pure stdlib unittest, no fixture I/O."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo  # noqa: F401 (path setup)

from jarvis_graph.drift_engine import (
    DriftReport,
    compute_drift,
    render_drift_markdown,
)


def _baseline_summary() -> dict:
    """A minimal but realistic snapshot to diff against."""
    return {
        "headline": {
            "files": 10,
            "symbols": 100,
            "files_with_parse_errors": 0,
            "imports_resolution_pct": 80.0,
            "calls_resolution_pct": 70.0,
        },
        "complexity": {
            "total": 50,
            "average": 3.5,
            "high": 4,
            "extreme": 1,
            "hotspot_count": 5,
            "hotspots": [
                {"qname": "mod.a", "rel_path": "mod.py", "lineno": 1, "complexity": 25, "line_count": 80},
                {"qname": "mod.b", "rel_path": "mod.py", "lineno": 30, "complexity": 18, "line_count": 60},
                {"qname": "mod.c", "rel_path": "mod.py", "lineno": 90, "complexity": 12, "line_count": 40},
            ],
        },
        "long_functions": {
            "total": 50,
            "over_threshold": 3,
            "average_lines": 12.0,
            "functions": [
                {"qname": "mod.a", "rel_path": "mod.py", "lineno": 1, "line_count": 80, "complexity": 25},
            ],
        },
        "god_files": [
            {"path": "mod.py", "score": 0.5, "symbols": 10, "loc": 200, "fan_in": 3},
        ],
        "dead_code": {
            "count": 2,
            "symbols": [
                {"qname": "mod.dead_one", "rel_path": "mod.py", "lineno": 100, "kind": "function"},
                {"qname": "mod.dead_two", "rel_path": "mod.py", "lineno": 110, "kind": "function"},
            ],
        },
        "unused_imports": {
            "count": 1,
            "top_files": [{"path": "mod.py", "count": 1}],
        },
        "cycles": {
            "count": 1,
            "groups": [{"size": 2, "files": ["a.py", "b.py"]}],
        },
    }


class ComputeDriftTests(unittest.TestCase):
    def test_no_baseline_yields_empty_report(self) -> None:
        rep = compute_drift(None, _baseline_summary())
        self.assertFalse(rep.has_baseline)
        self.assertEqual(rep.scalars, [])
        self.assertEqual(rep.sets, [])

    def test_unchanged_snapshot_has_no_regressions(self) -> None:
        baseline = _baseline_summary()
        rep = compute_drift(baseline, baseline)
        self.assertTrue(rep.has_baseline)
        self.assertEqual(rep.regression_count, 0)
        self.assertEqual(rep.improvement_count, 0)
        # Every scalar should be "unchanged" or "neutral".
        for s in rep.scalars:
            self.assertIn(s.direction, {"unchanged", "neutral"})
        for sd in rep.sets:
            self.assertEqual(sd.regressions, [])
            self.assertEqual(sd.improvements, [])

    def test_worsened_complexity_counts_regression(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["complexity"]["high"] = 7
        current["complexity"]["extreme"] = 3
        rep = compute_drift(baseline, current)
        worse = [s for s in rep.scalars if s.direction == "worsened"]
        self.assertEqual(len(worse), 2)
        names = {s.name for s in worse}
        self.assertIn("high-complexity callables", names)
        self.assertIn("extreme-complexity callables", names)

    def test_improved_resolution_pct(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["headline"]["calls_resolution_pct"] = 85.0
        rep = compute_drift(baseline, current)
        improved = [s for s in rep.scalars if s.direction == "improved"]
        self.assertEqual(len(improved), 1)
        self.assertEqual(improved[0].name, "call resolution %")
        self.assertAlmostEqual(improved[0].delta, 15.0)

    def test_neutral_metric_does_not_count(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["headline"]["files"] = 12  # neutral metric
        rep = compute_drift(baseline, current)
        self.assertEqual(rep.regression_count, 0)
        self.assertEqual(rep.improvement_count, 0)
        files_drift = [s for s in rep.scalars if s.name == "files"]
        self.assertEqual(len(files_drift), 1)
        self.assertEqual(files_drift[0].direction, "neutral")

    def test_set_drift_detects_new_hotspot(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["complexity"]["hotspots"].append(
            {"qname": "mod.brand_new", "rel_path": "mod.py", "lineno": 200, "complexity": 30, "line_count": 100}
        )
        current["complexity"]["hotspot_count"] = 6
        rep = compute_drift(baseline, current)
        hotspot_set = next(s for s in rep.sets if s.name == "complexity hotspots")
        self.assertEqual(hotspot_set.regressions, ["mod.brand_new"])
        self.assertEqual(hotspot_set.improvements, [])
        self.assertEqual(hotspot_set.unchanged, 3)

    def test_set_drift_detects_removed_hotspot(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["complexity"]["hotspots"] = [
            h for h in current["complexity"]["hotspots"] if h["qname"] != "mod.c"
        ]
        rep = compute_drift(baseline, current)
        hotspot_set = next(s for s in rep.sets if s.name == "complexity hotspots")
        self.assertEqual(hotspot_set.regressions, [])
        self.assertEqual(hotspot_set.improvements, ["mod.c"])

    def test_dead_code_set_diff(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["dead_code"]["symbols"] = [
            {"qname": "mod.dead_one", "rel_path": "mod.py", "lineno": 100, "kind": "function"},
            {"qname": "mod.dead_three", "rel_path": "mod.py", "lineno": 120, "kind": "function"},
        ]
        rep = compute_drift(baseline, current)
        dead_set = next(s for s in rep.sets if s.name == "dead code symbols")
        self.assertEqual(dead_set.regressions, ["mod.dead_three"])
        self.assertEqual(dead_set.improvements, ["mod.dead_two"])
        self.assertEqual(dead_set.unchanged, 1)

    def test_cycle_set_diff_keys_by_member_files(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["cycles"]["groups"] = [
            {"size": 3, "files": ["x.py", "y.py", "z.py"]},
        ]
        rep = compute_drift(baseline, current)
        cyc_set = next(s for s in rep.sets if s.name == "import cycles")
        self.assertEqual(len(cyc_set.regressions), 1)
        self.assertEqual(len(cyc_set.improvements), 1)

    def test_god_files_diff_by_path(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["god_files"] = [
            {"path": "mod.py", "score": 0.5, "symbols": 10, "loc": 200, "fan_in": 3},
            {"path": "huge_new.py", "score": 0.8, "symbols": 30, "loc": 2000, "fan_in": 8},
        ]
        rep = compute_drift(baseline, current)
        god_set = next(s for s in rep.sets if s.name == "god files")
        self.assertEqual(god_set.regressions, ["huge_new.py"])
        self.assertEqual(god_set.improvements, [])

    def test_missing_field_in_baseline_is_skipped(self) -> None:
        baseline = {"headline": {"files": 10}}
        current = _baseline_summary()
        rep = compute_drift(baseline, current)
        # Only `files` (neutral) should be in scalars from the headline path.
        names = {s.name for s in rep.scalars}
        self.assertIn("files", names)
        self.assertNotIn("dead code candidates", names)
        # Sets should be silently empty for missing fields too.
        self.assertEqual(rep.sets, [])

    def test_render_no_baseline_returns_empty(self) -> None:
        rep = compute_drift(None, _baseline_summary())
        self.assertEqual(render_drift_markdown(rep), "")

    def test_render_unchanged_says_no_drift(self) -> None:
        baseline = _baseline_summary()
        rep = compute_drift(baseline, baseline)
        md = render_drift_markdown(rep)
        self.assertIn("## 9. Drift since baseline", md)
        self.assertIn("No measurable drift", md)

    def test_render_lists_regressions_and_improvements(self) -> None:
        baseline = _baseline_summary()
        current = _baseline_summary()
        current["complexity"]["high"] = 7  # +3 → worsened
        current["complexity"]["hotspots"].append(
            {"qname": "mod.brand_new", "rel_path": "mod.py", "lineno": 200, "complexity": 30, "line_count": 100}
        )
        rep = compute_drift(baseline, current)
        md = render_drift_markdown(rep)
        self.assertIn("worsened", md)
        self.assertIn("`mod.brand_new`", md)
        self.assertIn("Newly in the list", md)


if __name__ == "__main__":
    unittest.main()
