"""Behaviour tests for jarvis_graph.ranker scoring helpers.

Scores are pinned to exact numbers so that *any* change to the tier boundaries
or tier values (hidden mutation testing) breaks at least one assertion.
"""

from __future__ import annotations

import unittest

from conftest import ROOT  # noqa: F401  ensures src/ is on sys.path

from jarvis_graph.ranker import (
    score_docstring,
    score_path,
    score_qname,
    score_symbol_name,
)


class ScoreSymbolNameTests(unittest.TestCase):
    def test_exact_match_scores_100_case_insensitive(self) -> None:
        self.assertEqual(score_symbol_name("greet", "greet"), 100)
        self.assertEqual(score_symbol_name("Greet", "GREET"), 100)
        self.assertEqual(score_symbol_name("Format_Greeting", "format_greeting"), 100)

    def test_prefix_match_scores_exactly_75(self) -> None:
        # "format" both starts with AND is contained in "format_greeting";
        # the prefix rule must win, so the score must be exactly 75 (not 55).
        self.assertEqual(score_symbol_name("format_greeting", "format"), 75)
        self.assertEqual(score_symbol_name("format_greeting", "FORMAT"), 75)

    def test_substring_only_scores_exactly_55(self) -> None:
        self.assertEqual(score_symbol_name("my_func", "func"), 55)
        self.assertEqual(score_symbol_name("my_func", "_FUN"), 55)

    def test_no_match_scores_0(self) -> None:
        self.assertEqual(score_symbol_name("greet", "parse"), 0)
        self.assertEqual(score_symbol_name("", "greet"), 0)

    def test_empty_query_pins_current_prefix_quirk(self) -> None:
        # "" .startswith(g) style edge: every name "starts with" the empty
        # query, so the code currently scores 75. Pinned as-is on purpose.
        self.assertEqual(score_symbol_name("greet", ""), 75)

    def test_exact_beats_prefix(self) -> None:
        # A name equal to the query must score higher than any prefix hit.
        self.assertGreater(score_symbol_name("greet", "greet"), score_symbol_name("greeting", "greet"))


class ScoreQnameTests(unittest.TestCase):
    def test_exact_qname_scores_exactly_90(self) -> None:
        self.assertEqual(score_qname("pkg.mod.greet", "pkg.mod.greet"), 90)
        self.assertEqual(score_qname("pkg.MOD", "pkg.mod"), 90)  # case-insensitive

    def test_last_segment_scores_exactly_65(self) -> None:
        # "greet" is a substring of "pkg.mod.greet" but only the trailing
        # ".greet" qualifies for the stronger tier, so exactly 65 (not 40).
        self.assertEqual(score_qname("pkg.mod.greet", "greet"), 65)
        self.assertEqual(score_qname("helpers.format_greeting", "format_greeting"), 65)

    def test_substring_only_scores_exactly_40(self) -> None:
        self.assertEqual(score_qname("my.greeting", "greet"), 40)
        self.assertEqual(score_qname("myfoo", "foo"), 40)
        self.assertEqual(score_qname("pkg.mod.greet", "od.gre"), 40)

    def test_no_match_scores_0(self) -> None:
        self.assertEqual(score_qname("pkg.mod.greet", "worker"), 0)

    def test_empty_query_pins_current_substring_quirk(self) -> None:
        # The empty query is "contained" in every qname, so the code
        # currently scores 40. Pinned as-is on purpose.
        self.assertEqual(score_qname("pkg.mod.greet", ""), 40)

    def test_exact_qname_beats_last_segment(self) -> None:
        self.assertGreater(score_qname("mod.greet", "mod.greet"), score_qname("a.mod.greet", "mod.greet"))


class ScorePathTests(unittest.TestCase):
    def test_substring_match_scores_exactly_35(self) -> None:
        self.assertEqual(score_path("pkg/my_module.py", "my_module"), 35)
        self.assertEqual(score_path("pkg/my_module.py", "MY MODULE"), 35)  # space -> _

    def test_query_spaces_become_underscores(self) -> None:
        # "my file" must match "my_file.py" but NOT "my-file.py".
        self.assertEqual(score_path("src/my_file.py", "my file"), 35)
        self.assertEqual(score_path("src/my-file.py", "my file"), 0)

    def test_match_is_case_insensitive(self) -> None:
        self.assertEqual(score_path("SRC/Helpers.PY", "helpers"), 35)

    def test_no_match_scores_0(self) -> None:
        self.assertEqual(score_path("pkg/other.py", "helpers"), 0)

    def test_empty_query_pins_current_substring_quirk(self) -> None:
        # The empty query is "contained" in every path, so the code
        # currently scores 35. Pinned as-is on purpose.
        self.assertEqual(score_path("pkg/other.py", ""), 35)


class ScoreDocstringTests(unittest.TestCase):
    def test_none_and_empty_docstring_score_0(self) -> None:
        self.assertEqual(score_docstring(None, "greet"), 0)
        self.assertEqual(score_docstring("", "greet"), 0)

    def test_match_scores_exactly_20(self) -> None:
        self.assertEqual(score_docstring("Greets the user politely.", "greet"), 20)
        self.assertEqual(score_docstring("Greets the user politely.", "GREET"), 20)

    def test_no_match_scores_0(self) -> None:
        self.assertEqual(score_docstring("Greets the user politely.", "parse"), 0)


if __name__ == "__main__":
    unittest.main()
