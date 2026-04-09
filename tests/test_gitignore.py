"""GitignoreMatcher / GitignoreStack / iter_python_files smoke tests."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from conftest import ROOT  # noqa: F401  ensures src/ is on sys.path

from jarvis_graph.gitignore import GitignoreMatcher, GitignoreStack
from jarvis_graph.utils import iter_python_files


class GitignoreMatcherTests(unittest.TestCase):
    def test_basename_glob(self) -> None:
        m = GitignoreMatcher(["*.pyc"])
        self.assertTrue(m.match("foo.pyc", False))
        self.assertTrue(m.match("pkg/sub/foo.pyc", False))
        self.assertIsNone(m.match("foo.py", False))

    def test_directory_only_pattern(self) -> None:
        m = GitignoreMatcher(["__pycache__/"])
        self.assertTrue(m.match("__pycache__", True))
        # File named __pycache__ at root must NOT match because of trailing /.
        self.assertIsNone(m.match("__pycache__", False))
        # Files inside an ignored directory must be reported as ignored
        # because the parent directory matches.
        self.assertTrue(m.match("pkg/__pycache__/foo.pyc", False))

    def test_anchored_pattern(self) -> None:
        m = GitignoreMatcher(["/build/"])
        self.assertTrue(m.match("build", True))
        self.assertTrue(m.match("build/output.py", False))
        # Subdir named "build" deeper in the tree must NOT match because
        # the leading slash anchors to the gitignore root.
        self.assertIsNone(m.match("pkg/build", True))
        self.assertIsNone(m.match("pkg/build/output.py", False))

    def test_internal_slash_is_anchored(self) -> None:
        m = GitignoreMatcher(["workspace/generated_projects/"])
        self.assertTrue(m.match("workspace/generated_projects", True))
        self.assertTrue(m.match("workspace/generated_projects/proj/main.py", False))
        self.assertIsNone(m.match("other/workspace/generated_projects", True))

    def test_double_star(self) -> None:
        m = GitignoreMatcher(["**/cache"])
        self.assertTrue(m.match("cache", True))
        self.assertTrue(m.match("a/b/cache", True))
        self.assertIsNone(m.match("notcache", True))

    def test_negation(self) -> None:
        m = GitignoreMatcher(["*.log", "!keep.log"])
        self.assertTrue(m.match("foo.log", False))
        self.assertEqual(m.match("keep.log", False), False)

    def test_comments_and_blank_lines(self) -> None:
        m = GitignoreMatcher([
            "# comment",
            "",
            "   ",
            "*.pyc",
        ])
        self.assertTrue(m.match("a.pyc", False))

    def test_char_class(self) -> None:
        m = GitignoreMatcher(["foo[123].py"])
        self.assertTrue(m.match("foo1.py", False))
        self.assertTrue(m.match("foo3.py", False))
        self.assertIsNone(m.match("foo4.py", False))


class GitignoreStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="jgl_gitignore_"))
        # Outer .gitignore: *.log
        # Inner .gitignore: !important.log (re-include)
        (self.tmp_root / "inner").mkdir()
        (self.tmp_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
        (self.tmp_root / "inner" / ".gitignore").write_text(
            "!important.log\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_outer_only(self) -> None:
        stack = GitignoreStack()
        stack.push(self.tmp_root, self.tmp_root / ".gitignore")
        self.assertTrue(stack.is_ignored(self.tmp_root / "x.log", False))

    def test_inner_negation_overrides_outer(self) -> None:
        stack = GitignoreStack()
        stack.push(self.tmp_root, self.tmp_root / ".gitignore")
        stack.push(self.tmp_root / "inner", self.tmp_root / "inner" / ".gitignore")
        # Inner re-includes important.log inside `inner/`.
        self.assertFalse(
            stack.is_ignored(self.tmp_root / "inner" / "important.log", False)
        )
        # Other .log files inside inner are still ignored by the outer rule.
        self.assertTrue(
            stack.is_ignored(self.tmp_root / "inner" / "other.log", False)
        )

    def test_pop_drops_layer(self) -> None:
        stack = GitignoreStack()
        stack.push(self.tmp_root, self.tmp_root / ".gitignore")
        stack.pop()
        self.assertFalse(stack.is_ignored(self.tmp_root / "x.log", False))


class WalkerGitignoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="jgl_walker_"))
        # Build a small repo with some ignored content.
        (self.tmp_root / "src").mkdir()
        (self.tmp_root / "generated").mkdir()
        (self.tmp_root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
        (self.tmp_root / "src" / "skip_me.py").write_text("y = 2\n", encoding="utf-8")
        (self.tmp_root / "generated" / "huge.py").write_text(
            "junk = 3\n", encoding="utf-8"
        )
        (self.tmp_root / ".gitignore").write_text(
            "generated/\nskip_me.py\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_gitignore_filters_files(self) -> None:
        seen = {
            str(rel).replace("\\", "/")
            for _, rel in iter_python_files(self.tmp_root, respect_gitignore=True)
        }
        self.assertIn("src/main.py", seen)
        self.assertNotIn("src/skip_me.py", seen)
        self.assertNotIn("generated/huge.py", seen)

    def test_disable_gitignore(self) -> None:
        seen = {
            str(rel).replace("\\", "/")
            for _, rel in iter_python_files(self.tmp_root, respect_gitignore=False)
        }
        self.assertIn("src/main.py", seen)
        self.assertIn("src/skip_me.py", seen)
        self.assertIn("generated/huge.py", seen)


if __name__ == "__main__":
    unittest.main()
