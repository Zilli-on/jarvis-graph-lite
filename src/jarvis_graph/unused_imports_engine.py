"""find_unused_imports: detect import statements with no in-file usage.

For each import_edge we determine the local binding name:
  - `import X`              → 'X'
  - `import X.Y`            → 'X' (binding is the head package)
  - `import X as Y`         → 'Y'
  - `from X import Y`       → 'Y'
  - `from X import Y as Z`  → 'Z'

An import is flagged as *unused* when **none** of the following are true:
  1. A call_edge in the same file references it (callee head matches), OR
  2. The local binding name appears as a textual token anywhere in the file
     OUTSIDE of import statements, OR
  3. The import line carries a `# noqa` or `# noqa: F401` suppression
     directive (flake8 / ruff convention for intentional side-effect
     imports — e.g. `from conftest import ROOT  # noqa: F401`).

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

# Top-level directive: matches `# noqa` or `# noqa: <codes>` with an
# optional (greedy-to-end-of-line) code list.  The code list may contain
# additional commentary (`# noqa: F401  path setup`) — we extract valid
# codes from it with a second regex below.
_NOQA_RE = re.compile(
    r"#\s*noqa\b(?:\s*:\s*([^\r\n]*))?",
    re.IGNORECASE,
)
# A single flake8 / ruff / pylint style code: one or two letters followed
# by 3-4 digits (F401, E501, W605, RUF001, etc.).
_NOQA_CODE_RE = re.compile(r"\b[A-Z]{1,3}\d{3,4}\b")


def _noqa_allows_unused_import(text: str) -> bool:
    """True if `text` contains a `# noqa` directive covering F401.

    Blanket `# noqa` (no code list or empty code list) suppresses
    everything. `# noqa: F401` specifically suppresses unused-import.
    `# noqa: E501` does NOT suppress F401 and returns False. Additional
    commentary after the code list is tolerated
    (`# noqa: F401  path setup`).
    """
    for m in _NOQA_RE.finditer(text):
        codes_raw = m.group(1)
        if codes_raw is None or not codes_raw.strip():
            return True  # blanket `# noqa` or `# noqa:` with nothing after
        codes = _NOQA_CODE_RE.findall(codes_raw.upper())
        if not codes:
            # `# noqa: something-weird` with no recognisable codes —
            # treat conservatively as blanket suppression (matches
            # flake8's behaviour).
            return True
        if "F401" in codes:
            return True
    return False


def _logical_import_line(lines: list[str], lineno: int) -> str:
    """Return the source of the import statement at `lineno` (1-indexed).

    For multi-line `from X import (\n  a,\n  b,\n)` the returned string
    joins all physical lines from the opening paren to the matching
    closing paren so noqa directives placed on any continuation line are
    visible to the caller.
    """
    idx = lineno - 1
    if idx < 0 or idx >= len(lines):
        return ""
    first = lines[idx]
    if "(" in first and ")" not in first:
        out = [first]
        j = idx + 1
        while j < len(lines):
            out.append(lines[j])
            if ")" in lines[j]:
                break
            j += 1
        return "\n".join(out)
    return first


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
        # Cache: file_id → list of physical source lines (for noqa scan).
        source_cache: dict[int, list[str]] = {}

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
            # Path 3: `# noqa` / `# noqa: F401` suppression on the import
            # line (flake8 / ruff convention for intentional side-effect
            # imports — e.g. `from conftest import ROOT  # noqa: F401`).
            if file_id not in source_cache:
                try:
                    source_cache[file_id] = (
                        Path(r["abs_path"])
                        .read_text(encoding="utf-8", errors="replace")
                        .splitlines()
                    )
                except OSError:
                    source_cache[file_id] = []
            logical = _logical_import_line(source_cache[file_id], int(r["lineno"]))
            if _noqa_allows_unused_import(logical):
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
