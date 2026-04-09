"""query: locate where a concept/symbol/file lives.

Strategy: split the question into 1-3 keywords (drop common words), search
symbol names, qualified names, file rel_paths, and docstrings, score with
ranker, return top N.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jarvis_graph.db import connect
from jarvis_graph.ranker import (
    score_docstring,
    score_path,
    score_qname,
    score_symbol_name,
)

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "is", "are", "of", "to", "for", "in", "on", "with",
        "and", "or", "what", "where", "which", "how", "why", "find", "show",
        "me", "the", "my", "this", "that", "any", "all", "do", "does",
    }
)


@dataclass
class QueryHit:
    score: int
    kind: str  # 'symbol' | 'file'
    name: str
    qualified_name: str | None
    rel_path: str
    lineno: int | None
    snippet: str | None


def _tokenize(question: str) -> list[str]:
    raw = [t.strip("()[]{},.\"'?!:;") for t in question.split()]
    return [t for t in raw if t and t.lower() not in _STOPWORDS][:5]


def query(repo_path: Path, question: str, limit: int = 20) -> list[QueryHit]:
    tokens = _tokenize(question)
    if not tokens:
        return []
    conn = connect(repo_path)
    try:
        # Pull a candidate pool with cheap LIKE filters, then score in Python.
        like_clauses = " OR ".join(
            ["LOWER(s.name) LIKE ?", "LOWER(s.qualified_name) LIKE ?",
             "LOWER(s.docstring) LIKE ?"]
        )
        sym_params: list[str] = []
        for t in tokens:
            like = f"%{t.lower()}%"
            sym_params.extend([like, like, like])
        sym_where = " OR ".join(f"({like_clauses})" for _ in tokens)
        sym_sql = f"""
            SELECT s.name, s.qualified_name, s.kind, s.docstring,
                   s.lineno, f.rel_path
              FROM symbol s
              JOIN file f ON f.file_id = s.file_id
             WHERE {sym_where}
             LIMIT 500
        """
        sym_rows = conn.execute(sym_sql, sym_params).fetchall()

        path_like = " OR ".join(["LOWER(f.rel_path) LIKE ?" for _ in tokens])
        path_params = [f"%{t.lower()}%" for t in tokens]
        file_rows = conn.execute(
            f"SELECT f.rel_path, f.module_path FROM file f WHERE {path_like} LIMIT 200",
            path_params,
        ).fetchall()
    finally:
        conn.close()

    hits: list[QueryHit] = []
    for row in sym_rows:
        s = 0
        for t in tokens:
            s += score_symbol_name(row["name"], t)
            s += score_qname(row["qualified_name"], t)
            s += score_docstring(row["docstring"], t)
        if s <= 0:
            continue
        snippet = (row["docstring"] or "").strip().splitlines()[0:1]
        hits.append(
            QueryHit(
                score=s,
                kind=row["kind"],
                name=row["name"],
                qualified_name=row["qualified_name"],
                rel_path=row["rel_path"],
                lineno=row["lineno"],
                snippet=snippet[0] if snippet else None,
            )
        )

    seen_paths = {h.rel_path for h in hits}
    for row in file_rows:
        if row["rel_path"] in seen_paths:
            continue
        s = 0
        for t in tokens:
            s += score_path(row["rel_path"], t)
        if s <= 0:
            continue
        hits.append(
            QueryHit(
                score=s,
                kind="file",
                name=row["module_path"] or row["rel_path"],
                qualified_name=row["module_path"],
                rel_path=row["rel_path"],
                lineno=None,
                snippet=None,
            )
        )

    hits.sort(key=lambda h: (-h.score, h.rel_path, h.lineno or 0))
    return hits[:limit]
