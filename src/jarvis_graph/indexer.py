"""Indexing pipeline.

  index_repo(repo_path, full=False) →
    1. Walk *.py files in the repo
    2. For each: hash → if hash matches existing file row, SKIP
    3. Else: parse → DELETE old rows for that file → INSERT new rows
    4. After file pass: resolve import_edge.resolved_file_id by matching
       imported_module → file.module_path
    5. Resolve call_edge.resolved_symbol_id by matching callee_name (last
       segment) → symbol.name within the same file or imported modules
    6. Update meta + config

Pure Python, single SQLite connection, single transaction at the end. Files
that disappeared from the repo are detected and their rows are removed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from jarvis_graph import config, logging_utils
from jarvis_graph.db import connect
from jarvis_graph.models import ParsedFile
from jarvis_graph.parser_python import parse_python_file
from jarvis_graph.utils import iter_python_files, now_epoch


@dataclass
class IndexReport:
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped_unchanged: int = 0
    files_removed: int = 0
    files_with_errors: int = 0
    symbols_total: int = 0
    imports_total: int = 0
    calls_total: int = 0
    elapsed_seconds: float = 0.0


def _delete_file_rows(conn, file_id: int) -> None:
    # ON DELETE CASCADE handles symbols/imports — but call_edge references
    # symbol_id which itself cascades from file_id, so this single delete is
    # enough.
    conn.execute("DELETE FROM file WHERE file_id = ?", (file_id,))


def _insert_parsed_file(conn, pf: ParsedFile) -> int:
    cur = conn.execute(
        """
        INSERT INTO file (rel_path, abs_path, module_path, sha256, size_bytes,
                          mtime, indexed_at, parse_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pf.rel_path,
            pf.abs_path,
            pf.module_path,
            pf.sha256,
            pf.size_bytes,
            pf.mtime,
            now_epoch(),
            pf.parse_error,
        ),
    )
    file_id = int(cur.lastrowid)

    # Symbols (two-pass: insert symbols, build qname→id map for call resolution)
    qname_to_id: dict[str, int] = {}
    for sym in pf.symbols:
        cur = conn.execute(
            """
            INSERT INTO symbol (file_id, name, qualified_name, kind, parent_qname,
                                lineno, end_lineno, col, docstring, signature, is_private)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                sym.name,
                sym.qualified_name,
                sym.kind,
                sym.parent_qname,
                sym.lineno,
                sym.end_lineno,
                sym.col,
                sym.docstring,
                sym.signature,
                sym.is_private,
            ),
        )
        qname_to_id[sym.qualified_name] = int(cur.lastrowid)

    for imp in pf.imports:
        conn.execute(
            """
            INSERT INTO import_edge (file_id, imported_module, imported_name,
                                     alias, lineno, resolved_file_id)
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (file_id, imp.imported_module, imp.imported_name, imp.alias, imp.lineno),
        )

    for call in pf.calls:
        caller_id = qname_to_id.get(call.caller_qname)
        if caller_id is None:
            continue
        conn.execute(
            """
            INSERT INTO call_edge (caller_symbol_id, callee_name,
                                   resolved_symbol_id, lineno)
            VALUES (?, ?, NULL, ?)
            """,
            (caller_id, call.callee_name, call.lineno),
        )
    return file_id


def _resolve_imports(conn) -> None:
    """Resolve import edges in two passes.

    Pass 1: exact `module_path = imported_module` (cleanest, dotted match).
    Pass 2: suffix fallback for flat sys.path layouts where callers do
            `from claude_bridge import X` but the file's module path is
            `agents.claude_bridge`. Only resolves when exactly one candidate
            exists, so we don't pick the wrong file when basenames collide.
    """
    conn.execute(
        """
        UPDATE import_edge
           SET resolved_file_id = (
               SELECT f.file_id
                 FROM file f
                WHERE f.module_path = import_edge.imported_module
                LIMIT 1
           )
         WHERE resolved_file_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE import_edge
           SET resolved_file_id = (
               SELECT f.file_id
                 FROM file f
                WHERE f.module_path = import_edge.imported_module
                   OR f.module_path LIKE '%.' || import_edge.imported_module
                LIMIT 1
           )
         WHERE resolved_file_id IS NULL
           AND import_edge.imported_module NOT LIKE '.%'
           AND (
               SELECT COUNT(*) FROM file f
                WHERE f.module_path = import_edge.imported_module
                   OR f.module_path LIKE '%.' || import_edge.imported_module
           ) = 1
        """
    )


def _resolve_calls(conn) -> None:
    """Best-effort: link `call_edge.callee_name` to a known symbol.

    Strategies, in order of confidence:
      b)  `bar`             → same-file symbol with `name = 'bar'`.
      c)  `bar`             → cross-module symbol where the caller's file has
                               an `import_edge` with `imported_name = 'bar'`
                               and a resolved target file.
                               Covers `from X import bar; bar()`.
      m1) `Cls.method`      → method whose `parent_qname` ends in `.Cls` (or
                               equals `Cls`) and the caller imports `Cls`.
                               Covers `var = Cls(); var.method()` after the
                               parser has already rewritten `var.method` →
                               `Cls.method`.
      m2) `Cls.method`      → same-file class method (Cls defined in caller's
                               own file). Covers `self.method()` rewrites.
      a)  `foo.bar`         → last segment `bar` resolved against a symbol in
                               a file reachable via caller's file imports.
    """
    # (b) same-file resolution — cheap and safe.
    conn.execute(
        """
        UPDATE call_edge
           SET resolved_symbol_id = (
               SELECT s.symbol_id
                 FROM symbol s
                 JOIN symbol caller ON caller.symbol_id = call_edge.caller_symbol_id
                WHERE s.file_id = caller.file_id
                  AND s.name = call_edge.callee_name
                  AND s.kind IN ('function', 'method', 'class')
                LIMIT 1
           )
         WHERE resolved_symbol_id IS NULL
           AND instr(call_edge.callee_name, '.') = 0
        """
    )
    # (c) cross-module via `from X import bar` — requires resolved import edge.
    conn.execute(
        """
        UPDATE call_edge
           SET resolved_symbol_id = (
               SELECT s.symbol_id
                 FROM symbol s
                 JOIN import_edge ie ON ie.resolved_file_id = s.file_id
                                    AND ie.imported_name = call_edge.callee_name
                 JOIN symbol caller ON caller.symbol_id = call_edge.caller_symbol_id
                WHERE ie.file_id = caller.file_id
                  AND s.name = call_edge.callee_name
                  AND s.kind IN ('function', 'method', 'class')
                LIMIT 1
           )
         WHERE resolved_symbol_id IS NULL
           AND instr(call_edge.callee_name, '.') = 0
        """
    )
    # (m1) `Cls.method` where Cls is imported into the caller's file.
    # Stronger than the generic dotted path because we filter by `parent_qname`,
    # so we land on the *right* method when multiple classes share a name.
    conn.execute(
        """
        UPDATE call_edge
           SET resolved_symbol_id = (
               SELECT s.symbol_id
                 FROM symbol s
                 JOIN file f         ON f.file_id = s.file_id
                 JOIN symbol caller  ON caller.symbol_id = call_edge.caller_symbol_id
                 JOIN import_edge ie ON ie.file_id = caller.file_id
                                    AND ie.resolved_file_id = f.file_id
                                    AND ie.imported_name = substr(
                                        call_edge.callee_name, 1,
                                        instr(call_edge.callee_name, '.') - 1)
                WHERE s.kind IN ('method', 'function')
                  AND s.name = substr(
                      call_edge.callee_name,
                      instr(call_edge.callee_name, '.') + 1)
                  AND (
                      s.parent_qname = ie.imported_name
                      OR s.parent_qname LIKE '%.' || ie.imported_name
                  )
                LIMIT 1
           )
         WHERE resolved_symbol_id IS NULL
           AND instr(call_edge.callee_name, '.') > 0
           AND length(call_edge.callee_name)
               - length(replace(call_edge.callee_name, '.', '')) = 1
        """
    )
    # (m2) `Cls.method` where Cls is defined in the caller's own file.
    # Covers `self.method()` rewrites: same-file class without an import.
    conn.execute(
        """
        UPDATE call_edge
           SET resolved_symbol_id = (
               SELECT s.symbol_id
                 FROM symbol s
                 JOIN symbol caller ON caller.symbol_id = call_edge.caller_symbol_id
                WHERE s.file_id = caller.file_id
                  AND s.kind IN ('method', 'function')
                  AND s.name = substr(
                      call_edge.callee_name,
                      instr(call_edge.callee_name, '.') + 1)
                  AND (
                      s.parent_qname = substr(
                          call_edge.callee_name, 1,
                          instr(call_edge.callee_name, '.') - 1)
                      OR s.parent_qname LIKE '%.' || substr(
                          call_edge.callee_name, 1,
                          instr(call_edge.callee_name, '.') - 1)
                  )
                LIMIT 1
           )
         WHERE resolved_symbol_id IS NULL
           AND instr(call_edge.callee_name, '.') > 0
           AND length(call_edge.callee_name)
               - length(replace(call_edge.callee_name, '.', '')) = 1
        """
    )
    # (a) dotted resolution via imports.
    # We use the last segment of callee_name and match against any symbol in a
    # file whose module_path matches an import of the caller's file. This is
    # a best-effort heuristic; uncertainty is acceptable.
    conn.execute(
        """
        UPDATE call_edge
           SET resolved_symbol_id = (
               SELECT s.symbol_id
                 FROM symbol s
                 JOIN file f         ON f.file_id = s.file_id
                 JOIN symbol caller  ON caller.symbol_id = call_edge.caller_symbol_id
                 JOIN import_edge ie ON ie.file_id = caller.file_id
                                    AND (ie.imported_module = f.module_path
                                         OR ie.imported_name =
                                            substr(call_edge.callee_name,
                                                   instr(call_edge.callee_name,'.')+1))
                WHERE s.name = (
                    CASE WHEN instr(call_edge.callee_name,'.') > 0
                         THEN substr(call_edge.callee_name,
                                     length(call_edge.callee_name) -
                                     length(replace(call_edge.callee_name,'.','')) + 1)
                         ELSE call_edge.callee_name
                    END
                )
                  AND s.kind IN ('function', 'method', 'class')
                LIMIT 1
           )
         WHERE resolved_symbol_id IS NULL
           AND instr(call_edge.callee_name, '.') > 0
        """
    )


def index_repo(repo_path: Path, full: bool = False) -> IndexReport:
    """Index (or re-index) the given repo path. `full=True` wipes the index first."""
    repo_path = repo_path.resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise FileNotFoundError(f"repo not found or not a directory: {repo_path}")

    started = time.time()
    report = IndexReport()
    conn = connect(repo_path)
    try:
        if full:
            conn.execute("DELETE FROM file")
            conn.commit()

        # Snapshot existing rows for diffing.
        existing: dict[str, tuple[int, str]] = {}
        for row in conn.execute("SELECT file_id, rel_path, sha256 FROM file"):
            existing[row["rel_path"]] = (int(row["file_id"]), row["sha256"])

        seen_rel: set[str] = set()
        for abs_path, rel_path in iter_python_files(repo_path):
            report.files_seen += 1
            rel_str = str(rel_path).replace("\\", "/")
            seen_rel.add(rel_str)

            try:
                pf = parse_python_file(abs_path, rel_path)
            except Exception as exc:  # noqa: BLE001 — defensive
                report.files_with_errors += 1
                logging_utils.log(repo_path, "parse_failed", f"{rel_str}: {exc}")
                continue

            prev = existing.get(rel_str)
            if prev is not None and prev[1] == pf.sha256 and not full:
                report.files_skipped_unchanged += 1
                continue

            if prev is not None:
                _delete_file_rows(conn, prev[0])

            _insert_parsed_file(conn, pf)
            report.files_indexed += 1
            report.symbols_total += len(pf.symbols)
            report.imports_total += len(pf.imports)
            report.calls_total += len(pf.calls)
            if pf.parse_error:
                report.files_with_errors += 1

        # Files that disappeared from disk → drop them.
        for rel_str, (fid, _) in existing.items():
            if rel_str not in seen_rel:
                _delete_file_rows(conn, fid)
                report.files_removed += 1

        _resolve_imports(conn)
        _resolve_calls(conn)
        conn.commit()
    finally:
        conn.close()

    cfg = config.load(repo_path)
    config.save(repo_path, cfg)
    report.elapsed_seconds = round(time.time() - started, 2)

    logging_utils.log(
        repo_path,
        "index",
        f"seen={report.files_seen} indexed={report.files_indexed} "
        f"skipped={report.files_skipped_unchanged} removed={report.files_removed} "
        f"errors={report.files_with_errors} elapsed={report.elapsed_seconds}s",
    )
    return report
