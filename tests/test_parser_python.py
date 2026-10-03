"""Unit tests for jarvis_graph.parser_python.parse_python_file.

Pure-parser tests: each test writes a throwaway .py file into a tmp dir,
calls parse_python_file directly and inspects the returned ParsedFile —
no DB, no indexer, no fixture repo.

Covered:
  - file metadata (rel_path, abs_path, module_path, sha256, size, mtime)
  - the synthetic `<module>` symbol + module_path/qname derivation for
    nested files and `__init__.py` (incl. the root-__init__ rel_path fallback)
  - symbol discovery: functions, async functions, classes, methods,
    UPPER_CASE constants, is_private flags, docstrings
  - signature rendering: pos-only `/`, `*args`, kw-only `*`, `**kwargs`,
    defaults omitted
  - McCabe cyclomatic complexity: a per-construct table plus a
    hand-counted kitchen-sink function
  - line counts for functions, methods, classes and constants
  - import recording: plain / aliased / from / from-as / relative
    (leading dots preserved) / function-local, all with lineno
  - call edges: caller attribution (function vs `<module>`, incl. the
    `if __name__ == "__main__"` block), `svc.greet` → `GreetingService.greet`
    local-type rewrite, `self.method` → `Class.method`, plain
    `import X; X.fn()` and aliased `import X as Y; Y.fn()` dotted callees,
    non-Name call bases dropped, nested-def calls attributed to the
    enclosing function
  - robustness: syntax-error and null-byte sources never raise; they return
    a ParsedFile with parse_error set and empty symbol/import/call lists
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from conftest import cleanup  # path setup — mirrors the other test files

from jarvis_graph.models import ParsedFile
from jarvis_graph.parser_python import parse_python_file

# ---------------------------------------------------------------------------
# Source fixtures
# ---------------------------------------------------------------------------

SAMPLE_MODULE = '''\
"""Module docstring for sample."""

import os

VERSION = "1.0.0"
total = 0


def visible():
    """Doc for visible."""
    return os.getcwd()


def _hidden():
    return -1


class Greeter:
    """Doc for Greeter."""

    def greet(self, name):
        return name

    def _secret(self):
        return 42


async def fetch_all(session):
    async with session.get("/x") as resp:
        return resp
'''

SIGNATURE_MODULE = '''\
def no_args():
    pass


def pos_only(a, b, /, c):
    return a + b + c


def star_args(a, *args):
    return a, args


def kw_only(*, x, y=1):
    return x, y


def var_kw(a, b=2, *args, flag, **kwargs):
    return a, b, args, flag, kwargs
'''

COMPLEXITY_MODULE = '''\
def simple(x):
    return x + 1


def if_else(cond):
    if cond:
        return 1
    else:
        return 2


def elif_chain(cond):
    if cond > 0:
        return 1
    elif cond < 0:
        return 2
    elif cond == 0:
        return 3
    return 0


def for_loop(xs):
    total = 0
    for x in xs:
        total += x
    return total


def while_loop(n):
    while n > 0:
        n -= 1
    return n


def with_stmt(path):
    with open(path) as fh:
        return fh.read()


def assert_stmt(x):
    assert x > 0, "positive"
    return x


def ternary(flag):
    return "yes" if flag else "no"


def bool_and3(a, b, c):
    return a and b and c


def try_one_except(items):
    try:
        return items[0]
    except IndexError:
        return None


def comprehension(xs):
    return [x for x in xs if x]


def match_three_cases(code):
    match code:
        case 1:
            return "one"
        case 2:
            return "two"
        case _:
            return "many"


async def async_with(session):
    async with session.get("/x") as resp:
        return resp


def kitchen_sink(items, cond):
    total = 0
    if cond > 0:
        total += 1
    elif cond < 0:
        total -= 1
    for it in items:
        if it and it > 0:
            total += it
    while total < 10:
        total += 1
    try:
        total = total / len(items)
    except ZeroDivisionError:
        total = 0
    return total if items else 0
'''

CALLS_SOURCE = '''\
import sqlite3


def typed_calls():
    svc = GreetingService("x")
    msg = svc.greet("world")
    conn = sqlite3.connect(":memory:")
    conn.execute("select 1")
    return svc, msg, conn


def chained_call():
    return build_handler().run()


def deep_attr(mod):
    return mod.first.second()


def outer():
    def inner():
        return helper()
    return inner()


class Speaker:
    def announce(self):
        return self.whisper()

    def whisper(self):
        return "shh"
'''

IMPORT_CALLER_SOURCE = '''\
import helpers
import helpers as h_alias
from helpers import format_greeting


def call_via_module():
    return helpers.format_greeting("plain")


def call_via_alias():
    return h_alias.format_greeting("aliased")


def call_via_from_import():
    return format_greeting("from-import")
'''

IMPORTS_SOURCE = '''\
import os
import json as j
from collections import OrderedDict
from collections import defaultdict as dd
from . import sibling
from .worker import run_worker
from ..util.strings import shorten


def lazy_import():
    from package.worker import run_worker as rw
    return rw()
'''

EXEC_SOURCE = '''\
def used():
    return 1


_RESULT = used()


if __name__ == "__main__":
    print(used())
'''


# ---------------------------------------------------------------------------
# Base class + helpers
# ---------------------------------------------------------------------------


class ParserTestCase(unittest.TestCase):
    """Base: a fresh tmp dir per test plus source/lookup helpers."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jgl_parser_"))
        self.addCleanup(cleanup, self.tmp)

    # -- helpers ----------------------------------------------------------

    def parse(self, name: str, source: str) -> ParsedFile:
        """Write ``source`` at ``name`` (subdirs created) and parse it."""
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
        return parse_python_file(p, Path(name))

    @staticmethod
    def symbol(pf: ParsedFile, name: str, kind: str | None = None) -> list:
        return [
            s for s in pf.symbols
            if s.name == name and (kind is None or s.kind == kind)
        ]

    def one_symbol(self, pf: ParsedFile, name: str, kind: str | None = None):
        hits = self.symbol(pf, name, kind)
        self.assertEqual(
            len(hits),
            1,
            f"expected exactly one {kind or 'any'} symbol named {name!r}, got {hits}",
        )
        return hits[0]

    @staticmethod
    def calls_for(pf: ParsedFile, caller_qname: str) -> list:
        return [c for c in pf.calls if c.caller_qname == caller_qname]

    @staticmethod
    def line_of(source: str, needle: str) -> int:
        """1-based line number of the first fixture line containing ``needle``."""
        for lineno, line in enumerate(source.splitlines(), start=1):
            if needle in line:
                return lineno
        raise AssertionError(f"needle {needle!r} not found in fixture source")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class FileMetadataTests(ParserTestCase):
    def test_metadata_fields(self) -> None:
        data = '"""Doc."""\n\nX = 1\n'.encode("utf-8")
        p = self.tmp / "meta_mod.py"
        p.write_bytes(data)
        pf = parse_python_file(p, Path("meta_mod.py"))

        self.assertIsInstance(pf, ParsedFile)
        self.assertEqual(pf.rel_path, "meta_mod.py")
        self.assertEqual(pf.abs_path, str(p))
        self.assertEqual(pf.module_path, "meta_mod")
        self.assertEqual(pf.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(pf.size_bytes, len(data))
        self.assertEqual(pf.mtime, int(p.stat().st_mtime))
        self.assertIsNone(pf.parse_error)

    def test_rel_path_uses_posix_separators(self) -> None:
        pf = self.parse("dir_a/mod_b.py", "X = 1\n")
        self.assertEqual(pf.rel_path, "dir_a/mod_b.py")
        self.assertEqual(pf.module_path, "dir_a.mod_b")


class ModuleSymbolTests(ParserTestCase):
    def test_module_symbol_fields(self) -> None:
        pf = self.parse("docmod.py", '"""Module doc."""\n\nX = 1\n')
        mod = self.one_symbol(pf, "<module>", "module")
        self.assertEqual(mod.qualified_name, "docmod")
        self.assertEqual(mod.kind, "module")
        self.assertEqual(mod.lineno, 1)
        self.assertIsNone(mod.end_lineno)
        self.assertEqual(mod.col, 0)
        self.assertIsNone(mod.parent_qname)
        self.assertIsNone(mod.signature)
        self.assertEqual(mod.is_private, 0)
        self.assertEqual(mod.docstring, "Module doc.")

    def test_module_without_docstring_has_none(self) -> None:
        pf = self.parse("nodoc.py", "X = 1\n")
        mod = self.one_symbol(pf, "<module>", "module")
        self.assertIsNone(mod.docstring)

    def test_nested_file_gets_dotted_module_path(self) -> None:
        pf = self.parse("pkg/sub/nested.py", "def helper():\n    return 1\n")
        self.assertEqual(pf.module_path, "pkg.sub.nested")
        fn = self.one_symbol(pf, "helper", "function")
        self.assertEqual(fn.qualified_name, "pkg.sub.nested.helper")

    def test_package_init_maps_to_package_module_path(self) -> None:
        pf = self.parse("pkg/__init__.py", '"""Pkg."""\n')
        self.assertEqual(pf.module_path, "pkg")
        mod = self.one_symbol(pf, "<module>", "module")
        self.assertEqual(mod.qualified_name, "pkg")

    def test_root_init_falls_back_to_rel_path(self) -> None:
        pf = self.parse("__init__.py", "VALUE = 1\n")
        self.assertEqual(pf.module_path, "")
        mod = self.one_symbol(pf, "<module>", "module")
        # module_path is empty for a root __init__.py, so the module symbol
        # falls back to the rel_path for its qualified name.
        self.assertEqual(mod.qualified_name, "__init__.py")


class SymbolDiscoveryTests(ParserTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.pf = self.parse("sample_mod.py", SAMPLE_MODULE)

    def test_functions_found_with_qnames(self) -> None:
        vis = self.one_symbol(self.pf, "visible", "function")
        self.assertEqual(vis.qualified_name, "sample_mod.visible")
        self.assertIsNone(vis.parent_qname)
        self.assertEqual(vis.docstring, "Doc for visible.")
        self.assertEqual(vis.complexity, 1)

    def test_private_flag(self) -> None:
        self.assertEqual(self.one_symbol(self.pf, "_hidden", "function").is_private, 1)
        self.assertEqual(self.one_symbol(self.pf, "visible", "function").is_private, 0)

    def test_async_def_is_a_function_symbol(self) -> None:
        fn = self.one_symbol(self.pf, "fetch_all", "function")
        self.assertEqual(fn.signature, "(session)")
        self.assertEqual(fn.complexity, 2)  # 1 + the async with

    def test_class_and_methods(self) -> None:
        cls = self.one_symbol(self.pf, "Greeter", "class")
        self.assertEqual(cls.qualified_name, "sample_mod.Greeter")
        self.assertIsNone(cls.signature)
        self.assertEqual(cls.complexity, 0)
        self.assertEqual(cls.docstring, "Doc for Greeter.")

        greet = self.one_symbol(self.pf, "greet", "method")
        self.assertEqual(greet.qualified_name, "sample_mod.Greeter.greet")
        self.assertEqual(greet.parent_qname, "sample_mod.Greeter")
        self.assertEqual(greet.signature, "(self, name)")
        self.assertIsNone(greet.docstring)

    def test_private_method_flag(self) -> None:
        self.assertEqual(self.one_symbol(self.pf, "_secret", "method").is_private, 1)

    def test_upper_case_constants_captured(self) -> None:
        const = self.one_symbol(self.pf, "VERSION", "constant")
        self.assertEqual(const.qualified_name, "sample_mod.VERSION")
        self.assertIsNone(const.parent_qname)
        self.assertIsNone(const.signature)
        self.assertEqual(const.is_private, 0)

    def test_lowercase_assign_and_import_are_not_symbols(self) -> None:
        self.assertFalse(self.symbol(self.pf, "total"))
        self.assertFalse(self.symbol(self.pf, "os"))


class SignatureTests(ParserTestCase):
    EXPECTED_SIGNATURES = {
        "no_args": "()",
        "pos_only": "(a, b, /, c)",
        "star_args": "(a, *args)",
        "kw_only": "(*, x, y)",
        "var_kw": "(a, b, *args, flag, **kwargs)",
    }

    def test_signature_rendering(self) -> None:
        pf = self.parse("sig_mod.py", SIGNATURE_MODULE)
        for fname, expected in self.EXPECTED_SIGNATURES.items():
            with self.subTest(function=fname):
                sym = self.one_symbol(pf, fname, "function")
                self.assertEqual(sym.signature, expected)

    def test_defaults_are_omitted(self) -> None:
        pf = self.parse("sig_mod.py", SIGNATURE_MODULE)
        # kw_only declares `y=1` and var_kw declares `b=2` — the rendered
        # signatures above must not contain any default values.
        for fname in ("kw_only", "var_kw"):
            with self.subTest(function=fname):
                sym = self.one_symbol(pf, fname, "function")
                self.assertNotIn("=", sym.signature)


class ComplexityTests(ParserTestCase):
    EXPECTED_COMPLEXITY = {
        "simple": 1,
        "if_else": 2,           # `else` adds nothing
        "elif_chain": 4,        # each elif is another nested ast.If node
        "for_loop": 2,
        "while_loop": 2,
        "with_stmt": 2,
        "assert_stmt": 2,
        "ternary": 2,           # IfExp
        "bool_and3": 3,         # `a and b and c` → (3 values - 1)
        "try_one_except": 3,    # try + its single except handler
        # The `for` clause counts (+1). The comprehension's `if` clause is
        # NOT counted: ast.comprehension is already in _BRANCH_NODES, so the
        # first isinstance branch consumes the node before the `len(sub.ifs)`
        # elif can run. This pins the current behaviour.
        "comprehension": 2,
        "match_three_cases": 4,  # +1 per case
        "async_with": 2,
        # Hand-counted: base + if + elif + for + if + and + while + try
        # + except + ternary.
        "kitchen_sink": 10,
    }

    def test_complexity_table(self) -> None:
        pf = self.parse("cplx_mod.py", COMPLEXITY_MODULE)
        for fname, expected in self.EXPECTED_COMPLEXITY.items():
            with self.subTest(function=fname):
                sym = self.one_symbol(pf, fname, "function")
                self.assertEqual(sym.complexity, expected)


class LineCountTests(ParserTestCase):
    def test_function_line_span(self) -> None:
        src = SIGNATURE_MODULE
        pf = self.parse("sig_mod.py", src)
        fn = self.one_symbol(pf, "no_args", "function")
        self.assertEqual(fn.lineno, self.line_of(src, "def no_args():"))
        self.assertEqual(fn.line_count, 2)  # def line + single body line

    def test_class_span_covers_all_methods(self) -> None:
        src = SAMPLE_MODULE
        pf = self.parse("sample_mod.py", src)
        cls = self.one_symbol(pf, "Greeter", "class")
        secret = self.one_symbol(pf, "_secret", "method")
        self.assertEqual(cls.lineno, self.line_of(src, "class Greeter:"))
        # The class ends where its last method ends.
        self.assertEqual(cls.end_lineno, secret.end_lineno)
        self.assertEqual(cls.line_count, cls.end_lineno - cls.lineno + 1)
        self.assertEqual(secret.line_count, 2)

    def test_constant_has_no_computed_line_count(self) -> None:
        # _line_count is only applied to functions/classes/methods;
        # constants keep the dataclass default of 0.
        pf = self.parse("sample_mod.py", SAMPLE_MODULE)
        const = self.one_symbol(pf, "VERSION", "constant")
        self.assertEqual(const.line_count, 0)


class ImportRecordingTests(ParserTestCase):
    def test_all_import_shapes_recorded(self) -> None:
        pf = self.parse("pkg/imports_mod.py", IMPORTS_SOURCE)
        got = {(i.imported_module, i.imported_name, i.alias) for i in pf.imports}
        self.assertEqual(
            got,
            {
                ("os", None, None),
                ("json", None, "j"),
                ("collections", "OrderedDict", None),
                ("collections", "defaultdict", "dd"),
                (".", "sibling", None),
                (".worker", "run_worker", None),
                ("..util.strings", "shorten", None),
                ("package.worker", "run_worker", "rw"),  # function-local import
            },
        )

    def test_import_lineno_recorded(self) -> None:
        src = IMPORTS_SOURCE
        pf = self.parse("pkg/imports_mod.py", src)
        os_imports = [i for i in pf.imports if i.imported_module == "os"]
        self.assertEqual(len(os_imports), 1)
        self.assertEqual(os_imports[0].lineno, self.line_of(src, "import os"))


class CallAttributionTests(ParserTestCase):
    def test_module_level_exec_calls_belong_to_module_symbol(self) -> None:
        src = EXEC_SOURCE
        pf = self.parse("exec_mod.py", src)
        mod_calls = self.calls_for(pf, "exec_mod")
        self.assertEqual({c.callee_name for c in mod_calls}, {"print", "used"})
        self.assertEqual(len(mod_calls), 3)  # used() twice + print() once
        # Calls inside the `if __name__ == "__main__":` block are included.
        print_calls = [c for c in mod_calls if c.callee_name == "print"]
        self.assertEqual(print_calls[0].lineno, self.line_of(src, "print(used())"))

    def test_function_with_no_calls_has_no_call_edges(self) -> None:
        pf = self.parse("exec_mod.py", EXEC_SOURCE)
        self.assertEqual(self.calls_for(pf, "exec_mod.used"), [])

    def test_nested_def_calls_attributed_to_enclosing_function(self) -> None:
        pf = self.parse("calls_mod.py", CALLS_SOURCE)
        callees = {c.callee_name for c in self.calls_for(pf, "calls_mod.outer")}
        # Calls inside the nested `inner` are walked with the outer body and
        # therefore attributed to `outer`, not to a symbol of their own.
        self.assertEqual(callees, {"helper", "inner"})


class CallRewriteTests(ParserTestCase):
    def test_local_type_tracking_rewrites_var_method(self) -> None:
        pf = self.parse("calls_mod.py", CALLS_SOURCE)
        callees = {c.callee_name for c in self.calls_for(pf, "calls_mod.typed_calls")}
        self.assertEqual(
            callees,
            {
                "GreetingService",        # the constructor call itself
                "GreetingService.greet",  # svc.greet(...) rewritten via local type
                "sqlite3.connect",        # dotted stdlib call, recorded verbatim
                "conn.execute",           # `connect` is lowercase → conn not tracked
            },
        )

    def test_rewrite_keeps_attribute_tail(self) -> None:
        pf = self.parse("calls_mod.py", CALLS_SOURCE)
        callees = [c.callee_name for c in self.calls_for(pf, "calls_mod.deep_attr")]
        # Only the head segment is eligible for rewriting; the chain is kept.
        self.assertEqual(callees, ["mod.first.second"])

    def test_self_method_call_rewritten_to_class(self) -> None:
        pf = self.parse("calls_mod.py", CALLS_SOURCE)
        callees = [
            c.callee_name for c in self.calls_for(pf, "calls_mod.Speaker.announce")
        ]
        self.assertEqual(callees, ["Speaker.whisper"])

    def test_non_name_call_base_is_dropped(self) -> None:
        pf = self.parse("calls_mod.py", CALLS_SOURCE)
        callees = [c.callee_name for c in self.calls_for(pf, "calls_mod.chained_call")]
        # build_handler().run() — the .run() attribute sits on a Call, not a
        # Name, so the parser drops it rather than guessing a callee.
        self.assertEqual(callees, ["build_handler"])

    def test_call_lineno_recorded(self) -> None:
        src = CALLS_SOURCE
        pf = self.parse("calls_mod.py", src)
        greet_calls = [
            c for c in self.calls_for(pf, "calls_mod.typed_calls")
            if c.callee_name == "GreetingService.greet"
        ]
        self.assertEqual(len(greet_calls), 1)
        self.assertEqual(greet_calls[0].lineno, self.line_of(src, 'svc.greet("world")'))


class PlainImportCallTests(ParserTestCase):
    """The `import X; X.fn()` case.

    The parser must record the dotted callee exactly as written (including
    the alias the caller used). Resolving that textual name to a real
    symbol happens downstream in the indexer — here we pin what the parser
    hands it.
    """

    def test_plain_import_dotted_callee(self) -> None:
        pf = self.parse("import_caller.py", IMPORT_CALLER_SOURCE)
        callees = [
            c.callee_name for c in self.calls_for(pf, "import_caller.call_via_module")
        ]
        self.assertEqual(callees, ["helpers.format_greeting"])

    def test_aliased_import_dotted_callee(self) -> None:
        pf = self.parse("import_caller.py", IMPORT_CALLER_SOURCE)
        callees = [
            c.callee_name for c in self.calls_for(pf, "import_caller.call_via_alias")
        ]
        self.assertEqual(callees, ["h_alias.format_greeting"])

    def test_from_import_call_keeps_bare_name(self) -> None:
        pf = self.parse("import_caller.py", IMPORT_CALLER_SOURCE)
        callees = [
            c.callee_name
            for c in self.calls_for(pf, "import_caller.call_via_from_import")
        ]
        self.assertEqual(callees, ["format_greeting"])

    def test_caller_qname_lineno_and_no_stray_edges(self) -> None:
        src = IMPORT_CALLER_SOURCE
        pf = self.parse("import_caller.py", src)
        # Imports at top level produce no call edges, so the file has exactly
        # one call per function.
        self.assertEqual(len(pf.calls), 3)
        call = self.calls_for(pf, "import_caller.call_via_module")[0]
        self.assertEqual(call.lineno, self.line_of(src, 'helpers.format_greeting("plain")'))


class ParseErrorTests(ParserTestCase):
    def test_syntax_error_sets_parse_error_and_never_raises(self) -> None:
        pf = self.parse("broken.py", "def broken(:\n    pass\n")
        self.assertIsNotNone(pf.parse_error)
        self.assertTrue(pf.parse_error.startswith("SyntaxError"), pf.parse_error)
        self.assertEqual(pf.symbols, [])
        self.assertEqual(pf.imports, [])
        self.assertEqual(pf.calls, [])
        # Metadata is still populated for the broken file.
        self.assertEqual(pf.module_path, "broken")
        self.assertEqual(len(pf.sha256), 64)

    def test_null_byte_source_sets_parse_error_and_never_raises(self) -> None:
        p = self.tmp / "nul_mod.py"
        p.write_bytes(b"x = 1\x00\n")
        pf = parse_python_file(p, Path("nul_mod.py"))
        self.assertIsNotNone(pf.parse_error)
        # 3.11 raises SyntaxError here, newer/older CPython may raise
        # ValueError — the parser catches both, so accept either.
        self.assertIn(
            pf.parse_error.split(":", 1)[0],
            {"SyntaxError", "ValueError"},
            pf.parse_error,
        )
        self.assertEqual(pf.symbols, [])


if __name__ == "__main__":
    unittest.main()
