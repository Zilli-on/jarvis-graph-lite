"""Tests for find_path (v0.7).

Builds a synthetic repo with a known call graph and asserts BFS finds the
expected chains.

Layout:
  entry.py:
    def entry():
        return level_one()
  step1.py:
    from step2 import level_two
    def level_one():
        return level_two()
  step2.py:
    from step3 import level_three
    def level_two():
        return level_three()
  step3.py:
    def level_three():
        return 42
  branch.py:
    from step3 import level_three
    def alt_route():        # alternative second-level path to level_three
        return level_three()
  unreachable.py:
    def lonely():           # has no caller anywhere; nothing reaches it
        return 0
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from conftest import cleanup  # noqa: F401 (path setup)

from jarvis_graph.find_path_engine import find_path
from jarvis_graph.indexer import index_repo


def _make_chain_repo() -> tuple[Path, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_findpath_"))
    repo = tmp_root / "chain_repo"
    repo.mkdir()
    (repo / "step3.py").write_text(
        "def level_three() -> int:\n"
        "    return 42\n",
        encoding="utf-8",
    )
    (repo / "step2.py").write_text(
        "from step3 import level_three\n"
        "\n"
        "def level_two() -> int:\n"
        "    return level_three()\n",
        encoding="utf-8",
    )
    (repo / "step1.py").write_text(
        "from step2 import level_two\n"
        "\n"
        "def level_one() -> int:\n"
        "    return level_two()\n",
        encoding="utf-8",
    )
    (repo / "entry.py").write_text(
        "from step1 import level_one\n"
        "\n"
        "def entry() -> int:\n"
        "    return level_one()\n",
        encoding="utf-8",
    )
    (repo / "branch.py").write_text(
        "from step3 import level_three\n"
        "\n"
        "def alt_route() -> int:\n"
        "    return level_three()\n",
        encoding="utf-8",
    )
    (repo / "unreachable.py").write_text(
        "def lonely() -> int:\n"
        "    return 0\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


class FindPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_chain_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_linear_chain_resolves_in_order(self) -> None:
        res = find_path(self.repo, "entry", "level_three")
        self.assertTrue(res.found, msg=res.note)
        names = [s.qualified_name for s in res.steps]
        self.assertEqual(
            names,
            ["entry.entry", "step1.level_one", "step2.level_two", "step3.level_three"],
        )
        self.assertEqual(res.depth, 3)

    def test_one_step_path(self) -> None:
        # alt_route → level_three is one direct hop
        res = find_path(self.repo, "alt_route", "level_three")
        self.assertTrue(res.found, msg=res.note)
        self.assertEqual(res.depth, 1)
        self.assertEqual(len(res.steps), 2)
        self.assertEqual(res.steps[-1].qualified_name, "step3.level_three")

    def test_source_equals_target_is_zero_step(self) -> None:
        res = find_path(self.repo, "level_three", "level_three")
        self.assertTrue(res.found)
        self.assertEqual(res.depth, 0)
        self.assertEqual(len(res.steps), 1)

    def test_unreachable_target_returns_not_found(self) -> None:
        res = find_path(self.repo, "entry", "lonely")
        self.assertFalse(res.found)
        self.assertIn("no resolved call path", res.note)

    def test_unresolvable_source_reports_clearly(self) -> None:
        res = find_path(self.repo, "no_such_function", "level_three")
        self.assertFalse(res.found)
        self.assertIn("source not resolvable", res.note)

    def test_unresolvable_target_reports_clearly(self) -> None:
        res = find_path(self.repo, "entry", "no_such_function")
        self.assertFalse(res.found)
        self.assertIn("target not resolvable", res.note)

    def test_max_depth_filters_long_paths(self) -> None:
        # Linear chain is 3 hops; depth 1 cannot reach the target.
        res = find_path(self.repo, "entry", "level_three", max_depth=1)
        self.assertFalse(res.found)
        self.assertIn("within depth 1", res.note)

    def test_dotted_qname_resolution_works(self) -> None:
        # Dotted name should resolve via _resolve_target's qname suffix path.
        res = find_path(self.repo, "entry.entry", "step3.level_three")
        self.assertTrue(res.found, msg=res.note)
        self.assertEqual(res.steps[-1].qualified_name, "step3.level_three")

    def test_nodes_explored_is_reported(self) -> None:
        res = find_path(self.repo, "entry", "level_three")
        # Should at least visit source + each chain link.
        self.assertGreaterEqual(res.nodes_explored, 3)

    def test_steps_carry_call_site_lineno(self) -> None:
        # The line attached to step N (N>0) should be the line where step N-1
        # calls into it (a `return level_X()` line in our fixture).
        res = find_path(self.repo, "entry", "level_three")
        self.assertTrue(res.found)
        # Each non-source step has a positive lineno from a call_edge.
        for step in res.steps[1:]:
            self.assertGreater(step.lineno, 0)


if __name__ == "__main__":
    unittest.main()
