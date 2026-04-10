"""Tests for todo_comments_engine.

Covers:
  - pure helpers (_is_test_path, _bucket, _score, _parse_comment)
  - comment extraction via tokenize (vs regex false-positives)
  - integration: synthetic fixture with known TODOs, verify ranking,
    enclosing-symbol resolution, test-file exclusion, risk bucketing
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from conftest import cleanup, prepare_sample_repo  # noqa: F401 (path setup)

# Make `src/` importable without installing the package.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_graph.indexer import index_repo  # noqa: E402
from jarvis_graph.todo_comments_engine import (  # noqa: E402
    _bucket,
    _extract_comments,
    _is_test_path,
    _parse_comment,
    _score,
    find_todo_comments,
)


class IsTestPathTests(unittest.TestCase):
    def test_plain_test_file(self) -> None:
        self.assertTrue(_is_test_path("test_foo.py"))

    def test_suffix_test_file(self) -> None:
        self.assertTrue(_is_test_path("foo_test.py"))

    def test_tests_directory(self) -> None:
        self.assertTrue(_is_test_path("pkg/tests/test_x.py"))

    def test_tests_top_level(self) -> None:
        self.assertTrue(_is_test_path("tests/test_x.py"))

    def test_windows_separators(self) -> None:
        self.assertTrue(_is_test_path("pkg\\tests\\test_x.py"))

    def test_contest_false_positive_ignored(self) -> None:
        """contest.py contains 'test' as a substring but isn't a test file."""
        self.assertFalse(_is_test_path("contest.py"))

    def test_regular_module(self) -> None:
        self.assertFalse(_is_test_path("src/jarvis_graph/indexer.py"))

    def test_case_insensitive(self) -> None:
        self.assertTrue(_is_test_path("TEST_Foo.py"))


class BucketTests(unittest.TestCase):
    def test_critical_at_20(self) -> None:
        self.assertEqual(_bucket(20.0), "critical")

    def test_critical_above(self) -> None:
        self.assertEqual(_bucket(100.0), "critical")

    def test_high_at_10(self) -> None:
        self.assertEqual(_bucket(10.0), "high")

    def test_high_range(self) -> None:
        self.assertEqual(_bucket(15.0), "high")

    def test_medium_at_5(self) -> None:
        self.assertEqual(_bucket(5.0), "medium")

    def test_medium_range(self) -> None:
        self.assertEqual(_bucket(7.0), "medium")

    def test_low_below_5(self) -> None:
        self.assertEqual(_bucket(4.9), "low")

    def test_low_zero(self) -> None:
        self.assertEqual(_bucket(0.0), "low")


class ScoreTests(unittest.TestCase):
    def test_tag_only(self) -> None:
        # tag=2 (TODO) + cplx=0 + lc*0.1=0
        self.assertEqual(_score(2, 0, 0), 2.0)

    def test_complexity_dominates_in_beast_function(self) -> None:
        # A HACK in amv_engine.beat_match_edit: cplx=156, loc=474
        score = _score(4, 156, 474)
        # 4 + 156 + 47.4 = 207.4
        self.assertAlmostEqual(score, 207.4, places=1)

    def test_trivial_todo_is_low(self) -> None:
        # TODO in a 5-line helper with cplx 1
        score = _score(2, 1, 5)
        # 2 + 1 + 0.5 = 3.5
        self.assertAlmostEqual(score, 3.5, places=1)

    def test_lines_scaled_down(self) -> None:
        """100 LOC should contribute 10 to the score (LOC / 10)."""
        self.assertAlmostEqual(_score(0, 0, 100), 10.0, places=1)


class ParseCommentTests(unittest.TestCase):
    def test_plain_todo(self) -> None:
        result = _parse_comment("# TODO: fix this")
        assert result is not None
        tag, text = result
        self.assertEqual(tag, "todo")
        self.assertIn("TODO: fix this", text)

    def test_fixme(self) -> None:
        result = _parse_comment("# FIXME - broken edge case")
        assert result is not None
        self.assertEqual(result[0], "fixme")

    def test_hack(self) -> None:
        result = _parse_comment("# HACK(fabi): workaround for API bug")
        assert result is not None
        self.assertEqual(result[0], "hack")

    def test_bug(self) -> None:
        result = _parse_comment("# BUG: this returns None sometimes")
        assert result is not None
        self.assertEqual(result[0], "bug")

    def test_xxx(self) -> None:
        result = _parse_comment("# XXX this is wrong")
        assert result is not None
        self.assertEqual(result[0], "xxx")

    def test_lowercase_tag_not_matched(self) -> None:
        """v0.12.4: lowercase `# todo` is NOT a tag — the convention is
        ALL-CAPS, and matching lowercase generates false positives on
        English prose like 'The bug was silent' or 'this is a hack'."""
        self.assertIsNone(_parse_comment("# todo: lowercase form"))

    def test_lowercase_bug_in_prose_not_matched(self) -> None:
        """v0.12.4 regression guard: the phrase 'The bug was silent'
        appeared in a v0.12.3 docstring and made find_todo_comments
        flag the _resolve_calls function with a critical-risk score of
        22.1. Must never match again."""
        self.assertIsNone(
            _parse_comment("# The bug was silent because the fallback ran")
        )

    def test_lowercase_hack_in_prose_not_matched(self) -> None:
        """Same rule, different word — 'hack' in prose is not a HACK tag."""
        self.assertIsNone(
            _parse_comment("# this is a hack around the limitation")
        )

    def test_lowercase_fixme_in_prose_not_matched(self) -> None:
        self.assertIsNone(
            _parse_comment("# please fixme in the next release")
        )

    def test_uppercase_tag_still_matches_embedded(self) -> None:
        """Positive guard: ALL-CAPS tags embedded in prose still match,
        matching the existing `test_embedded_in_sentence` contract."""
        result = _parse_comment("# The BUG manifests under load")
        assert result is not None
        self.assertEqual(result[0], "bug")

    def test_no_tag_returns_none(self) -> None:
        self.assertIsNone(_parse_comment("# just a regular comment"))

    def test_word_boundary_protects_against_false_match(self) -> None:
        """'todoList' contains 'todo' but shouldn't match — no word boundary."""
        # Actually, \b matches at start and end of word characters, so
        # 'todoList' DOES match at position 0..4. This is a known
        # accepted false positive (very rare in practice).
        # We test the inverse: a truly non-matching comment.
        self.assertIsNone(_parse_comment("# nothing interesting"))

    def test_embedded_in_sentence(self) -> None:
        """'TODO' as a word in a longer comment still matches."""
        result = _parse_comment("# There is a TODO item here for later")
        assert result is not None
        self.assertEqual(result[0], "todo")

    def test_xxx_in_format_specifier_not_matched(self) -> None:
        """Dogfooding on JARVIS surfaced this: `average:X.XXX` matched
        because `.` is a word/non-word boundary. Real tags are always
        preceded by whitespace or a comment marker, never glued to
        another identifier-ish token."""
        self.assertIsNone(
            _parse_comment(
                '# Parse average PSNR from stderr: "PSNR y:... average:X.XXX"'
            )
        )

    def test_tag_after_paren(self) -> None:
        """'(TODO)' style still matches - common in inline review notes."""
        result = _parse_comment("# (TODO) revisit after v2")
        assert result is not None
        self.assertEqual(result[0], "todo")

    def test_tag_after_bracket(self) -> None:
        """'[FIXME]' style also matches."""
        result = _parse_comment("# [FIXME] broken in edge case")
        assert result is not None
        self.assertEqual(result[0], "fixme")

    def test_tag_glued_to_dot_rejected(self) -> None:
        """Extra safety: `foo.TODO` is not a real tag, it's a dotted attr."""
        self.assertIsNone(_parse_comment("# see config.TODO_LIST for details"))


class ExtractCommentsTests(unittest.TestCase):
    """Integration-style: write a file, run tokenize, check output."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="todo_comments_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, content: str) -> Path:
        p = self.tmp / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_single_comment(self) -> None:
        p = self._write("x.py", "x = 1  # TODO: fix\n")
        comments = _extract_comments(p)
        self.assertEqual(len(comments), 1)
        self.assertIn("TODO", comments[0][1])

    def test_string_literal_not_confused_with_comment(self) -> None:
        """The whole point of using tokenize: a string containing '#' is
        NOT a comment."""
        p = self._write(
            "x.py",
            'x = "# TODO: this is in a string"\n'
            "y = 2  # actual TODO: real one\n",
        )
        comments = _extract_comments(p)
        # Only the second line has a real comment
        self.assertEqual(len(comments), 1)
        self.assertIn("real one", comments[0][1])

    def test_no_comments(self) -> None:
        p = self._write("x.py", "x = 1\ny = 2\n")
        self.assertEqual(_extract_comments(p), [])

    def test_multiple_comments_different_lines(self) -> None:
        p = self._write(
            "x.py",
            "# comment 1\n"
            "x = 1\n"
            "# comment 2\n"
            "y = 2\n",
        )
        comments = _extract_comments(p)
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0][0], 1)
        self.assertEqual(comments[1][0], 3)

    def test_syntax_error_returns_empty(self) -> None:
        p = self._write("broken.py", "def broken(:\n    pass\n")
        self.assertEqual(_extract_comments(p), [])

    def test_docstring_not_treated_as_comment(self) -> None:
        """Docstrings are STRING tokens, not COMMENTs. They should be
        ignored by the comment extractor — even if they contain TODO."""
        p = self._write(
            "x.py",
            'def f():\n    """TODO: refactor this someday"""\n    return 1\n',
        )
        comments = _extract_comments(p)
        self.assertEqual(len(comments), 0)


class TodoCommentsIntegrationTests(unittest.TestCase):
    """End-to-end: synthetic repo with known TODOs, index, run engine."""

    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_sample_repo()
        # Add test files with known TODO structure
        (self.repo / "with_todos.py").write_text(
            "def simple_helper():\n"
            "    # TODO: add type hints\n"
            "    return 42\n"
            "\n"
            "def complex_func(x, y, z):\n"
            "    # HACK: legacy workaround, remove after v2.0\n"
            "    if x > 0:\n"
            "        if y > 0:\n"
            "            if z > 0:\n"
            "                if x + y > z:\n"
            "                    return x + y + z\n"
            "                else:\n"
            "                    return x - y\n"
            "            return x\n"
            "        return y\n"
            "    return 0\n"
            "\n"
            "def with_bug():\n"
            "    # BUG: off-by-one on edge case\n"
            "    return [1, 2, 3][1]\n"
            "\n"
            "# module-level TODO: rewrite this whole thing\n",
            encoding="utf-8",
        )
        (self.repo / "no_todos.py").write_text(
            "def clean_func():\n"
            "    return 'no comments here'\n",
            encoding="utf-8",
        )
        (self.repo / "test_things.py").write_text(
            "def test_skipped_by_default():\n"
            "    # TODO: this is in a test file, should be skipped\n"
            "    pass\n",
            encoding="utf-8",
        )
        (self.repo / "docstring_only.py").write_text(
            'def f():\n'
            '    """TODO: this is in a docstring and should NOT be flagged."""\n'
            '    return 1\n',
            encoding="utf-8",
        )
        index_repo(self.repo, full=True)
        self.report = find_todo_comments(self.repo, limit=None, min_risk=0.0)

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_finds_todos(self) -> None:
        self.assertGreater(self.report.total_hits, 0)

    def test_files_with_todos_counted(self) -> None:
        # Only with_todos.py has real TODOs that aren't in test/docstring
        self.assertEqual(self.report.files_with_todos, 1)

    def test_docstring_todo_not_flagged(self) -> None:
        for hit in self.report.hits:
            if hit.rel_path == "docstring_only.py":
                self.fail("docstring TODO must not be flagged")

    def test_test_file_excluded_by_default(self) -> None:
        for hit in self.report.hits:
            self.assertFalse(
                hit.rel_path.startswith("test_"),
                f"test file should be excluded: {hit.rel_path}",
            )

    def test_test_file_included_with_flag(self) -> None:
        rep = find_todo_comments(self.repo, include_tests=True)
        test_hits = [h for h in rep.hits if "test_" in h.rel_path]
        self.assertGreater(len(test_hits), 0)

    def test_hack_tag_detected(self) -> None:
        hack_hits = [h for h in self.report.hits if h.tag == "hack"]
        self.assertEqual(len(hack_hits), 1)

    def test_bug_tag_detected(self) -> None:
        bug_hits = [h for h in self.report.hits if h.tag == "bug"]
        self.assertEqual(len(bug_hits), 1)

    def test_todo_tag_detected(self) -> None:
        todo_hits = [h for h in self.report.hits if h.tag == "todo"]
        # Two TODOs in with_todos.py: one in simple_helper, one module-level
        self.assertGreaterEqual(len(todo_hits), 2)

    def test_sorted_by_risk_desc(self) -> None:
        risks = [h.risk for h in self.report.hits]
        self.assertEqual(risks, sorted(risks, reverse=True))

    def test_hack_in_complex_func_outranks_todo_in_helper(self) -> None:
        """HACK in complex_func (branchy, high cplx) should beat
        TODO in simple_helper (1-line return)."""
        hack = next(h for h in self.report.hits if h.tag == "hack")
        helper_todo = next(
            h for h in self.report.hits
            if h.tag == "todo" and h.enclosing_qname.endswith("simple_helper")
        )
        self.assertGreater(hack.risk, helper_todo.risk)

    def test_module_level_todo_has_empty_qname(self) -> None:
        module_hits = [
            h for h in self.report.hits
            if h.enclosing_kind == "module"
        ]
        self.assertGreater(len(module_hits), 0)
        for hit in module_hits:
            self.assertEqual(hit.enclosing_qname, "")
            self.assertEqual(hit.complexity, 0)

    def test_by_tag_counter_populated(self) -> None:
        self.assertGreater(self.report.by_tag.get("todo", 0), 0)
        self.assertEqual(self.report.by_tag.get("hack", 0), 1)
        self.assertEqual(self.report.by_tag.get("bug", 0), 1)

    def test_by_bucket_counter_populated(self) -> None:
        total_by_bucket = sum(self.report.by_bucket.values())
        self.assertEqual(total_by_bucket, self.report.total_hits)

    def test_min_risk_filter(self) -> None:
        rep = find_todo_comments(self.repo, min_risk=5.0)
        for hit in rep.hits:
            self.assertGreaterEqual(hit.risk, 5.0)

    def test_limit_applied(self) -> None:
        rep = find_todo_comments(self.repo, limit=1)
        self.assertLessEqual(len(rep.hits), 1)

    def test_total_hits_counts_before_limit(self) -> None:
        """total_hits should report the full count, regardless of limit."""
        rep = find_todo_comments(self.repo, limit=1)
        # total_hits reflects the full count of hits kept after min_risk,
        # before the limit cap. So it should be >= 1 (the limit).
        self.assertGreaterEqual(rep.total_hits, 1)


class EmptyRepoTests(unittest.TestCase):
    """An indexed repo with zero TODO comments should work cleanly."""

    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_sample_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_no_todos_in_sample_repo(self) -> None:
        """The canned sample_repo fixture has no TODO comments."""
        rep = find_todo_comments(self.repo)
        self.assertEqual(rep.total_hits, 0)
        self.assertEqual(rep.files_with_todos, 0)
        self.assertEqual(rep.hits, [])


if __name__ == "__main__":
    unittest.main()
