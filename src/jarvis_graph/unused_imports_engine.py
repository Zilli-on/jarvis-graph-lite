"""find_unused_imports: detect import statements with no in-file usage.

For each import_edge we determine the local binding name:
  - `import X`              → 'X'
  - `import X.Y`            → 'X' (binding is the head package)
  - `import X as Y`         → 'Y'
  - `from X import Y`       → 'Y'
  - `from X import Y as Z`  → 'Z'

An import is flagged as *unused* when **neither** of the following is true:
  1. A call_edge in the same file references it (callee head matches), OR
  2. The local binding name appears as a textual token anywhere in the file
     OUTSIDE of import statements.

The textual fallback catches type annotations, isinstance() checks, class
bases, decorator @references, and bare attribute reads — all things that
do not produce call_edges. Import lines are stripped before scanning so
the import statement itself doesn't keep itself alive.

Limitations:
  - `from X import *` is not flagged (no name to check) and not penalised.
  - Imports referenced only via string-based reflection (`getattr`, `__all__`)
    will still slip through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def _scan_non_import_tokens(file_path: Path) -> set[str]:
    """Return all identifier-like tokens in the file, skipping import lines."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    keep: list[str] = []
    in_paren_import = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        # Multi-line `from X import (a,\n  b,\n  c,\n)` — skip until close paren.
        if in_paren_import:
            keep.append("")
            if ")" in line:
                in_paren_import -= line.count(")")
                in_paren_import = max(0, in_paren_import - line.count("("))
            continue
        if stripped.startswith("import ") or stripped.startswith("from "):
            if "(" in line and ")" not in line:
                in_paren_import = 1
            continue
        keep.append(line)
    body = "\n".join(keep)
    return set(_TOKEN_RE.findall(body))


@dataclass
class UnusedImport:
    rel_path: str
    lineno: int
    imported_module: str
    imported_name: str | None
    alias: str | None
    binding: str


@dataclass
class UnusedImportsReport:
    repo_path: str
    total_imports: int = 0
    unused: list[UnusedImport] = field(default_factory=list)


# Modules whose import is almost always for side effects.
_SIDE_EFFECT_MODULES = {
    "__future__",
    "warnings",
    "logging.config",
    "readline",
    "rlcompleter",
}


def _binding_name(imp_module: str, imp_name: str | None, alias: str | None) -> str | None:
    if alias:
        return alias
    if imp_name:
        if imp_name == "*":
            return None
        return imp_name
    # plain `import X.Y.Z` binds X
    return imp_module.split(".", 1)[0] if imp_module else None


def find_unused_imports(repo_path: Path) -> UnusedImportsReport:
    repo_path = repo_path.resolve()
    rep = UnusedImportsReport(repo_path=str(repo_path))
    conn = connect(repo_path)
    try:
        rows = conn.execute(
            """
            SELECT ie.edge_id, ie.file_id, ie.imported_module, ie.imported_name,
                   ie.alias, ie.lineno, f.rel_path, f.abs_path
              FROM import_edge ie
              JOIN file f ON f.file_id = ie.file_id
            """
        ).fetchall()
        # Cache: file_id → set of identifier tokens outside import lines.
        token_cache: dict[int, set[str]] = {}

        for r in rows:
            rep.total_imports += 1
            mod = r["imported_module"] or ""
            if mod.lstrip(".") in _SIDE_EFFECT_MODULES or mod in _SIDE_EFFECT_MODULES:
                continue
            binding = _binding_name(mod, r["imported_name"], r["alias"])
            if not binding:
                continue
            # Path 1: a call_edge in the same file uses it.
            used = conn.execute(
                """
                SELECT 1
                  FROM call_edge ce
                  JOIN symbol s ON s.symbol_id = ce.caller_symbol_id
                 WHERE s.file_id = ?
                   AND (ce.callee_name = ? OR ce.callee_name LIKE ?)
                 LIMIT 1
                """,
                (r["file_id"], binding, binding + ".%"),
            ).fetchone()
            if used:
                continue
            # Path 2: textual reference outside import statements (catches
            # type annotations, isinstance, class bases, decorators, etc.).
            file_id = int(r["file_id"])
            if file_id not in token_cache:
                token_cache[file_id] = _scan_non_import_tokens(Path(r["abs_path"]))
            if binding in token_cache[file_id]:
                continue
            rep.unused.append(
                UnusedImport(
                    rel_path=r["rel_path"],
                    lineno=r["lineno"],
                    imported_module=mod,
                    imported_name=r["imported_name"],
                    alias=r["alias"],
                    binding=binding,
                )
            )
    finally:
        conn.close()
    rep.unused.sort(key=lambda u: (u.rel_path, u.lineno))
    return rep
