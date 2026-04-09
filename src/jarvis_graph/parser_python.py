"""Parse one .py file with `ast` and return a ParsedFile.

Extracts:
  - top-level functions, classes, methods (one nesting level deep is enough)
  - imports (`import X`, `import X as Y`, `from X import Y`, `from .X import Y`)
  - call references inside functions/methods (raw textual callee name)

Robust to syntax errors: returns a ParsedFile with `parse_error` set.
Never raises on bad files.
"""

from __future__ import annotations

import ast
from pathlib import Path

from jarvis_graph.hashing import sha256_file
from jarvis_graph.models import ParsedCall, ParsedFile, ParsedImport, ParsedSymbol
from jarvis_graph.utils import to_module_path


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Best-effort textual signature without type annotations or defaults eval."""
    args = node.args
    parts: list[str] = []
    posonly = list(getattr(args, "posonlyargs", []))
    for a in posonly:
        parts.append(a.arg)
    if posonly:
        parts.append("/")
    for a in args.args:
        parts.append(a.arg)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for a in args.kwonlyargs:
        parts.append(a.arg)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return f"({', '.join(parts)})"


def _callee_name(node: ast.AST) -> str | None:
    """Render a Call.func into its textual reference, e.g. 'foo' or 'mod.bar.baz'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = [node.attr]
        cur: ast.AST = node.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        else:
            return None
        return ".".join(reversed(parts))
    return None


def _walk_calls(body_nodes: list[ast.stmt], caller_qname: str) -> list[ParsedCall]:
    out: list[ParsedCall] = []
    for n in body_nodes:
        for sub in ast.walk(n):
            if isinstance(sub, ast.Call):
                name = _callee_name(sub.func)
                if name:
                    out.append(
                        ParsedCall(
                            caller_qname=caller_qname,
                            callee_name=name,
                            lineno=getattr(sub, "lineno", 0),
                        )
                    )
    return out


def parse_python_file(abs_path: Path, rel_path: Path) -> ParsedFile:
    sha = sha256_file(abs_path)
    stat = abs_path.stat()
    module_path = to_module_path(rel_path)

    pf = ParsedFile(
        rel_path=str(rel_path).replace("\\", "/"),
        abs_path=str(abs_path),
        module_path=module_path,
        sha256=sha,
        size_bytes=stat.st_size,
        mtime=int(stat.st_mtime),
    )

    try:
        src = abs_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(abs_path))
    except (SyntaxError, ValueError) as exc:
        pf.parse_error = f"{type(exc).__name__}: {exc}"
        return pf

    # Synthetic module-level symbol so we can attach top-level execution
    # calls (script bodies, `if __name__ == '__main__'` blocks, etc.).
    module_qname = module_path or pf.rel_path
    pf.symbols.append(
        ParsedSymbol(
            name="<module>",
            qualified_name=module_qname,
            kind="module",
            lineno=1,
            end_lineno=None,
            col=0,
            docstring=ast.get_docstring(tree),
            signature=None,
            is_private=0,
            parent_qname=None,
        )
    )

    # Imports — full ast.walk so function-local and conditional imports
    # are also recorded. Real-world scripts often do `from X import Y`
    # inside a function body to avoid circular imports or for lazy loading;
    # without this pass, those calls can never be resolved cross-module.
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                pf.imports.append(
                    ParsedImport(
                        imported_module=alias.name,
                        imported_name=None,
                        alias=alias.asname,
                        lineno=sub.lineno,
                    )
                )
        elif isinstance(sub, ast.ImportFrom):
            mod = sub.module or ""
            mod_full = ("." * (sub.level or 0)) + mod
            for alias in sub.names:
                pf.imports.append(
                    ParsedImport(
                        imported_module=mod_full,
                        imported_name=alias.name,
                        alias=alias.asname,
                        lineno=sub.lineno,
                    )
                )

    # Top-level statements that aren't a def/class/import → collect their
    # calls under the module symbol so impact analysis sees script-level use.
    top_level_exec_nodes: list[ast.stmt] = []
    for n in tree.body:
        if isinstance(
            n,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
             ast.Import, ast.ImportFrom),
        ):
            continue
        top_level_exec_nodes.append(n)
    if top_level_exec_nodes:
        pf.calls.extend(_walk_calls(top_level_exec_nodes, module_qname))

    # Top-level pass: module-level functions/classes/constants only.
    # Imports are handled by the ast.walk pass above.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qname = f"{module_path}.{node.name}" if module_path else node.name
            pf.symbols.append(
                ParsedSymbol(
                    name=node.name,
                    qualified_name=qname,
                    kind="function",
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", None),
                    col=node.col_offset,
                    docstring=ast.get_docstring(node),
                    signature=_signature(node),
                    is_private=1 if node.name.startswith("_") else 0,
                    parent_qname=None,
                )
            )
            pf.calls.extend(_walk_calls(node.body, qname))
        elif isinstance(node, ast.ClassDef):
            cls_qname = f"{module_path}.{node.name}" if module_path else node.name
            pf.symbols.append(
                ParsedSymbol(
                    name=node.name,
                    qualified_name=cls_qname,
                    kind="class",
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", None),
                    col=node.col_offset,
                    docstring=ast.get_docstring(node),
                    signature=None,
                    is_private=1 if node.name.startswith("_") else 0,
                    parent_qname=None,
                )
            )
            for cn in node.body:
                if isinstance(cn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_qname = f"{cls_qname}.{cn.name}"
                    pf.symbols.append(
                        ParsedSymbol(
                            name=cn.name,
                            qualified_name=m_qname,
                            kind="method",
                            lineno=cn.lineno,
                            end_lineno=getattr(cn, "end_lineno", None),
                            col=cn.col_offset,
                            docstring=ast.get_docstring(cn),
                            signature=_signature(cn),
                            is_private=1 if cn.name.startswith("_") else 0,
                            parent_qname=cls_qname,
                        )
                    )
                    pf.calls.extend(_walk_calls(cn.body, m_qname))
        elif isinstance(node, ast.Assign):
            # Capture module-level UPPER_CASE constants only — keeps the index small.
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.isupper():
                    pf.symbols.append(
                        ParsedSymbol(
                            name=tgt.id,
                            qualified_name=(
                                f"{module_path}.{tgt.id}" if module_path else tgt.id
                            ),
                            kind="constant",
                            lineno=node.lineno,
                            end_lineno=getattr(node, "end_lineno", None),
                            col=node.col_offset,
                            docstring=None,
                            signature=None,
                            is_private=1 if tgt.id.startswith("_") else 0,
                            parent_qname=None,
                        )
                    )

    return pf
