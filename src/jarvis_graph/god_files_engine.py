"""find_god_files: rank files by symbol count, total LOC, and import fan-in.

A "god file" is one that has too many top-level definitions OR too many lines
OR is imported by too many other files. Each metric is reported separately so
the user can decide which dimension matters for the refactor at hand.

The query is one big SQL statement so we don't need engine-side joins; the
output is a flat list sorted by a small composite score that weights all
three dimensions equally on a 0-1 normalized scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


@dataclass
class GodFile:
    rel_path: str
    module_path: str
    symbol_count: int
    function_count: int
    class_count: int
    method_count: int
    total_loc: int  # sum of line_count over symbols (approx; doesn't include blank/import lines)
    fan_in: int  # how many other files import this one (resolved)
    score: float  # composite, 0..1


@dataclass
class GodFilesReport:
    repo_path: str
    total_files: int = 0
    files: list[GodFile] = field(default_factory=list)


def find_god_files(repo_path: Path, limit: int = 20) -> GodFilesReport:
    repo_path = repo_path.resolve()
    rep = GodFilesReport(repo_path=str(repo_path))
    conn = connect(repo_path)
    try:
        rep.total_files = int(
            conn.execute("SELECT COUNT(*) FROM file").fetchone()[0]
        )
        rows = conn.execute(
            """
            SELECT f.file_id, f.rel_path, f.module_path,
                   COUNT(s.symbol_id) AS sym_n,
                   SUM(CASE WHEN s.kind = 'function' THEN 1 ELSE 0 END) AS fn_n,
                   SUM(CASE WHEN s.kind = 'class'    THEN 1 ELSE 0 END) AS cls_n,
                   SUM(CASE WHEN s.kind = 'method'   THEN 1 ELSE 0 END) AS m_n,
                   COALESCE(SUM(s.line_count), 0) AS loc,
                   (SELECT COUNT(DISTINCT ie.file_id)
                      FROM import_edge ie
                     WHERE ie.resolved_file_id = f.file_id) AS fan_in
              FROM file f
              LEFT JOIN symbol s ON s.file_id = f.file_id
             GROUP BY f.file_id
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return rep

    # Normalize each axis to 0..1 against the max value seen, then average.
    # Composite weights: symbol count and LOC are intrinsic; fan_in is
    # extrinsic. Equal-weight to keep it simple to explain.
    max_sym = max(int(r["sym_n"] or 0) for r in rows) or 1
    max_loc = max(int(r["loc"] or 0) for r in rows) or 1
    max_fan = max(int(r["fan_in"] or 0) for r in rows) or 1

    enriched: list[GodFile] = []
    for r in rows:
        sym_n = int(r["sym_n"] or 0)
        if sym_n == 0:
            continue  # __init__.py and similar — not interesting
        loc = int(r["loc"] or 0)
        fan_in = int(r["fan_in"] or 0)
        score = round(
            (sym_n / max_sym + loc / max_loc + fan_in / max_fan) / 3.0,
            3,
        )
        enriched.append(
            GodFile(
                rel_path=r["rel_path"],
                module_path=r["module_path"] or "",
                symbol_count=sym_n,
                function_count=int(r["fn_n"] or 0),
                class_count=int(r["cls_n"] or 0),
                method_count=int(r["m_n"] or 0),
                total_loc=loc,
                fan_in=fan_in,
                score=score,
            )
        )

    enriched.sort(key=lambda g: (-g.score, -g.symbol_count, g.rel_path))
    rep.files = enriched[:limit]
    return rep
