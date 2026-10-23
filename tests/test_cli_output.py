"""Tests for the human-readable CLI layer (`jarvis_graph.cli`).

Most engine suites call the engines directly; this file drives the CLI in
process through `cli.main([...])` and pins the behaviour users see:

  - the exact plain-text shapes each subcommand prints (no --json)
  - exit codes (0 ok / 1 not-found / 2 hard errors)
  - colour handling: `--color always|never`, `--no-color`, `$NO_COLOR`,
    and auto-detection treating a non-TTY stdout as plain
  - the `health_report` baseline / --out / snapshot round trip
  - the error paths (bad baseline, unknown symbol, missing repo)

Colour state is class-level (`_Style.enabled`) and `$NO_COLOR` leaks between
tests, so the base class snapshots and restores both after every test.
"""

from __future__ import annotations

import io
import json
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from conftest import cleanup, prepare_extended_repo, prepare_sample_repo

from jarvis_graph import __version__, cli
from jarvis_graph.indexer import index_repo

ANSI_RE = re.compile(r"\x1b\[")


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run cli.main in-process, capturing stdout + stderr."""
    buf, errbuf = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(errbuf):
        rc = cli.main(argv)
    return rc, buf.getvalue(), errbuf.getvalue()


class _CliBase(unittest.TestCase):
    """Snapshot + restore the class-level colour state and $NO_COLOR.

    `_Style.enabled` is a class attribute mutated by every cli.main() call
    and `NO_COLOR` is read from the process environment; both would leak
    into sibling tests without this.
    """

    def setUp(self) -> None:
        self._saved_style = cli._Style.enabled
        self._no_color_was_set = "NO_COLOR" in os.environ
        self._saved_no_color = os.environ.pop("NO_COLOR", None)
        self._tmp_roots: list[Path] = []

    def tearDown(self) -> None:
        cli._Style.enabled = self._saved_style
        if self._no_color_was_set:
            os.environ["NO_COLOR"] = self._saved_no_color or "1"
        else:
            os.environ.pop("NO_COLOR", None)
        for root in self._tmp_roots:
            cleanup(root)
        super().tearDown()

    def _keep(self, tmp_root: Path) -> Path:
        self._tmp_roots.append(tmp_root)
        return tmp_root


class SampleRepoCliTest(_CliBase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp_root, self.repo = prepare_sample_repo()
        self._keep(self.tmp_root)


class ExtendedRepoCliTest(_CliBase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp_root, self.repo = prepare_extended_repo()
        self._keep(self.tmp_root)


# --- index -----------------------------------------------------------------


class IndexCliTests(ExtendedRepoCliTest):
    def test_incremental_header_and_count_labels(self) -> None:
        rc, out, err = run_cli(["--no-color", "index", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertIn("jarvis-graph index (incremental)", out)
        # The resolved repo path is echoed.
        self.assertIn(str(self.repo.resolve()), out)
        for label in (
            "files seen:",
            "files indexed:",
            "skipped unchanged:",
            "files removed:",
            "parse errors:",
            "symbols:",
            "imports:",
            "calls:",
            "elapsed:",
        ):
            self.assertIn(label, out)
        # The fixture parses cleanly, so the error count is pinned at 0.
        self.assertRegex(out, r"\n\s+parse errors:\s+0\n")
        # Incremental re-run over an unchanged index: everything skipped.
        self.assertRegex(out, r"\n\s+files indexed:\s+0\n")
        self.assertRegex(out, r"\n\s+skipped unchanged:\s+12\n")
        self.assertRegex(out, r"\n\s+symbols:\s+0\n")

    def test_full_header_labels_the_wipe(self) -> None:
        rc, out, _ = run_cli(["--no-color", "index", str(self.repo), "--full"])
        self.assertEqual(rc, 0)
        self.assertIn("jarvis-graph index (full)", out)
        self.assertNotIn("(incremental)", out)
        # A full rebuild actually indexes: symbols must be non-zero.
        self.assertRegex(out, r"\n\s+symbols:\s+[1-9]\d*\n")
        self.assertRegex(out, r"\n\s+files indexed:\s+12\n")

    def test_index_json_payload(self) -> None:
        rc, out, _ = run_cli(["--no-color", "index", str(self.repo), "--json"])
        self.assertEqual(rc, 0)
        self.assertNotIn("jarvis-graph index", out)  # no header in json mode
        payload = json.loads(out)
        self.assertEqual(payload["files_with_errors"], 0)
        self.assertEqual(payload["files_indexed"], 0)
        self.assertEqual(payload["files_skipped_unchanged"], 12)
        for key in ("files_seen", "symbols_total", "imports_total", "calls_total"):
            self.assertIn(key, payload)


# --- query -----------------------------------------------------------------


class QueryCliTests(ExtendedRepoCliTest):
    def test_hit_listing_shape(self) -> None:
        rc, out, _ = run_cli(["--no-color", "query", str(self.repo), "format_greeting"])
        self.assertEqual(rc, 0)
        self.assertIn("hit(s) for: format_greeting", out)
        # Score/grade/kind/name/qname/path line shape.
        self.assertRegex(
            out,
            r"\n  \[\s*\d+\] function\s+format_greeting"
            r" \(helpers\.format_greeting\)  -> helpers\.py:\d+",
        )
        # The source snippet (if present) is indented by 8 spaces; here the
        # module-level hit of the aliased-import fixture carries one.
        self.assertIn(
            "        Exercises the `import X; X.func()` resolution path.", out
        )

    def test_no_hits_message(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "query", str(self.repo), "definitelynotinthisrepo_xyz"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("(no hits)", out)
        self.assertNotIn("hit(s) for", out)

    def test_limit_caps_the_hit_count_header(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "query", str(self.repo), "greeting", "--limit", "1"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("1 hit(s) for: greeting", out)

    def test_json_mode_emits_structured_hits(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "query", str(self.repo), "format_greeting", "--json"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload)
        first = payload[0]
        for key in ("name", "kind", "score", "rel_path", "lineno", "snippet"):
            self.assertIn(key, first)
        self.assertEqual(first["name"], "format_greeting")


# --- context ----------------------------------------------------------------


class ContextCliTests(ExtendedRepoCliTest):
    def test_symbol_context_sections(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "context", str(self.repo), "GreetingService"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("context: GreetingService (symbol)", out)
        self.assertRegex(out, r"\n  file:        service\.py")
        self.assertIn("qname:       service.GreetingService", out)
        self.assertIn("doc:         Builds greeting messages", out)
        self.assertIn("role:        ordinary module", out)
        self.assertIn("imported by: 1 file(s)", out)
        self.assertIn("    - app.py", out)
        self.assertIn("imports:     1 statement(s)", out)
        self.assertIn("    - from helpers import format_greeting", out)
        self.assertIn("callers:     1", out)
        self.assertIn("    - app.main  (app.py:", out)
        self.assertIn("siblings:    4", out)
        self.assertIn("    - greet  (line 12)", out)
        self.assertIn("    - shout  (line 15)", out)

    def test_file_context_lists_importers_and_siblings(self) -> None:
        rc, out, _ = run_cli(["--no-color", "context", str(self.repo), "helpers.py"])
        self.assertEqual(rc, 0)
        self.assertIn("context: helpers.py (file)", out)
        self.assertIn("imported by: 6 file(s)", out)
        self.assertIn("    - service.py", out)
        self.assertIn("siblings:    4", out)
        self.assertIn("    - DEFAULT_PREFIX  (line 3)", out)
        self.assertIn("    - load_config  (line 6)", out)

    def test_not_found_returns_1_with_message(self) -> None:
        rc, out, _ = run_cli(["--no-color", "context", str(self.repo), "no_such_thing"])
        self.assertEqual(rc, 1)
        self.assertIn("(no symbol or file matching: no_such_thing)", out)
        self.assertNotIn("context:", out)  # no header on the miss path

    def test_context_json_payload(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "context", str(self.repo), "greet", "--json"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "symbol")
        self.assertEqual(payload["qualified_name"], "service.GreetingService.greet")


# --- impact -----------------------------------------------------------------


class ImpactCliTests(ExtendedRepoCliTest):
    def test_impact_sections_and_risk_word(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "impact", str(self.repo), "format_greeting"]
        )
        self.assertEqual(rc, 0)
        self.assertRegex(
            out, r"impact: format_greeting \(symbol\)  risk=(LOW|MEDIUM|HIGH)"
        )
        self.assertRegex(out, r"\n  file:        helpers\.py")
        self.assertIn("qname:       helpers.format_greeting", out)
        self.assertRegex(out, r"\n  direct callers:   7\n")
        self.assertIn("    - service.GreetingService.greet  (service.py:", out)
        self.assertRegex(out, r"\n  direct importers: 6\n")
        self.assertIn("    - plain_import_caller.py", out)
        self.assertRegex(out, r"\n  second-order:     5\n")
        self.assertIn("    - service.GreetingService.shout", out)
        self.assertIn("  why:", out)
        self.assertIn("    - 13 direct + 5 second-order dependents", out)

    def test_not_found_returns_1_with_message(self) -> None:
        rc, out, _ = run_cli(["--no-color", "impact", str(self.repo), "no_such_thing"])
        self.assertEqual(rc, 1)
        self.assertIn("(no symbol or file matching: no_such_thing)", out)
        self.assertNotIn("risk=", out)  # no report on the miss path

    def test_json_mode_emits_structured_payload(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "impact", str(self.repo), "greet", "--json"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["qualified_name"], "service.GreetingService.greet")
        self.assertIn("direct_callers", payload)
        self.assertIn("second_order", payload)
        self.assertIn("why", payload)


# --- find_path ---------------------------------------------------------------


class FindPathCliTests(ExtendedRepoCliTest):
    def test_found_chain_marks_steps_with_arrows(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_path", str(self.repo), "shout", "format_greeting"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("find_path: shout → format_greeting", out)
        self.assertIn("(2-step chain,", out)
        self.assertIn("source: service.GreetingService.shout", out)
        self.assertIn("target: helpers.format_greeting", out)
        # Step 0 has no arrow, later steps get the `→` prefix.
        self.assertIn("\n      service.GreetingService.shout  (service.py:", out)
        self.assertIn("\n    → service.GreetingService.greet  (service.py:", out)
        self.assertIn("\n    → helpers.format_greeting  (helpers.py:", out)

    def test_no_path_returns_1_with_note(self) -> None:
        rc, out, _ = run_cli(
            [
                "--no-color",
                "find_path",
                str(self.repo),
                "helpers.load_config",
                "package.worker.run_worker",
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("find_path: helpers.load_config → package.worker.run_worker", out)
        self.assertIn("source: helpers.load_config", out)
        self.assertIn("target: package.worker.run_worker", out)
        self.assertIn("  no path: no resolved call path within depth 8", out)

    def test_max_depth_is_respected_in_the_note(self) -> None:
        rc, out, _ = run_cli(
            [
                "--no-color",
                "find_path",
                str(self.repo),
                "shout",
                "format_greeting",
                "--max-depth",
                "1",
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("no resolved call path within depth 1", out)


# --- detect_changes ----------------------------------------------------------


class DetectChangesCliTests(ExtendedRepoCliTest):
    def test_clean_repo_reports_no_changes(self) -> None:
        rc, out, _ = run_cli(["--no-color", "detect_changes", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("detect_changes", out)
        self.assertRegex(out, r"\n  total on disk: \d+")
        self.assertRegex(out, r"\n  unchanged:\s+12\n")
        self.assertRegex(out, r"\n  added:\s+0\n")
        self.assertRegex(out, r"\n  modified:\s+0\n")
        self.assertRegex(out, r"\n  removed:\s+0\n")
        self.assertIn("  recommendation: no_changes", out)
        self.assertIn("  reason:         index is up to date", out)

    def test_modified_file_is_listed_with_marker(self) -> None:
        path = self.repo / "helpers.py"
        path.write_text(
            path.read_text(encoding="utf-8") + "# touched\n", encoding="utf-8"
        )
        rc, out, _ = run_cli(["--no-color", "detect_changes", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"\n  modified:\s+1\n")
        self.assertIn("\n    ~ helpers.py\n", out)
        self.assertIn("  recommendation: incremental", out)
        self.assertIn("1 changes (0 added, 1 modified, 0 removed)", out)

    def test_removed_file_is_listed_with_marker(self) -> None:
        (self.repo / "plain_import_caller.py").unlink()
        rc, out, _ = run_cli(["--no-color", "detect_changes", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"\n  removed:\s+1\n")
        self.assertIn("\n    - plain_import_caller.py\n", out)

    def test_added_file_is_listed_with_marker(self) -> None:
        (self.repo / "new_mod.py").write_text(
            "def fresh() -> int:\n    return 1\n", encoding="utf-8"
        )
        rc, out, _ = run_cli(["--no-color", "detect_changes", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"\n  added:\s+1\n")
        self.assertIn("\n    + new_mod.py\n", out)


# --- summary -----------------------------------------------------------------


class SummaryCliTests(ExtendedRepoCliTest):
    def test_counts_and_listing_blocks(self) -> None:
        rc, out, _ = run_cli(["--no-color", "summary", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"^summary: ")
        self.assertRegex(out, r"\n  files:        \d+ \(0 with parse errors\)")
        self.assertRegex(
            out, r"\n  symbols:      \d+  \(fn=\d+ cls=\d+ m=\d+ const=\d+\)"
        )
        self.assertRegex(out, r"\n  imports:      \d+")
        self.assertRegex(out, r"\n  call edges:   \d+")
        self.assertIn("most imported files:", out)
        # helpers.py is the most-imported file in the fixture.
        self.assertIn("9  helpers.py", out)
        self.assertIn("largest files by symbol count:", out)
        # No parse errors and exact symbol total for the canned fixture.
        self.assertIn("symbols:      37", out)

    def test_json_payload_keys(self) -> None:
        rc, out, _ = run_cli(["--no-color", "summary", str(self.repo), "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["parse_errors"], 0)
        self.assertEqual(payload["symbols"], 37)
        for key in ("files", "imports", "calls", "most_imported_files"):
            self.assertIn(key, payload)


# --- find_dead_code ----------------------------------------------------------


class DeadCodeCliTests(ExtendedRepoCliTest):
    def test_dead_candidates_listing(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_dead_code", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("find_dead_code:", out)
        self.assertIn(
            "checked=19 excluded(dunder=1 private=0 entrypoint=1 test=0 textual=0)", out
        )
        self.assertRegex(out, r"\n  dead candidates: [1-9]")
        # The notorious dead fixture function is on the list ...
        self.assertIn(
            "\n    - function dead_function.really_dead  (dead_function.py:1)", out
        )
        # ... and its used sibling is NOT (top-level call keeps it alive).
        self.assertNotIn("used_function", out)

    def test_limit_truncates_with_more_suffix(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_dead_code", str(self.repo), "--limit", "2"]
        )
        self.assertEqual(rc, 0)
        # 6 dead candidates exist; showing 2 must append the +N-more footer.
        self.assertIn("    ... +4 more", out)

    def test_json_payload(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_dead_code", str(self.repo), "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        names = {d["qualified_name"] for d in payload["dead"]}
        self.assertIn("dead_function.really_dead", names)


# --- find_coverage_gaps ------------------------------------------------------


class _CoverageGapRepoMixin:
    """Builds the small src/test layout the coverage-gap engine expects."""

    @staticmethod
    def _build(base_repo: Path) -> None:
        (base_repo / "widget.py").write_text(
            "def widget_helper() -> int:\n"
            "    return 11\n"
            "\n"
            "def uncovered_func() -> int:\n"
            "    if True:\n"
            "        return 2\n"
            "    return 3\n",
            encoding="utf-8",
        )
        tests = base_repo / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_widget.py").write_text(
            "from widget import widget_helper\n"
            "\n"
            "def test_widget() -> None:\n"
            "    assert widget_helper() == 11\n",
            encoding="utf-8",
        )


class CoverageGapsCliTests(ExtendedRepoCliTest, _CoverageGapRepoMixin):
    def test_gaps_listing_with_tests(self) -> None:
        self._build(self.repo)
        index_repo(self.repo, full=True)
        rc, out, _ = run_cli(["--no-color", "find_coverage_gaps", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("find_coverage_gaps:", out)
        self.assertRegex(out, r"test entry points: 1")
        self.assertIn("reached symbols: 2", out)
        self.assertIn("widget.uncovered_func  cmplx=2", out)
        self.assertNotIn("widget_helper  (widget.py:", out)  # reached → no gap row
        self.assertRegex(out, r"\n  coverage: \d+(\.\d+)?%")
        self.assertIn("(min_complexity=1)", out)
        self.assertRegex(out, r"\n  gaps shown: [1-9]")

    def test_no_test_entry_points_is_a_soft_error(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_coverage_gaps", str(self.repo)])
        self.assertEqual(rc, 1)
        self.assertIn("find_coverage_gaps:", out)
        self.assertIn("no test entry points found", out)
        self.assertNotIn("coverage:", out)


# --- generate_test_skeleton ---------------------------------------------------


class SkeletonCliTests(ExtendedRepoCliTest):
    def test_stdout_mode_prints_header_and_body(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "generate_test_skeleton", str(self.repo), "GreetingService"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("generate_test_skeleton: service.GreetingService", out)
        self.assertIn("kind=class  module=service", out)
        self.assertIn("suggested filename: test_greetingservice_skeleton.py", out)
        self.assertIn("class GreetingServiceTests(unittest.TestCase):", out)
        self.assertIn("raise NotImplementedError", out)

    def test_out_mode_writes_file_with_target_line(self) -> None:
        out_path = self.tmp_root / "generated" / "test_greeting.py"
        rc, out, _ = run_cli(
            [
                "--no-color",
                "generate_test_skeleton",
                str(self.repo),
                "GreetingService",
                "--out",
                str(out_path),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())
        self.assertIn("written to " + str(out_path), out)
        self.assertIn(
            "target: class service.GreetingService  (import: service.GreetingService)",
            out,
        )
        self.assertIn(
            "class GreetingServiceTests(unittest.TestCase):",
            out_path.read_text(encoding="utf-8"),
        )

    def test_out_mode_refuses_to_clobber_without_force(self) -> None:
        out_path = self.tmp_root / "test_greeting.py"
        out_path.write_text("# pre-existing\n", encoding="utf-8")
        rc, out, err = run_cli(
            [
                "--no-color",
                "generate_test_skeleton",
                str(self.repo),
                "GreetingService",
                "--out",
                str(out_path),
            ]
        )
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("generate_test_skeleton: ", err)
        self.assertEqual(out_path.read_text(encoding="utf-8"), "# pre-existing\n")

    def test_force_overwrites(self) -> None:
        out_path = self.tmp_root / "test_greeting.py"
        out_path.write_text("# pre-existing\n", encoding="utf-8")
        rc, out, _ = run_cli(
            [
                "--no-color",
                "generate_test_skeleton",
                str(self.repo),
                "GreetingService",
                "--out",
                str(out_path),
                "--force",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertIn(
            "class GreetingServiceTests(unittest.TestCase):",
            out_path.read_text(encoding="utf-8"),
        )

    def test_unknown_symbol_is_a_stderr_error(self) -> None:
        rc, out, err = run_cli(
            [
                "--no-color",
                "generate_test_skeleton",
                str(self.repo),
                "definitely_not_a_symbol",
            ]
        )
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("generate_test_skeleton: ", err)


# --- find_unused_imports ------------------------------------------------------


class UnusedImportsCliTests(ExtendedRepoCliTest):
    def test_unused_listing_with_statements(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_unused_imports", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("find_unused_imports:", out)
        self.assertIn("total imports: 16", out)
        self.assertIn("unused:        2", out)
        self.assertIn("    - unused_import.py:1  import os\n", out)
        self.assertIn(
            "    - unused_import.py:2  from helpers import load_config\n", out
        )

    def test_limit_truncates_with_more_suffix(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_unused_imports", str(self.repo), "--limit", "1"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("    ... +1 more", out)

    def test_json_payload(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_unused_imports", str(self.repo), "--json"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total_imports"], 16)
        self.assertEqual(len(payload["unused"]), 2)


# --- find_circular_deps -------------------------------------------------------


class CircularDepsCliTests(ExtendedRepoCliTest):
    def test_cycle_listing(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_circular_deps", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("find_circular_deps:", out)
        self.assertRegex(out, r"\n  files: 12  edges: \d+")
        self.assertIn("cycles found: 1", out)
        self.assertIn("  [1] size=2", out)
        self.assertIn("      - cycle_a.py", out)
        self.assertIn("      - cycle_b.py", out)

    def test_json_payload_matches(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_circular_deps", str(self.repo), "--json"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        files_in_cycle = set(payload["cycles"][0]["files"])
        self.assertEqual(files_in_cycle, {"cycle_a.py", "cycle_b.py"})


# --- find_complexity ----------------------------------------------------------


class ComplexityCliTests(ExtendedRepoCliTest):
    def test_hotspot_row_with_echoed_threshold(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_complexity", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertRegex(
            out, r"callables=\d+ avg=[\d.]+ high\(11-20\)=1 extreme\(>20\)=0"
        )
        self.assertIn("threshold=10", out)
        self.assertIn("hotspots: 1", out)
        self.assertIn("    [ 13] function complex_module.tangled", out)
        self.assertIn("(complex_module.py:6, 21 lines)", out)

    def test_lower_threshold_expands_the_list(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_complexity", str(self.repo), "--threshold", "1"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("threshold=1", out)
        self.assertIn("hotspots: 17", out)
        # cx=1 functions like `simple` join the list under threshold 1.
        self.assertIn("[  1] function complex_module.simple", out)

    def test_json_payload(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_complexity", str(self.repo), "--json"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(
            payload["hotspots"][0]["qualified_name"], "complex_module.tangled"
        )


# --- find_long_functions ------------------------------------------------------


class LongFunctionsCliTests(ExtendedRepoCliTest):
    def test_clean_run_at_default_threshold(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_long_functions", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("long functions: 0", out)
        self.assertNotIn("long_but_simple", out)

    def test_over_threshold_rows(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_long_functions", str(self.repo), "--threshold", "10"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("over_threshold(10)=2", out)
        self.assertIn("long functions: 2", out)
        self.assertIn("[  22L cx=  1] function complex_module.long_but_simple", out)
        self.assertIn("[  21L cx= 13] function complex_module.tangled", out)


# --- find_god_files -----------------------------------------------------------


class GodFilesCliTests(ExtendedRepoCliTest):
    def test_ranked_rows(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_god_files", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("find_god_files:", out)
        self.assertRegex(out, r"\n  total files: \d+")
        # Every indexed file gets ranked, each row carries sym/loc/in columns.
        self.assertRegex(
            out,
            r"\n    \[\d+\.\d{3}\] sym=\s*\d+ loc=\s*\d+ in=\s*\d+  \S+",
        )
        self.assertIn("helpers.py", out)
        self.assertIn("service.py", out)

    def test_json_payload(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_god_files", str(self.repo), "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total_files"], 12)
        self.assertTrue(payload["files"])


# --- find_high_fan_out --------------------------------------------------------


class FanOutCliTests(ExtendedRepoCliTest):
    def test_no_hubs_at_default_threshold(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_high_fan_out", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("threshold: 5", out)
        self.assertIn("high-fan-out files: 0", out)

    def test_rows_with_low_threshold(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "find_high_fan_out", str(self.repo), "--threshold", "1"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("threshold: 1", out)
        self.assertRegex(out, r"high-fan-out files: [1-9]")
        self.assertRegex(out, r"\n    \[\s*3\] \( 25\.0%\) imports_total=\s*4  app\.py")
        # The percentage column is formatted to one decimal place.
        self.assertRegex(
            out, r"\n    \[\s*1\] \(  8\.3%\) imports_total=\s*1  cycle_a\.py"
        )

    def test_json_payload(self) -> None:
        rc, out, _ = run_cli(
            [
                "--no-color",
                "find_high_fan_out",
                str(self.repo),
                "--threshold",
                "1",
                "--json",
            ]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["threshold"], 1)
        self.assertTrue(payload["files"])


# --- refactor_priority --------------------------------------------------------


class RefactorPriorityCliTests(ExtendedRepoCliTest):
    def test_ranked_table_with_untested_tag(self) -> None:
        rc, out, _ = run_cli(
            ["--no-color", "refactor_priority", str(self.repo), "--min-priority", "0"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("refactor_priority:", out)
        self.assertRegex(out, r"evaluated: \d+  skipped_test: 0  skipped_trivial: \d+")
        self.assertIn("threshold: 0.0", out)
        self.assertIn("candidates: 2", out)
        self.assertIn("  rank  score   cplx  lines  callers  symbol", out)
        self.assertIn("UNTEST  complex_module.tangled  [complex_module.py:6]", out)
        self.assertIn("high complexity (13)", out)
        # Every scored symbol in this test-less repo is untested.
        self.assertNotIn(" OK ", out)

    def test_json_payload(self) -> None:
        rc, out, _ = run_cli(
            [
                "--no-color",
                "refactor_priority",
                str(self.repo),
                "--min-priority",
                "0",
                "--json",
            ]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["candidates"])
        self.assertTrue(payload["candidates"][0]["is_untested"])


# --- find_todo_comments -------------------------------------------------------


class _TodoRepoMixin:
    @staticmethod
    def _build(base_repo: Path) -> None:
        (base_repo / "sketchy_notes.py").write_text(
            "def alpha():\n"
            "    # TODO: wire this up properly\n"
            "    return 1\n"
            "\n"
            "def beta(x):\n"
            "    # FIXME: this branch misses negative values and the duplicated\n"
            "    # guard below needs a real refactor before the next release cycle\n"
            "    if x:\n"
            "        if x > 0:\n"
            "            return 1\n"
            "    return 0\n"
            "\n"
            "# BUG: module-level breakage\n"
            "_X = 1\n",
            encoding="utf-8",
        )


class TodoCommentsCliTests(ExtendedRepoCliTest, _TodoRepoMixin):
    def test_hit_listing_with_tags_and_module(self) -> None:
        self._build(self.repo)
        index_repo(self.repo, full=True)
        rc, out, _ = run_cli(["--no-color", "find_todo_comments", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("find_todo_comments:", out)
        self.assertIn("files_with_todos=1", out)
        self.assertIn("total=3", out)
        self.assertIn("tags[bug=1 fixme=1 todo=1]", out)
        # BUG only reaches the low bucket here (score = tag + 0 extras < 5).
        self.assertIn("buckets[low=2 medium=1]", out)
        self.assertRegex(out, r"\n  shown: [1-9]")
        # Tags are upper-cased and space-padded to 5 columns.
        self.assertIn("TODO ", out)
        self.assertIn("FIXME", out)
        # Function-level todo carries its enclosing symbol; module-level one
        # falls back to `<module>`.
        self.assertIn("sketchy_notes.alpha", out)
        self.assertIn("<module>", out)
        self.assertIn("(cplx=", out)

    def test_clean_repo_shows_zero(self) -> None:
        rc, out, _ = run_cli(["--no-color", "find_todo_comments", str(self.repo)])
        self.assertEqual(rc, 0)
        self.assertIn("shown: 0", out)
        self.assertIn("tags[]", out)
        self.assertIn("buckets[]", out)

    def test_json_payload(self) -> None:
        self._build(self.repo)
        index_repo(self.repo, full=True)
        rc, out, _ = run_cli(
            ["--no-color", "find_todo_comments", str(self.repo), "--json"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["total_hits"], 3)
        self.assertTrue(payload["hits"])


# --- health_report ------------------------------------------------------------


class HealthReportCliTests(ExtendedRepoCliTest):
    BASE_FLAGS = ["--no-color", "health_report"]

    def _run(self, *extra: str) -> tuple[int, str, str]:
        return run_cli(
            [
                *self.BASE_FLAGS,
                str(self.repo),
                "--complexity-threshold",
                "1",
                "--long-threshold",
                "10",
                *extra,
            ]
        )

    def test_stdout_mode_prints_markdown_sections(self) -> None:
        rc, out, err = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
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
            self.assertIn(section, out)
        self.assertNotIn("empty index", out.lower())

    def test_out_mode_writes_markdown_file(self) -> None:
        out_path = self.tmp_root / "reports" / "health.md"
        rc, out, _ = self._run("--out", str(out_path))
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())
        self.assertIn("health_report written to " + str(out_path), out)
        md = out_path.read_text(encoding="utf-8")
        self.assertIn("## 1. Headline", md)
        # The one-line signal summary comes right after the path echo.
        self.assertRegex(
            out,
            r"files=12 complexity_hotspots=\d+ fan_out_hubs=\d+ dead_code=\d+"
            r" unused_imports=\d+ cycles=\d+",
        )
        self.assertNotIn("## 1. Headline", out)  # markdown went to the file

    def test_save_baseline_writes_snapshot_and_stderr_note(self) -> None:
        snap = self.tmp_root / "snap.json"
        rc, out, err = self._run("--save-baseline", str(snap))
        self.assertEqual(rc, 0)
        self.assertIn("## 1. Headline", out)
        self.assertIn("[snapshot saved to", err)
        loaded = json.loads(snap.read_text(encoding="utf-8"))
        self.assertIn("summary", loaded)
        self.assertEqual(loaded["summary"]["headline"]["files"], 12)

    def test_json_payload_shape(self) -> None:
        rc, out, _ = self._run("--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["repo_path"], str(self.repo.resolve()))
        self.assertEqual(payload["summary"]["headline"]["files"], 12)
        self.assertIn("dead_code", payload["summary"])
        self.assertIn("cycles", payload["summary"])

    def test_baseline_replay_adds_drift_section(self) -> None:
        snap = self.tmp_root / "snap.json"
        rc, _, _ = self._run("--save-baseline", str(snap))
        self.assertEqual(rc, 0)
        # Second run, stdout mode: the drift section is appended to markdown.
        rc2, out, _ = self._run("--baseline", str(snap))
        self.assertEqual(rc2, 0)
        self.assertIn("## 10. Drift since baseline", out)
        self.assertIn("**0** regression(s)", out)

    def test_baseline_with_out_reports_zero_drift_line(self) -> None:
        snap = self.tmp_root / "snap.json"
        out_path = self.tmp_root / "r.md"
        self._run("--save-baseline", str(snap))
        rc, out, _ = self._run("--baseline", str(snap), "--out", str(out_path))
        self.assertEqual(rc, 0)
        self.assertIn("drift: 0 regression(s), 0 improvement(s)", out)
        self.assertIn(
            "## 10. Drift since baseline", out_path.read_text(encoding="utf-8")
        )

    def test_baseline_detects_real_regressions(self) -> None:
        snap = self.tmp_root / "snap.json"
        self._run("--save-baseline", str(snap))
        # Fake a rosier past: no dead code and no cycles in the baseline.
        snap_data = json.loads(snap.read_text(encoding="utf-8"))
        snap_data["summary"]["dead_code"] = {"count": 0, "symbols": []}
        snap_data["summary"]["cycles"] = {"count": 0, "groups": []}
        snap.write_text(json.dumps(snap_data), encoding="utf-8")
        rc, out, _ = self._run("--baseline", str(snap), "--json")
        self.assertEqual(rc, 0)
        drift = json.loads(out)["summary"]["drift"]
        self.assertGreater(drift["regression_count"], 0)
        names = {s["name"] for s in drift["sets"]}
        self.assertIn("dead code symbols", names)
        self.assertIn("import cycles", names)

    def test_missing_baseline_file_returns_2(self) -> None:
        rc, out, err = self._run("--baseline", str(self.tmp_root / "ghost.json"))
        self.assertEqual(rc, 2)
        self.assertIn("cannot read baseline", err)
        self.assertNotIn("## 1.", out)

    def test_baseline_with_wrong_shape_returns_2(self) -> None:
        bad = self.tmp_root / "bad.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        rc, out, err = self._run("--baseline", str(bad))
        self.assertEqual(rc, 2)
        self.assertIn("does not look like a health_report snapshot", err)


# --- global error paths --------------------------------------------------------


class ErrorPathCliTests(SampleRepoCliTest):
    def test_missing_repo_returns_2_with_error_line(self) -> None:
        rc, out, err = run_cli(["--no-color", "index", str(self.tmp_root / "ghost")])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("error: repo not found or not a directory", err)

    def test_version_flag_prints_and_exits(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(f"jarvis-graph {__version__}", buf.getvalue())

    def test_missing_subcommand_exits_via_argparse(self) -> None:
        errbuf = io.StringIO()
        with redirect_stderr(errbuf):
            with self.assertRaises(SystemExit) as ctx:
                cli.main([])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("the following arguments are required", errbuf.getvalue())

    def test_unknown_subcommand_is_rejected(self) -> None:
        errbuf = io.StringIO()
        with redirect_stderr(errbuf):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["frobnicate"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("invalid choice", errbuf.getvalue())


# --- colour handling ------------------------------------------------------------


class ColourAlwaysCliTests(ExtendedRepoCliTest):
    ALWAYS = "--color"
    VALUE = "always"

    def _run_always(self, *args: str) -> tuple[int, str, str]:
        return run_cli([self.ALWAYS, self.VALUE, *args])

    def test_hit_line_carries_bold_dim_cyan_blue(self) -> None:
        rc, out, _ = self._run_always("query", str(self.repo), "format_greeting")
        self.assertEqual(rc, 0)
        self.assertIn("\x1b[1m", out)  # bold: score + symbol name
        self.assertIn("\x1b[2m", out)  # dim: qualified name + snippet
        self.assertIn("\x1b[36m", out)  # cyan: function kind
        self.assertIn("\x1b[34m", out)  # blue: hit path
        self.assertIn("\x1b[0m", out)  # reset terminates every run

    def test_class_kind_is_magenta(self) -> None:
        rc, out, _ = self._run_always("query", str(self.repo), "GreetingService")
        self.assertEqual(rc, 0)
        self.assertIn("\x1b[35m", out)  # magenta: class kind

    def test_constant_kind_is_yellow(self) -> None:
        rc, out, _ = self._run_always("query", str(self.repo), "DEFAULT_PREFIX")
        self.assertEqual(rc, 0)
        self.assertTrue(out.strip())
        self.assertIn("\x1b[33m", out)  # yellow: constant kind

    def test_dead_code_count_is_red_when_dead_exist(self) -> None:
        rc, out, _ = self._run_always("find_dead_code", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertIn("\x1b[31m", out)  # red: dead candidates count

    def test_no_hits_is_dimmed(self) -> None:
        rc, out, _ = self._run_always(
            "query", str(self.repo), "definitelynotinthisrepo_xyz"
        )
        self.assertEqual(rc, 0)
        self.assertIn("\x1b[2m(no hits)\x1b[0m", out)


class ColourOffCliTests(SampleRepoCliTest):
    """Every suppression route must produce ANSI-free output."""

    def _run_plain_mode(self, *prefix: str) -> tuple[int, str, str]:
        return run_cli([*prefix, "query", str(self.repo), "format_greeting"])

    def test_no_color_flag(self) -> None:
        rc, out, err = self._run_plain_mode("--no-color")
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x1b", err)

    def test_never_choice(self) -> None:
        rc, out, err = self._run_plain_mode("--color", "never")
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x1b", err)

    def test_no_color_env_var_wins_over_auto(self) -> None:
        os.environ["NO_COLOR"] = "1"
        rc, out, err = self._run_plain_mode()  # default: auto
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x1b", err)

    def test_auto_non_tty_stdout_is_plain(self) -> None:
        rc, out, err = self._run_plain_mode()
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b", out)
        # Sanity: the run still produced output.
        self.assertIn("hit(s) for: format_greeting", out)

    def test_no_unused_imports_paints_green(self) -> None:
        # Colour-mode pairing for the green hue: sample repo has zero unused.
        rc, out, _ = run_cli(
            ["--color", "always", "find_unused_imports", str(self.repo)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("\x1b[32m", out)  # green: unused count of 0
        self.assertRegex(out, r"unused:\s+\x1b\[32m0\x1b\[0m")
        self.assertRegex(out, r"total imports: 11")


if __name__ == "__main__":
    unittest.main()
