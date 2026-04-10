"""generate_test_skeleton smoke tests.

Validates the v0.10 test-stub generator end-to-end:
  - Looks up a symbol by qname OR bare name
  - Strips the `src.` prefix when the file lives under `src/`
  - Renders a function skeleton with `from <module> import <name>`
  - Renders a class skeleton with one test method per public method
  - Drops `self`/`cls` from method param hints
  - Refuses to overwrite an existing file unless force=True
  - Output parses as valid Python (the strongest possible round-trip check)
  - Generated test class follows the `<Subject>Tests` suffix convention
    so v0.9.2's coverage_gap_engine fix recognizes it as an entry point
"""

from __future__ import annotations

import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from conftest import cleanup, prepare_extended_repo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_graph.indexer import index_repo  # noqa: E402
from jarvis_graph.test_skeleton_engine import (  # noqa: E402
    SkeletonError,
    _params_for_signature,
    generate_test_skeleton,
    write_skeleton,
)


def _make_skeleton_repo() -> tuple[Path, Path]:
    """Synthetic repo with src/ layout: one module that defines a free
    function, an init-taking class, and a class with no init."""
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_skel_"))
    repo = tmp_root / "skel_repo"
    (repo / "src" / "mypkg").mkdir(parents=True)
    (repo / "src" / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "mypkg" / "core.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "\n"
        "def lonely() -> None:\n"
        "    pass\n"
        "\n"
        "class Greeter:\n"
        "    def __init__(self, name: str, prefix: str = 'Hello') -> None:\n"
        "        self.name = name\n"
        "        self.prefix = prefix\n"
        "\n"
        "    def greet(self) -> str:\n"
        "        return f'{self.prefix}, {self.name}'\n"
        "\n"
        "    def shout(self, volume: int) -> str:\n"
        "        return self.greet().upper() + ('!' * volume)\n"
        "\n"
        "    def _internal(self) -> int:\n"
        "        return 0\n"
        "\n"
        "class Bare:\n"
        "    pass\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


class ParseSignatureTests(unittest.TestCase):
    def test_empty_signature(self) -> None:
        self.assertEqual(_params_for_signature(None, drop_self=False), [])
        self.assertEqual(_params_for_signature("", drop_self=False), [])
        self.assertEqual(_params_for_signature("()", drop_self=False), [])

    def test_simple_params(self) -> None:
        self.assertEqual(
            _params_for_signature("(a, b, c)", drop_self=False),
            ["a", "b", "c"],
        )

    def test_annotations_and_defaults_stripped(self) -> None:
        self.assertEqual(
            _params_for_signature("(a: int, b: str = 'x', c=42)", drop_self=False),
            ["a", "b", "c"],
        )

    def test_nested_brackets_not_split(self) -> None:
        self.assertEqual(
            _params_for_signature(
                "(handler: Callable[[int, str], bool], opts: dict[str, int])",
                drop_self=False,
            ),
            ["handler", "opts"],
        )

    def test_drop_self_from_methods(self) -> None:
        self.assertEqual(
            _params_for_signature("(self, x, y)", drop_self=True),
            ["x", "y"],
        )

    def test_drop_cls_from_classmethods(self) -> None:
        self.assertEqual(
            _params_for_signature("(cls, x)", drop_self=True),
            ["x"],
        )

    def test_starargs_kept_without_prefix(self) -> None:
        self.assertEqual(
            _params_for_signature("(*args, **kwargs)", drop_self=False),
            ["args", "kwargs"],
        )


class FunctionSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_skeleton_repo()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_lookup_by_bare_name_finds_symbol(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        self.assertEqual(skel.symbol_kind, "function")
        self.assertEqual(skel.target_name, "add")

    def test_src_prefix_stripped_from_module(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        # File is src/mypkg/core.py → import path is mypkg.core, NOT src.mypkg.core
        self.assertEqual(skel.target_module, "mypkg.core")
        self.assertIn("from mypkg.core import add", skel.body)

    def test_function_body_includes_test_class_with_suffix(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        # Class name uses the `<Subject>Tests` suffix convention
        # (matches v0.9.2's coverage_gap_engine fix)
        self.assertIn("class AddTests(unittest.TestCase):", skel.body)

    def test_function_test_body_raises_not_implemented(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        # The body must FAIL until the user fills it in — silent passing
        # stubs are worse than no test at all.
        self.assertIn("raise NotImplementedError", skel.body)

    def test_function_call_hint_uses_param_names(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        self.assertIn("add(a, b)", skel.body)

    def test_function_with_no_params(self) -> None:
        skel = generate_test_skeleton(self.repo, "lonely")
        self.assertIn("lonely()", skel.body)

    def test_generated_body_is_valid_python(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        try:
            ast.parse(skel.body)
        except SyntaxError as e:  # pragma: no cover — failing test surfaces it
            self.fail(f"generated skeleton failed to parse: {e}\n---\n{skel.body}")


class ClassSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_skeleton_repo()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_class_skeleton_imports_class(self) -> None:
        skel = generate_test_skeleton(self.repo, "Greeter")
        self.assertEqual(skel.symbol_kind, "class")
        self.assertIn("from mypkg.core import Greeter", skel.body)

    def test_class_setup_uses_constructor_params(self) -> None:
        skel = generate_test_skeleton(self.repo, "Greeter")
        # __init__(self, name, prefix='Hello') → params after dropping self
        # are [name, prefix]
        self.assertIn("Greeter(name, prefix)", skel.body)

    def test_one_test_method_per_public_method(self) -> None:
        skel = generate_test_skeleton(self.repo, "Greeter")
        # Public methods of Greeter: greet, shout. NOT __init__, NOT _internal.
        self.assertIn("def test_greet_smoke", skel.body)
        self.assertIn("def test_shout_smoke", skel.body)
        self.assertNotIn("test__internal", skel.body)
        self.assertNotIn("test___init__", skel.body)

    def test_method_params_drop_self(self) -> None:
        skel = generate_test_skeleton(self.repo, "Greeter")
        # shout(self, volume) → call hint should be shout(volume), not shout(self, volume)
        self.assertIn("shout(volume)", skel.body)
        self.assertNotIn("shout(self", skel.body)

    def test_class_with_no_methods_gets_constructor_test(self) -> None:
        skel = generate_test_skeleton(self.repo, "Bare")
        self.assertIn("class BareTests(unittest.TestCase):", skel.body)
        self.assertIn("test_constructs", skel.body)

    def test_class_skeleton_is_valid_python(self) -> None:
        skel = generate_test_skeleton(self.repo, "Greeter")
        try:
            ast.parse(skel.body)
        except SyntaxError as e:  # pragma: no cover
            self.fail(f"class skeleton failed to parse: {e}\n---\n{skel.body}")


class WriteSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_skeleton_repo()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_write_creates_file(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        out_path = self.tmp_root / "out" / "test_add.py"
        written = write_skeleton(skel, out_path)
        self.assertTrue(written.exists())
        contents = written.read_text(encoding="utf-8")
        self.assertEqual(contents, skel.body)

    def test_write_refuses_to_clobber_without_force(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        out_path = self.tmp_root / "out" / "test_add.py"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("# pre-existing\n", encoding="utf-8")
        with self.assertRaises(SkeletonError):
            write_skeleton(skel, out_path)
        # Original file untouched
        self.assertEqual(out_path.read_text(encoding="utf-8"), "# pre-existing\n")

    def test_write_force_overwrites(self) -> None:
        skel = generate_test_skeleton(self.repo, "add")
        out_path = self.tmp_root / "out" / "test_add.py"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("# pre-existing\n", encoding="utf-8")
        write_skeleton(skel, out_path, force=True)
        # Replaced with the skeleton body
        self.assertEqual(out_path.read_text(encoding="utf-8"), skel.body)


class UnknownSymbolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_skeleton_repo()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_unknown_symbol_raises(self) -> None:
        with self.assertRaises(SkeletonError):
            generate_test_skeleton(self.repo, "definitely_not_a_real_symbol")


class FlatLayoutTests(unittest.TestCase):
    """Repos without a src/ dir should use the module path as-is."""

    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_flat_layout_module_path_unchanged(self) -> None:
        # The fixture repo has app.py at the root, no src/.
        skel = generate_test_skeleton(self.repo, "GreetingService")
        self.assertEqual(skel.symbol_kind, "class")
        self.assertNotIn("src.", skel.body)


if __name__ == "__main__":
    unittest.main()
