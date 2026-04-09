"""find_complexity: rank functions/methods by McCabe cyclomatic complexity.

The complexity score itself is computed at parse time and stored in
``symbol.complexity`` (see ``parser_python._complexity``). This engine just
selects the top N over a configurable threshold and groups by file for the
report.

Risk buckets follow the same convention everyone else uses:
  1-5    : low      (a `def foo(): return 42` is 1)
  6-10   : medium   (still grokkable in one read)
  11-20  : high     (probably needs decomposition)
  21+    : extreme  (almost certainly buggy or untested)

We exclude `__init__`, dunder methods, and the synthetic `<module>` row;
those distort the top-N with churn nobody can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


_RISK_THRESHOLDS = (5, 10, 20)


def _bucket(score: int) -> str:
    if score <= _RISK_THRESHOLDS[0]:
        return "low"
    if score <= _RISK_THRESHOLDS[1]:
        return "medium"
    if score <= _RISK_THRESHOLDS[2]:
        return "high"
    return "extreme"


@dataclass
class ComplexHotspot:
    qualified_name: str
    name: str
    kind: str
    rel_path: str
    lineno: int
    complexity: int
    line_count: int
    risk: str


@dataclass
class ComplexityReport:
    repo_path: str
    threshold: int
    total_callables: int = 0
    high: int = 0
    extreme: int = 0
    average: float = 0.0
    hotspots: list[ComplexHotspot] = field(default_factory=list)


def find_complexity(
    repo_path: Path,
    threshold: int = 10,
    limit: int = 30,
) -> ComplexityReport:
    repo_path = repo_path.resolve()
    rep = ComplexityReport(repo_path=str(repo_path), threshold=threshold)
    conn = connect(repo_path)
    try:
        agg = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(AVG(complexity), 0.0) AS avg_c,
                   SUM(CASE WHEN complexity BETWEEN 11 AND 20 THEN 1 ELSE 0 END) AS hi,
                   SUM(CASE WHEN complexity > 20 THEN 1 ELSE 0 END) AS xt
              FROM symbol
             WHERE kind IN ('function', 'method')
               AND name NOT LIKE '\\_\\_%' ESCAPE '\\'
            """
        ).fetchone()
        rep.total_callables = int(agg["n"] or 0)
        rep.average = round(float(agg["avg_c"] or 0.0), 2)
        rep.high = int(agg["hi"] or 0)
        rep.extreme = int(agg["xt"] or 0)

        rows = conn.execute(
            """
            SELECT s.qualified_name, s.name, s.kind, s.lineno,
                   s.complexity, s.line_count, f.rel_path
              FROM symbol s
              JOIN file f ON f.file_id = s.file_id
             WHERE s.kind IN ('function', 'method')
               AND s.complexity >= ?
               AND s.name NOT LIKE '\\_\\_%' ESCAPE '\\'
             ORDER BY s.complexity DESC, f.rel_path, s.lineno
             LIMIT ?
            """,
            (threshold, limit),
        ).fetchall()
    finally:
        conn.close()

    for r in rows:
        rep.hotspots.append(
            ComplexHotspot(
                qualified_name=r["qualified_name"],
                name=r["name"],
                kind=r["kind"],
                rel_path=r["rel_path"],
                lineno=int(r["lineno"]),
                complexity=int(r["complexity"]),
                line_count=int(r["line_count"]),
                risk=_bucket(int(r["complexity"])),
            )
        )
    return rep
