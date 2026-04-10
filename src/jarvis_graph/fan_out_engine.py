"""find_high_fan_out: rank files by how many *other in-repo files* they import.

Symmetric counterpart to `find_god_files` (which uses fan-in). High fan-out
flags files that depend on a large slice of the codebase: every change to
those dependencies has a non-zero chance of breaking this file. They are
the "client" hubs in the dependency graph — touching them is fine, but
breakage in any of their many dependencies will land here first.

What we measure:
  - `fan_out`           — distinct in-repo files this file imports from
                          (resolved import edges only, excluding self-imports)
  - `imports_total`     — every `import_edge` recorded for the file (incl.
                          unresolved third-party / stdlib)
  - `imports_resolved`  — `import_edge` rows where we successfully resolved
                          the target inside the repo

A file's `fan_out_pct` is `fan_out / total_files` so two repos can be
compared on a relative scale (a file importing 30 of 50 in-repo files is
more notable than one importing 30 of 5000).

The engine is one SQL select + a tiny Python post-pass; nothing slow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


@dataclass
class FanOutFile:
    rel_path: str
    module_path: str
    fan_out: int             # distinct in-repo files imported from
    imports_total: int       # every import_edge row, incl. unresolved
    imports_resolved: int    # subset that resolved to a file_id
    fan_out_pct: float       # fan_out as a fraction of total files in the repo
    risk: str                # low / medium / high


@dataclass
class FanOutReport:
    repo_path: str
    total_files: int = 0
    threshold: int = 0
    files: list[FanOutFile] = field(default_factory=list)


def _risk(fan_out: int, total_files: int) -> str:
    if total_files == 0:
        return "low"
    pct = fan_out / total_files
    if pct >= 0.20 or fan_out >= 30:
        return "high"
    if pct >= 0.08 or fan_out >= 12:
        return "medium"
    return "low"


def find_high_fan_out(
    repo_path: Path,
    threshold: int = 5,
    limit: int = 20,
) -> FanOutReport:
    """List files whose distinct in-repo import count is >= threshold.

    Args:
      threshold: minimum `fan_out` to include in the result. Defaults to 5
                 — anything below that is hard to argue is "too coupled".
      limit:     cap on the result list length.
    """
    repo_path = repo_path.resolve()
    rep = FanOutReport(repo_path=str(repo_path), threshold=threshold)
    conn = connect(repo_path)
    try:
        rep.total_files = int(
            conn.execute("SELECT COUNT(*) FROM file").fetchone()[0]
        )
        rows = conn.execute(
            """
            SELECT f.file_id,
                   f.rel_path,
                   f.module_path,
                   COUNT(DISTINCT CASE
                       WHEN ie.resolved_file_id IS NOT NULL
                        AND ie.resolved_file_id != f.file_id
                       THEN ie.resolved_file_id
                   END) AS fan_out,
                   COUNT(ie.edge_id) AS imports_total,
                   SUM(CASE WHEN ie.resolved_file_id IS NOT NULL THEN 1 ELSE 0 END) AS imports_resolved
              FROM file f
              LEFT JOIN import_edge ie ON ie.file_id = f.file_id
             GROUP BY f.file_id
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return rep

    enriched: list[FanOutFile] = []
    for r in rows:
        fan_out = int(r["fan_out"] or 0)
        if fan_out < threshold:
            continue
        enriched.append(
            FanOutFile(
                rel_path=r["rel_path"],
                module_path=r["module_path"] or "",
                fan_out=fan_out,
                imports_total=int(r["imports_total"] or 0),
                imports_resolved=int(r["imports_resolved"] or 0),
                fan_out_pct=(
                    round(fan_out / rep.total_files, 4) if rep.total_files else 0.0
                ),
                risk=_risk(fan_out, rep.total_files),
            )
        )

    enriched.sort(key=lambda f: (-f.fan_out, f.rel_path))
    rep.files = enriched[:limit]
    return rep
