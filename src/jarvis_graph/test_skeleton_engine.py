"""generate_test_skeleton: closes the loop from `find_coverage_gaps` to
"how do I actually start writing the test?".

Given a symbol qname, emits a self-contained `test_<name>.py` template with:
  - the right `from <module> import <symbol>` statement
  - a `unittest.TestCase` subclass named `<Symbol>Tests` (suffix convention,
    matches the rest of this repo and the v0.9.2 fix that made suffix
    classes coverage-visible)
  - one test method per public method (for classes) or one test_<name>
    method (for functions / module-level methods)
  - `setUp` boilerplate when the target is a class — including a stub
    constructor call commented out so the user knows where to start
  - explicit `raise NotImplementedError` in every body so the test will
    FAIL until you fill it in (silent passing test stubs are worse than
    no tests at all)

Deliberately NOT in scope:
  - Actually generating assertions or guessing return values — that
    would need either an LLM call or a mountain of heuristics. The tool
    just gives you a starting structure.
  - Auto-running the new test. The user runs it.
  - Overwriting an existing file. We refuse unless `force=True`.

The output is plain Python text. The CLI subcommand can either print it
or write it to a chosen path; this engine never touches the filesystem
on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jarvis_graph.db import connect


@dataclass
class TestSkeleton:
    symbol_qname: str
    symbol_kind: str
    target_module: str
    target_name: str
    suggested_filename: str
    body: str


class SkeletonError(ValueError):
    """Raised when a skeleton cannot be built (symbol unknown, weird
    layout, etc.). Caller turns this into a CLI error message."""


def _module_import_path(rel_path: str, module_path: str) -> str:
    """Convert the indexer's `module_path` (which prefixes `src.` because
    the walker descends from the repo root) into the actual import path a
    consumer uses. We strip a leading `src.` segment iff the underlying
    `rel_path` lives under `src/` — anything else is left untouched so
    flat-layout repos still work.
    """
    rp = rel_path.replace("\\", "/")
    if rp.startswith("src/") and module_path.startswith("src."):
        return module_path[len("src."):]
    return module_path


def _params_for_signature(signature: str | None, drop_self: bool) -> list[str]:
    """Pull the bare parameter names out of a stored `(a, b, c=1)` style
    signature string. We don't try to preserve defaults or annotations —
    a stub doesn't need to be call-compatible, it just needs to give the
    user a starting point. Returns [] for missing/empty/`()` signatures.
    """
    if not signature:
        return []
    s = signature.strip()
    if s.startswith("("):
        s = s[1:]
    if s.endswith(")"):
        s = s[:-1]
    if not s.strip():
        return []
    params: list[str] = []
    depth = 0
    current: list[str] = []
    # Comma-split that respects nested brackets in annotations like
    # `Callable[[int, str], bool]` so we don't split inside them.
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            params.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        params.append("".join(current).strip())
    # Strip annotation/default off each entry: keep the bare name.
    cleaned: list[str] = []
    for p in params:
        if not p:
            continue
        if p in ("/", "*"):
            continue
        bare = p.split(":", 1)[0].split("=", 1)[0].strip()
        bare = bare.lstrip("*")
        if bare:
            cleaned.append(bare)
    if drop_self and cleaned and cleaned[0] in ("self", "cls"):
        cleaned = cleaned[1:]
    return cleaned


def _function_test_method(name: str, params: list[str]) -> str:
    """Render one `test_<name>` method body for a free function or method."""
    if params:
        arg_hint = ", ".join(params)
        call_hint = f"{name}({arg_hint})"
    else:
        call_hint = f"{name}()"
    return (
        f"    def test_{name}_smoke(self) -> None:\n"
        f"        # TODO: replace the placeholder call below with real inputs\n"
        f"        # result = {call_hint}\n"
        f"        # self.assertIsNotNone(result)\n"
        f"        raise NotImplementedError(\n"
        f'            "test_{name}_smoke is a generated stub — fill it in"\n'
        f"        )\n"
    )


def _class_setup_body(class_name: str, init_params: list[str]) -> str:
    if init_params:
        arg_hint = ", ".join(init_params)
        ctor_hint = f"{class_name}({arg_hint})"
    else:
        ctor_hint = f"{class_name}()"
    return (
        f"    def setUp(self) -> None:\n"
        f"        # TODO: construct a real instance for the tests below\n"
        f"        # self.subject = {ctor_hint}\n"
        f"        self.subject = None\n"
    )


def _render_function_skeleton(
    target_module: str,
    target_name: str,
    params: list[str],
) -> str:
    test_class = f"{target_name[0].upper()}{target_name[1:]}Tests"
    return (
        f'"""Generated test skeleton for `{target_module}.{target_name}`.\n'
        f'\n'
        f'Created by `jarvis-graph-lite generate_test_skeleton`. Every test\n'
        f'body raises NotImplementedError on purpose — fill them in before\n'
        f'committing.\n'
        f'"""\n'
        f'\n'
        f'from __future__ import annotations\n'
        f'\n'
        f'import unittest\n'
        f'\n'
        f'from {target_module} import {target_name}\n'
        f'\n'
        f'\n'
        f'class {test_class}(unittest.TestCase):\n'
        f'{_function_test_method(target_name, params)}'
        f'\n'
        f'if __name__ == "__main__":\n'
        f'    unittest.main()\n'
    )


def _render_class_skeleton(
    target_module: str,
    target_name: str,
    init_params: list[str],
    public_methods: list[tuple[str, list[str]]],
) -> str:
    test_class = f"{target_name}Tests"
    body_parts: list[str] = [_class_setup_body(target_name, init_params)]
    if not public_methods:
        body_parts.append(
            "\n"
            "    def test_constructs(self) -> None:\n"
            "        # TODO: assert any invariants the bare constructor should hold\n"
            "        self.assertIsNotNone(self.subject)\n"
        )
    else:
        for mname, mparams in public_methods:
            body_parts.append("\n" + _function_test_method(mname, mparams))
    return (
        f'"""Generated test skeleton for `{target_module}.{target_name}`.\n'
        f'\n'
        f'Created by `jarvis-graph-lite generate_test_skeleton`. Every test\n'
        f'body raises NotImplementedError on purpose — fill them in before\n'
        f'committing.\n'
        f'"""\n'
        f'\n'
        f'from __future__ import annotations\n'
        f'\n'
        f'import unittest\n'
        f'\n'
        f'from {target_module} import {target_name}\n'
        f'\n'
        f'\n'
        f'class {test_class}(unittest.TestCase):\n'
        + "".join(body_parts)
        + '\n'
        + 'if __name__ == "__main__":\n'
        + '    unittest.main()\n'
    )


def generate_test_skeleton(repo_path: Path, symbol_qname: str) -> TestSkeleton:
    """Build a `TestSkeleton` for `symbol_qname`. Looks the symbol up in
    the existing index — `repo_path` must already have been indexed."""
    repo_path = repo_path.resolve()
    conn = connect(repo_path)
    try:
        # Try qualified_name first (exact match), then bare name as a
        # convenience so the user can pass `summarize` instead of
        # `src.jarvis_graph.repo_summary.summarize`.
        row = conn.execute(
            """
            SELECT s.symbol_id, s.name, s.qualified_name, s.kind,
                   s.parent_qname, s.signature, f.rel_path, f.module_path
              FROM symbol s
              JOIN file f ON f.file_id = s.file_id
             WHERE s.qualified_name = ?
               AND s.kind IN ('function', 'method', 'class')
             LIMIT 1
            """,
            (symbol_qname,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT s.symbol_id, s.name, s.qualified_name, s.kind,
                       s.parent_qname, s.signature, f.rel_path, f.module_path
                  FROM symbol s
                  JOIN file f ON f.file_id = s.file_id
                 WHERE s.name = ?
                   AND s.kind IN ('function', 'method', 'class')
                 LIMIT 1
                """,
                (symbol_qname,),
            ).fetchone()
        if row is None:
            raise SkeletonError(
                f"symbol not found: {symbol_qname!r} (function/method/class)"
            )

        target_name = row["name"]
        kind = row["kind"]
        target_module = _module_import_path(row["rel_path"], row["module_path"])

        if kind == "function":
            params = _params_for_signature(row["signature"], drop_self=False)
            body = _render_function_skeleton(target_module, target_name, params)
        elif kind == "method":
            params = _params_for_signature(row["signature"], drop_self=True)
            body = _render_function_skeleton(target_module, target_name, params)
        elif kind == "class":
            # Find the __init__ to get constructor params, then list every
            # public method (no leading `_`, no dunder).
            init_row = conn.execute(
                """
                SELECT signature FROM symbol
                 WHERE parent_qname = ? AND name = '__init__' AND kind = 'method'
                 LIMIT 1
                """,
                (row["qualified_name"],),
            ).fetchone()
            init_params = (
                _params_for_signature(init_row["signature"], drop_self=True)
                if init_row
                else []
            )
            method_rows = conn.execute(
                """
                SELECT name, signature FROM symbol
                 WHERE parent_qname = ? AND kind = 'method'
                 ORDER BY lineno
                """,
                (row["qualified_name"],),
            ).fetchall()
            public_methods: list[tuple[str, list[str]]] = []
            for mr in method_rows:
                mname = mr["name"]
                if mname.startswith("_"):
                    continue
                public_methods.append(
                    (mname, _params_for_signature(mr["signature"], drop_self=True))
                )
            body = _render_class_skeleton(
                target_module, target_name, init_params, public_methods
            )
        else:  # pragma: no cover — narrowed by SQL above
            raise SkeletonError(f"unsupported kind: {kind}")
    finally:
        conn.close()

    return TestSkeleton(
        symbol_qname=row["qualified_name"],
        symbol_kind=kind,
        target_module=target_module,
        target_name=target_name,
        suggested_filename=f"test_{target_name.lower()}_skeleton.py",
        body=body,
    )


def write_skeleton(skel: TestSkeleton, out_path: Path, force: bool = False) -> Path:
    """Persist `skel.body` to `out_path`. Refuses to clobber an existing
    file unless `force=True`. Returns the resolved write path."""
    out_path = out_path.resolve()
    if out_path.exists() and not force:
        raise SkeletonError(
            f"refusing to overwrite existing file: {out_path} (pass --force)"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(skel.body, encoding="utf-8")
    return out_path
