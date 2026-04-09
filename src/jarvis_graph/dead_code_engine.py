"""find_dead_code: best-effort detection of unused functions / classes / methods.

A symbol is flagged as *dead* only when ALL of the following are true:
  - kind is function, method, or class (not constants, not <module>),
  - it is not private (leading underscore — those are explicitly internal),
  - the name is not a dunder (`__init__`, `__str__`, etc.),
  - the name is not a well-known entrypoint (`main`, `run`, `cli`, `app`),
  - the name does not start with `test_` / `Test` (pytest / unittest discovery),
  - no call_edge anywhere references the name as the last segment of its
    callee_name,
  - AND the name does not appear as an identifier OR a string literal
    anywhere in the repo's source files.

The string-literal check is what catches dynamic dispatch through registry
dicts: `tools["bash_exec"] = bash_exec` keeps `bash_exec` alive even when
no static call_edge ever resolves to it. That single check eliminates the
bulk of false positives that come from decorator-registered handlers,
plugin systems, and CLI command dispatchers.

False negatives (missed dead code) are acceptable; false positives (live
code labeled dead) are NOT — the user has to be able to trust this list.

Remaining limitations:
  - Symbols whose name is a common English word (`process`, `run`) will
    almost always appear in some string somewhere → never flagged. That's
    the price of strictness.
  - getattr/setattr through computed names is still invisible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


@dataclass
class DeadSymbol:
    qualified_name: str
    name: str
    kind: str
    rel_path: str
    lineno: int


@dataclass
class DeadCodeReport:
    repo_path: str
    total_checked: int = 0
    excluded_private: int = 0
    excluded_dunder: int = 0
    excluded_entrypoint: int = 0
    excluded_test: int = 0
    excluded_textual: int = 0
    dead: list[DeadSymbol] = field(default_factory=list)


_ENTRYPOINT_NAMES = {"main", "run", "cli", "app"}


def _build_per_file_tokens(conn) -> dict[int, set[str]]:
    """Map ``file_id → set of identifier-like tokens`` (string literals
    included, since the regex matches anywhere in the file). Used to
    answer "is this name mentioned in any OTHER file in the repo".
    """
    out: dict[int, set[str]] = {}
    rows = conn.execute("SELECT file_id, abs_path FROM file").fetchall()
    for r in rows:
        try:
            text = Path(r["abs_path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            out[int(r["file_id"])] = set()
            continue
        out[int(r["file_id"])] = set(_TOKEN_RE.findall(text))
    return out


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and len(name) > 4


def find_dead_code(repo_path: Path) -> DeadCodeReport:
    repo_path = repo_path.resolve()
    rep = DeadCodeReport(repo_path=str(repo_path))
    conn = connect(repo_path)
    try:
        rows = conn.execute(
            """
            SELECT s.symbol_id, s.file_id, s.qualified_name, s.name, s.kind,
                   s.lineno, s.is_private, f.rel_path
              FROM symbol s
              JOIN file f ON f.file_id = s.file_id
             WHERE s.kind IN ('function', 'method', 'class')
            """
        ).fetchall()
        per_file_tokens: dict[int, set[str]] | None = None

        for r in rows:
            rep.total_checked += 1
            name = r["name"]
            own_file_id = int(r["file_id"])
            # Dunder check FIRST — `__init__` etc. also start with `_` so the
            # is_private filter would otherwise hide them from the dunder count.
            if _is_dunder(name):
                rep.excluded_dunder += 1
                continue
            if r["is_private"]:
                rep.excluded_private += 1
                continue
            if r["kind"] == "function" and name in _ENTRYPOINT_NAMES:
                rep.excluded_entrypoint += 1
                continue
            if name.startswith("test_") or name.startswith("Test"):
                rep.excluded_test += 1
                continue
            # Textual call search: any callee that ends in this name?
            hit = conn.execute(
                """
                SELECT 1 FROM call_edge
                 WHERE callee_name = ?
                    OR callee_name LIKE ?
                 LIMIT 1
                """,
                (name, "%." + name),
            ).fetchone()
            if hit:
                continue
            # Final check: does the name appear in any OTHER file's source as
            # an identifier OR a string literal? Catches dispatch-dict
            # registrations like `tools["bash_exec"] = bash_exec`.
            if per_file_tokens is None:
                per_file_tokens = _build_per_file_tokens(conn)
            referenced_externally = any(
                name in tokens
                for fid, tokens in per_file_tokens.items()
                if fid != own_file_id
            )
            if referenced_externally:
                rep.excluded_textual += 1
                continue
            rep.dead.append(
                DeadSymbol(
                    qualified_name=r["qualified_name"],
                    name=name,
                    kind=r["kind"],
                    rel_path=r["rel_path"],
                    lineno=r["lineno"],
                )
            )
    finally:
        conn.close()
    rep.dead.sort(key=lambda d: (d.rel_path, d.lineno))
    return rep
