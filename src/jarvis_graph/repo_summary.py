"""Deterministic per-repo summary written to .jarvis_graph/summaries/.

A flat heuristic snapshot — file/symbol counts, top imported files,
top fan-in modules, top callers. No LLM, no embeddings.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jarvis_graph.db import connect
from jarvis_graph.utils import repo_data_dir


@dataclass
class RepoSummary:
    repo_path: str
    files: int = 0
    symbols: int = 0
    functions: int = 0
    classes: int = 0
    methods: int = 0
    constants: int = 0
    imports: int = 0
    calls: int = 0
    parse_errors: int = 0
    most_imported_files: list[tuple[str, int]] = field(default_factory=list)
    largest_files_by_symbols: list[tuple[str, int]] = field(default_factory=list)
    likely_entrypoints: list[str] = field(default_factory=list)


def summarize(repo_path: Path) -> RepoSummary:
    repo_path = repo_path.resolve()
    summary = RepoSummary(repo_path=str(repo_path))
    conn = connect(repo_path)
    try:
        summary.files = conn.execute("SELECT COUNT(*) AS c FROM file").fetchone()["c"]
        summary.parse_errors = conn.execute(
            "SELECT COUNT(*) AS c FROM file WHERE parse_error IS NOT NULL"
        ).fetchone()["c"]
        summary.symbols = conn.execute("SELECT COUNT(*) AS c FROM symbol").fetchone()["c"]
        summary.imports = conn.execute("SELECT COUNT(*) AS c FROM import_edge").fetchone()["c"]
        summary.calls = conn.execute("SELECT COUNT(*) AS c FROM call_edge").fetchone()["c"]

        for kind, attr in [
            ("function", "functions"),
            ("class", "classes"),
            ("method", "methods"),
            ("constant", "constants"),
        ]:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM symbol WHERE kind = ?", (kind,)
            ).fetchone()["c"]
            setattr(summary, attr, n)

        most_imported = conn.execute(
            """
            SELECT f.rel_path, COUNT(*) AS n
              FROM import_edge ie
              JOIN file f ON f.file_id = ie.resolved_file_id
             GROUP BY f.rel_path
             ORDER BY n DESC
             LIMIT 15
            """
        ).fetchall()
        summary.most_imported_files = [(r["rel_path"], r["n"]) for r in most_imported]

        biggest = conn.execute(
            """
            SELECT f.rel_path, COUNT(s.symbol_id) AS n
              FROM file f
              LEFT JOIN symbol s ON s.file_id = f.file_id
             GROUP BY f.rel_path
             ORDER BY n DESC
             LIMIT 15
            """
        ).fetchall()
        summary.largest_files_by_symbols = [(r["rel_path"], r["n"]) for r in biggest]

        entry_rows = conn.execute(
            """
            SELECT rel_path FROM file
             WHERE rel_path LIKE '%__main__.py'
                OR rel_path LIKE '%cli.py'
                OR rel_path LIKE '%/main.py'
                OR rel_path = 'main.py'
                OR rel_path LIKE '%/manage.py'
             ORDER BY rel_path LIMIT 20
            """
        ).fetchall()
        summary.likely_entrypoints = [r["rel_path"] for r in entry_rows]
    finally:
        conn.close()

    out_dir = repo_data_dir(repo_path) / "summaries"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "repo_summary.json").write_text(
        json.dumps(asdict(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary
