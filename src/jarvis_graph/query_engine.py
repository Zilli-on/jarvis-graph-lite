"""query: locate where a concept/symbol/file lives.

Strategy: split the question into 1-5 keywords (drop common words), search
symbol names, qualified names, file rel_paths, and docstrings, score with
the lexical ranker, then apply two extra signals before returning top N:

  * **multi-token bonus**: a result that matches every token in the query
    gets a multiplier proportional to its coverage. Encourages ANDing
    without strictly excluding partial matches.
  * **recency boost**: results in files modified recently get a small
    additive bonus. The most-recently-touched file gets the full boost
    and older files decay linearly to zero. Helps surface "the thing the
    user was just working on" when the lexical signal is ambiguous.

Pass `match_all=True` to enforce strict AND across tokens.
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

# Bonus added (additive) to the score of the most-recently-touched file.
_RECENCY_BONUS_MAX = 8

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


def query(
    repo_path: Path,
    question: str,
    limit: int = 20,
    match_all: bool = False,
) -> list[QueryHit]:
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
                   s.lineno, f.rel_path, f.mtime
              FROM symbol s
              JOIN file f ON f.file_id = s.file_id
             WHERE {sym_where}
             LIMIT 500
        """
        sym_rows = conn.execute(sym_sql, sym_params).fetchall()

        path_like = " OR ".join(["LOWER(f.rel_path) LIKE ?" for _ in tokens])
        path_params = [f"%{t.lower()}%" for t in tokens]
        file_rows = conn.execute(
            f"SELECT f.rel_path, f.module_path, f.mtime FROM file f "
            f"WHERE {path_like} LIMIT 200",
            path_params,
        ).fetchall()

        # mtime range across the WHOLE repo so the recency boost is stable
        # regardless of how many candidates the query produced.
        mtime_row = conn.execute(
            "SELECT MIN(mtime) AS lo, MAX(mtime) AS hi FROM file"
        ).fetchone()
    finally:
        conn.close()

    lo = int(mtime_row["lo"] or 0)
    hi = int(mtime_row["hi"] or 0)
    span = max(1, hi - lo)

    def recency_bonus(mtime: int | None) -> int:
        if not mtime or hi == lo:
            return 0
        norm = (int(mtime) - lo) / span  # 0..1
        return int(round(norm * _RECENCY_BONUS_MAX))

    hits: list[QueryHit] = []
    n_tokens = len(tokens)

    for row in sym_rows:
        per_token: list[int] = []
        for t in tokens:
            sc = (
                score_symbol_name(row["name"], t)
                + score_qname(row["qualified_name"], t)
                + score_docstring(row["docstring"], t)
            )
            per_token.append(sc)
        total = sum(per_token)
        if total <= 0:
            continue
        matched = sum(1 for sc in per_token if sc > 0)
        if match_all and matched < n_tokens:
            continue
        # Soft AND multiplier: 1.0 for all-matched, 0.5 for half-matched, etc.
        coverage = 0.5 + 0.5 * (matched / n_tokens) if n_tokens else 1.0
        s = int(round(total * coverage)) + recency_bonus(row["mtime"])
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
        per_token = [score_path(row["rel_path"], t) for t in tokens]
        total = sum(per_token)
        if total <= 0:
            continue
        matched = sum(1 for sc in per_token if sc > 0)
        if match_all and matched < n_tokens:
            continue
        coverage = 0.5 + 0.5 * (matched / n_tokens) if n_tokens else 1.0
        s = int(round(total * coverage)) + recency_bonus(row["mtime"])
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
