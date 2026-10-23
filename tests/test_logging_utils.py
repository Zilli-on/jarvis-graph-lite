"""Behaviour tests for jarvis_graph.logging_utils (append-only ops log)."""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from conftest import ROOT  # noqa: F401  ensures src/ is on sys.path

from jarvis_graph.logging_utils import log, log_path

_TS_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00"


class LoggingUtilsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="jgl_log_test_"))
        self.repo = self._tmp / "repo"
        self.repo.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _lines(self, logfile: Path) -> list[str]:
        with logfile.open(encoding="utf-8") as f:
            return f.read().splitlines(keepends=True)

    def test_log_path_is_under_dotjarvis_graph_logs(self) -> None:
        self.assertEqual(
            log_path(self.repo),
            self.repo / ".jarvis_graph" / "logs" / "operations.log",
        )

    def test_log_creates_directories_and_writes_tab_separated_line(self) -> None:
        # Neither .jarvis_graph nor logs/ exists yet.
        self.assertFalse((self.repo / ".jarvis_graph").exists())
        log(self.repo, "index", "full scan")
        logfile = log_path(self.repo)
        self.assertTrue(logfile.exists())
        lines = self._lines(logfile)
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], rf"^{_TS_RE}\tindex\tfull scan\n$")

    def test_appends_without_truncating(self) -> None:
        log(self.repo, "index", "run one")
        log(self.repo, "query", "run two")
        lines = self._lines(log_path(self.repo))
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], rf"^{_TS_RE}\tindex\trun one\n$")
        self.assertRegex(lines[1], rf"^{_TS_RE}\tquery\trun two\n$")

    def test_default_detail_is_empty_string(self) -> None:
        log(self.repo, "health")
        lines = self._lines(log_path(self.repo))
        # No detail at all: the line must end right after the tab.
        self.assertEqual(lines[0].count("\t"), 2)
        self.assertRegex(lines[0], rf"^{_TS_RE}\thealth\t\n$")

    def test_timestamp_is_utc_isoformat_with_seconds(self) -> None:
        log(self.repo, "index", "")
        line = self._lines(log_path(self.repo))[0]
        ts = line.split("\t")[0]
        # Seconds granularity: no microseconds, no "Z" suffix, explicit UTC offset.
        self.assertNotIn(".", ts)
        self.assertTrue(ts.endswith("+00:00"), ts)
        self.assertIsNotNone(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", ts)
        )

    def test_log_survives_pre_existing_file(self) -> None:
        logfile = log_path(self.repo)
        logfile.parent.mkdir(parents=True, exist_ok=True)
        logfile.write_text("PRE-EXISTING HEADER\n", encoding="utf-8")
        log(self.repo, "index", "after header")
        text = logfile.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("PRE-EXISTING HEADER\n"))
        self.assertTrue(text.rstrip("\n").split("\n")[-1].endswith("index\tafter header"))


if __name__ == "__main__":
    unittest.main()
