"""context: explain the role of a symbol or file.

Resolution order for the user-supplied target:
  1. exact qualified_name match
  2. exact symbol name match
  3. file rel_path match (substring)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


@dataclass
class ContextResult:
    target: str
    kind: str  # 'symbol' | 'file' | 'not_found'
    rel_path: str | None = None
    qualified_name: str | None = None
    signature: str | None = None
    docstring: str | None = None
    imports_out: list[str] = field(default_factory=list)
    imports_in: list[str] = field(default_factory=list)
    callers: list[tuple[str, str, int]] = field(default_factory=list)  # (qname, rel_path, lineno)
    callees: list[tuple[str, str | None]] = field(default_factory=list)  # (callee_name, resolved_qname)
    siblings: list[tuple[str, str, int]] = field(default_factory=list)
    role_note: str = ""


def _resolve_target(conn, target: str) -> tuple[str, dict] | None:
    # 1. exact qualified name
    row = conn.execute(
        "SELECT s.*, f.rel_path FROM symbol s JOIN file f ON f.file_id=s.file_id "
        "WHERE s.qualified_name = ? LIMIT 1",
        (target,),
    ).fetchone()
    if row:
        return "symbol", dict(row)

    # 1b. qualified_name suffix — `GreetingService.greet` matches
    # `service.GreetingService.greet` and similar trailing forms.
    if "." in target:
        row = conn.execute(
            "SELECT s.*, f.rel_path FROM symbol s "
            "JOIN file f ON f.file_id=s.file_id "
            "WHERE s.qualified_name LIKE ? "
            "ORDER BY length(s.qualified_name) LIMIT 1",
            ("%." + target,),
        ).fetchone()
        if row:
            return "symbol", dict(row)
        # 1c. `Class.method` → method by parent_qname suffix.
        cls_part, method_part = target.rsplit(".", 1)
        row = conn.execute(
            """
            SELECT s.*, f.rel_path FROM symbol s
              JOIN file f ON f.file_id = s.file_id
             WHERE s.name = ?
               AND (s.parent_qname = ?
                    OR s.parent_qname LIKE ?)
               AND s.kind IN ('method', 'function', 'class')
             ORDER BY s.is_private, s.kind LIMIT 1
            """,
            (method_part, cls_part, "%." + cls_part),
        ).fetchone()
        if row:
            return "symbol", dict(row)

    # 2. symbol name
    row = conn.execute(
        "SELECT s.*, f.rel_path FROM symbol s JOIN file f ON f.file_id=s.file_id "
        "WHERE s.name = ? ORDER BY s.is_private, s.kind LIMIT 1",
        (target,),
    ).fetchone()
    if row:
        return "symbol", dict(row)

    # 3. file path / module path (substring + whole-word match preferred)
    norm = target.replace("\\", "/")
    row = conn.execute(
        "SELECT * FROM file WHERE rel_path = ? OR module_path = ? LIMIT 1",
        (norm, norm),
    ).fetchone()
    if row:
        return "file", dict(row)
    row = conn.execute(
        "SELECT * FROM file WHERE rel_path LIKE ? OR module_path LIKE ? LIMIT 1",
        (f"%{norm}%", f"%{norm}%"),
    ).fetchone()
    if row:
        return "file", dict(row)
    return None


def _imports_out(conn, file_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT imported_module, imported_name FROM import_edge WHERE file_id = ?",
        (file_id,),
    ).fetchall()
    out = []
    for r in rows:
        if r["imported_name"]:
            out.append(f"from {r['imported_module']} import {r['imported_name']}")
        else:
            out.append(f"import {r['imported_module']}")
    return out


def _imports_in(conn, file_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT f.rel_path
          FROM import_edge ie
          JOIN file f ON f.file_id = ie.file_id
         WHERE ie.resolved_file_id = ?
        """,
        (file_id,),
    ).fetchall()
    return [r["rel_path"] for r in rows]


def _role_note(rel_path: str, n_imports_in: int) -> str:
    p = rel_path.lower()
    notes = []
    if "/db/" in p or "/models" in p or p.endswith("models.py"):
        notes.append("db / data model")
    if "/cli/" in p or p.endswith("cli.py") or p.endswith("__main__.py"):
        notes.append("CLI surface")
    if "/api/" in p or "/routes" in p:
        notes.append("API entrypoint")
    if "/services/" in p:
        notes.append("service-layer orchestration")
    if "/parsers/" in p:
        notes.append("parsing layer")
    if "/scoring/" in p or "/ranker" in p:
        notes.append("scoring / ranking")
    if "test_" in p or "/tests/" in p:
        notes.append("test")
    if "config" in p or "settings" in p:
        notes.append("configuration")
    if n_imports_in >= 10:
        notes.append("widely imported (high blast radius)")
    elif n_imports_in >= 3:
        notes.append("moderately imported")
    return "; ".join(notes) or "ordinary module"


def context(repo_path: Path, target: str) -> ContextResult:
    conn = connect(repo_path)
    try:
        resolved = _resolve_target(conn, target)
        if resolved is None:
            return ContextResult(target=target, kind="not_found")

        kind, data = resolved
        if kind == "symbol":
            file_id = data["file_id"]
            file_row = conn.execute(
                "SELECT * FROM file WHERE file_id = ?", (file_id,)
            ).fetchone()

            callers_rows = conn.execute(
                """
                SELECT s.qualified_name, f.rel_path, ce.lineno
                  FROM call_edge ce
                  JOIN symbol s ON s.symbol_id = ce.caller_symbol_id
                  JOIN file f   ON f.file_id   = s.file_id
                 WHERE ce.resolved_symbol_id = ?
                 ORDER BY f.rel_path, ce.lineno
                """,
                (data["symbol_id"],),
            ).fetchall()

            callees_rows = conn.execute(
                """
                SELECT ce.callee_name, target.qualified_name AS resolved_qname
                  FROM call_edge ce
                  LEFT JOIN symbol target ON target.symbol_id = ce.resolved_symbol_id
                 WHERE ce.caller_symbol_id = ?
                 ORDER BY ce.lineno
                """,
                (data["symbol_id"],),
            ).fetchall()

            sibling_rows = conn.execute(
                """
                SELECT name, qualified_name, lineno
                  FROM symbol
                 WHERE file_id = ? AND symbol_id != ?
                 ORDER BY lineno LIMIT 30
                """,
                (file_id, data["symbol_id"]),
            ).fetchall()

            n_in = len(_imports_in(conn, file_id))
            return ContextResult(
                target=target,
                kind="symbol",
                rel_path=file_row["rel_path"],
                qualified_name=data["qualified_name"],
                signature=data["signature"],
                docstring=data["docstring"],
                imports_out=_imports_out(conn, file_id),
                imports_in=_imports_in(conn, file_id),
                callers=[(r["qualified_name"], r["rel_path"], r["lineno"]) for r in callers_rows],
                callees=[(r["callee_name"], r["resolved_qname"]) for r in callees_rows],
                siblings=[(r["name"], r["qualified_name"], r["lineno"]) for r in sibling_rows],
                role_note=_role_note(file_row["rel_path"], n_in),
            )

        # kind == "file"
        file_id = data["file_id"]
        symbol_rows = conn.execute(
            "SELECT name, qualified_name, lineno, kind FROM symbol "
            "WHERE file_id = ? ORDER BY lineno LIMIT 60",
            (file_id,),
        ).fetchall()
        n_in_list = _imports_in(conn, file_id)
        return ContextResult(
            target=target,
            kind="file",
            rel_path=data["rel_path"],
            qualified_name=data["module_path"],
            imports_out=_imports_out(conn, file_id),
            imports_in=n_in_list,
            siblings=[(r["name"], r["qualified_name"], r["lineno"]) for r in symbol_rows],
            role_note=_role_note(data["rel_path"], len(n_in_list)),
        )
    finally:
        conn.close()
