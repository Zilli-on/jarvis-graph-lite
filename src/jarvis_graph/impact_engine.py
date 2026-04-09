"""impact: estimate blast radius of changing a symbol or file.

For a SYMBOL target:
  - direct callers (call_edge → caller symbol)
  - file-level importers of the symbol's file
  - second-order: callers-of-callers (one hop)
  - risk score from total reachable surface

For a FILE target:
  - direct importers
  - aggregated callers across every symbol defined in the file
  - second-order importers (files that import a direct importer)
  - risk score from total reachable surface

This is intentionally a heuristic — call resolution is best-effort, so the
numbers are a guide, not a proof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.context_engine import _resolve_target
from jarvis_graph.db import connect


@dataclass
class ImpactResult:
    target: str
    kind: str  # 'symbol' | 'file' | 'not_found'
    rel_path: str | None = None
    qualified_name: str | None = None
    direct_callers: list[tuple[str, str, int]] = field(default_factory=list)
    direct_importers: list[str] = field(default_factory=list)
    second_order: list[str] = field(default_factory=list)  # qnames or rel_paths
    risk: str = "low"  # low | medium | high
    why: list[str] = field(default_factory=list)


def _callers_of_symbol(conn, symbol_id: int) -> list[tuple[int, str, str, int]]:
    rows = conn.execute(
        """
        SELECT s.symbol_id, s.qualified_name, f.rel_path, ce.lineno
          FROM call_edge ce
          JOIN symbol s ON s.symbol_id = ce.caller_symbol_id
          JOIN file f   ON f.file_id   = s.file_id
         WHERE ce.resolved_symbol_id = ?
         ORDER BY f.rel_path, ce.lineno
        """,
        (symbol_id,),
    ).fetchall()
    return [(r["symbol_id"], r["qualified_name"], r["rel_path"], r["lineno"]) for r in rows]


def _importers_of_file(conn, file_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT f.rel_path
          FROM import_edge ie
          JOIN file f ON f.file_id = ie.file_id
         WHERE ie.resolved_file_id = ?
         ORDER BY f.rel_path
        """,
        (file_id,),
    ).fetchall()
    return [r["rel_path"] for r in rows]


def _file_id_for_rel_path(conn, rel_path: str) -> int | None:
    row = conn.execute(
        "SELECT file_id FROM file WHERE rel_path = ? LIMIT 1", (rel_path,)
    ).fetchone()
    return int(row["file_id"]) if row else None


def _score_risk(direct: int, second: int) -> tuple[str, list[str]]:
    why: list[str] = []
    total = direct + second
    if direct >= 10 or total >= 25:
        risk = "high"
        why.append(f"{direct} direct + {second} second-order dependents")
    elif direct >= 3 or total >= 8:
        risk = "medium"
        why.append(f"{direct} direct + {second} second-order dependents")
    else:
        risk = "low"
        why.append(f"only {direct} direct + {second} second-order dependents")
    return risk, why


def impact(repo_path: Path, target: str) -> ImpactResult:
    conn = connect(repo_path)
    try:
        resolved = _resolve_target(conn, target)
        if resolved is None:
            return ImpactResult(target=target, kind="not_found")

        kind, data = resolved

        if kind == "symbol":
            file_id = data["file_id"]
            file_row = conn.execute(
                "SELECT rel_path FROM file WHERE file_id = ?", (file_id,)
            ).fetchone()
            symbol_id = data["symbol_id"]

            direct = _callers_of_symbol(conn, symbol_id)
            direct_callers = [(q, p, ln) for (_sid, q, p, ln) in direct]

            # File-level importers (anyone who imports the module hosting this symbol)
            direct_importers = _importers_of_file(conn, file_id)

            # Second-order: callers of each direct caller
            second_set: set[str] = set()
            for sid, qn, _, _ in direct:
                for _sid2, q2, _p2, _ln2 in _callers_of_symbol(conn, sid):
                    if q2 != qn:
                        second_set.add(q2)
            second_order = sorted(second_set)

            risk, why = _score_risk(len(direct_callers) + len(direct_importers), len(second_order))
            if direct_importers:
                why.append(f"file is imported by {len(direct_importers)} module(s)")
            if not direct_callers and not direct_importers:
                why.append("no resolved callers found — may be unused or only called dynamically")

            return ImpactResult(
                target=target,
                kind="symbol",
                rel_path=file_row["rel_path"],
                qualified_name=data["qualified_name"],
                direct_callers=direct_callers,
                direct_importers=direct_importers,
                second_order=second_order,
                risk=risk,
                why=why,
            )

        # kind == "file"
        file_id = data["file_id"]
        rel_path = data["rel_path"]

        direct_importers = _importers_of_file(conn, file_id)

        # Aggregate callers across all symbols defined in this file, but
        # exclude calls that happen WITHIN the same file — for file-level
        # impact we only care about external coupling.
        sym_rows = conn.execute(
            "SELECT symbol_id, qualified_name FROM symbol WHERE file_id = ?",
            (file_id,),
        ).fetchall()
        callers_seen: dict[str, tuple[str, str, int]] = {}
        for srow in sym_rows:
            for _sid, q, p, ln in _callers_of_symbol(conn, srow["symbol_id"]):
                if p == rel_path:
                    continue
                key = f"{q}@{p}:{ln}"
                callers_seen.setdefault(key, (q, p, ln))
        direct_callers = sorted(callers_seen.values(), key=lambda t: (t[1], t[2]))

        # Second-order: files that import a direct importer
        second_set: set[str] = set()
        for imp_path in direct_importers:
            imp_fid = _file_id_for_rel_path(conn, imp_path)
            if imp_fid is None:
                continue
            for second in _importers_of_file(conn, imp_fid):
                if second != rel_path:
                    second_set.add(second)
        second_order = sorted(second_set)

        risk, why = _score_risk(
            len(direct_importers) + len(direct_callers), len(second_order)
        )
        if not direct_importers and not direct_callers:
            why.append("no resolved imports or calls — file may be a leaf script or entrypoint")

        return ImpactResult(
            target=target,
            kind="file",
            rel_path=rel_path,
            qualified_name=data["module_path"],
            direct_callers=direct_callers,
            direct_importers=direct_importers,
            second_order=second_order,
            risk=risk,
            why=why,
        )
    finally:
        conn.close()
