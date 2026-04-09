"""find_long_functions: rank functions/methods by line count.

Pure SQL — line_count is materialized at parse time. Excludes the synthetic
`<module>` rows and dunder methods (auto-generated `__init__`s with 80 lines
of attribute wiring are usually fine, even if visually long).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


@dataclass
class LongFunction:
    qualified_name: str
    name: str
    kind: str
    rel_path: str
    lineno: int
    line_count: int
    complexity: int


@dataclass
class LongFunctionsReport:
    repo_path: str
    threshold: int
    total_callables: int = 0
    over_threshold: int = 0
    average: float = 0.0
    functions: list[LongFunction] = field(default_factory=list)


def find_long_functions(
    repo_path: Path,
    threshold: int = 50,
    limit: int = 30,
) -> LongFunctionsReport:
    repo_path = repo_path.resolve()
    rep = LongFunctionsReport(repo_path=str(repo_path), threshold=threshold)
    conn = connect(repo_path)
    try:
        agg = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(AVG(line_count), 0.0) AS avg_l,
                   SUM(CASE WHEN line_count >= ? THEN 1 ELSE 0 END) AS over
              FROM symbol
             WHERE kind IN ('function', 'method')
               AND name NOT LIKE '\\_\\_%' ESCAPE '\\'
            """,
            (threshold,),
        ).fetchone()
        rep.total_callables = int(agg["n"] or 0)
        rep.average = round(float(agg["avg_l"] or 0.0), 2)
        rep.over_threshold = int(agg["over"] or 0)

        rows = conn.execute(
            """
            SELECT s.qualified_name, s.name, s.kind, s.lineno,
                   s.line_count, s.complexity, f.rel_path
              FROM symbol s
              JOIN file f ON f.file_id = s.file_id
             WHERE s.kind IN ('function', 'method')
               AND s.line_count >= ?
               AND s.name NOT LIKE '\\_\\_%' ESCAPE '\\'
             ORDER BY s.line_count DESC, f.rel_path, s.lineno
             LIMIT ?
            """,
            (threshold, limit),
        ).fetchall()
    finally:
        conn.close()

    for r in rows:
        rep.functions.append(
            LongFunction(
                qualified_name=r["qualified_name"],
                name=r["name"],
                kind=r["kind"],
                rel_path=r["rel_path"],
                lineno=int(r["lineno"]),
                line_count=int(r["line_count"]),
                complexity=int(r["complexity"]),
            )
        )
    return rep
