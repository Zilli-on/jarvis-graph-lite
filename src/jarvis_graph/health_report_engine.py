"""health_report: build a single Markdown report combining every quality signal.

Calls the existing engines and stitches their results into one document, so
the user gets a complete "state of the repo" view from a single CLI invocation.

The report has nine fixed sections plus an optional drift section when a
baseline is supplied:
  1. Headline numbers (files, symbols, calls, resolution rate)
  2. Hotspots (top complexity)
  3. Long functions (top by line count)
  4. God files — high fan-in (top by composite score)
  5. Client hubs — high fan-out (files importing many others)
  6. Dead code (top dead candidates)
  7. Coverage gaps (top untested risky symbols)
  8. Unused imports (top files with most unused imports)
  9. Circular dependencies (full list)
 10. Drift since baseline (only when --baseline is provided)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_graph.circular_deps_engine import find_circular_deps
from jarvis_graph.complexity_engine import find_complexity
from jarvis_graph.coverage_gap_engine import find_coverage_gaps
from jarvis_graph.db import connect
from jarvis_graph.dead_code_engine import find_dead_code
from jarvis_graph.drift_engine import compute_drift, render_drift_markdown
from jarvis_graph.fan_out_engine import find_high_fan_out
from jarvis_graph.god_files_engine import find_god_files
from jarvis_graph.long_functions_engine import find_long_functions
from jarvis_graph.unused_imports_engine import find_unused_imports


@dataclass
class HealthReport:
    repo_path: str
    markdown: str
    summary: dict  # one-shot stats for JSON consumers


def _headline_stats(repo_path: Path) -> dict:
    conn = connect(repo_path)
    try:
        files = int(conn.execute("SELECT COUNT(*) FROM file").fetchone()[0])
        files_err = int(
            conn.execute(
                "SELECT COUNT(*) FROM file WHERE parse_error IS NOT NULL"
            ).fetchone()[0]
        )
        symbols = int(conn.execute("SELECT COUNT(*) FROM symbol").fetchone()[0])
        functions = int(
            conn.execute(
                "SELECT COUNT(*) FROM symbol WHERE kind = 'function'"
            ).fetchone()[0]
        )
        classes = int(
            conn.execute(
                "SELECT COUNT(*) FROM symbol WHERE kind = 'class'"
            ).fetchone()[0]
        )
        methods = int(
            conn.execute(
                "SELECT COUNT(*) FROM symbol WHERE kind = 'method'"
            ).fetchone()[0]
        )
        imports = int(conn.execute("SELECT COUNT(*) FROM import_edge").fetchone()[0])
        imports_resolved = int(
            conn.execute(
                "SELECT COUNT(*) FROM import_edge WHERE resolved_file_id IS NOT NULL"
            ).fetchone()[0]
        )
        calls = int(conn.execute("SELECT COUNT(*) FROM call_edge").fetchone()[0])
        calls_resolved = int(
            conn.execute(
                "SELECT COUNT(*) FROM call_edge WHERE resolved_symbol_id IS NOT NULL"
            ).fetchone()[0]
        )
    finally:
        conn.close()
    return {
        "files": files,
        "files_with_parse_errors": files_err,
        "symbols": symbols,
        "functions": functions,
        "classes": classes,
        "methods": methods,
        "imports": imports,
        "imports_resolved": imports_resolved,
        "imports_resolution_pct": (
            round(100.0 * imports_resolved / imports, 1) if imports else 0.0
        ),
        "calls": calls,
        "calls_resolved": calls_resolved,
        "calls_resolution_pct": (
            round(100.0 * calls_resolved / calls, 1) if calls else 0.0
        ),
    }


def health_report(
    repo_path: Path,
    complexity_threshold: int = 10,
    long_threshold: int = 50,
    top_n: int = 15,
    fan_out_threshold: int = 8,
    coverage_min_complexity: int = 5,
    baseline: dict | None = None,
) -> HealthReport:
    repo_path = repo_path.resolve()
    headline = _headline_stats(repo_path)
    cx = find_complexity(repo_path, threshold=complexity_threshold, limit=top_n)
    lf = find_long_functions(repo_path, threshold=long_threshold, limit=top_n)
    god = find_god_files(repo_path, limit=top_n)
    fan_out = find_high_fan_out(repo_path, threshold=fan_out_threshold, limit=top_n)
    dead = find_dead_code(repo_path)
    coverage = find_coverage_gaps(
        repo_path,
        limit=top_n,
        min_complexity=coverage_min_complexity,
    )
    unused = find_unused_imports(repo_path)
    cycles = find_circular_deps(repo_path)

    # Group unused-imports by file so the report shows hot files first
    # rather than 327 individual lines.
    file_unused = Counter()
    for u in unused.unused:
        file_unused[u.rel_path] += 1
    top_unused_files = file_unused.most_common(top_n)

    lines: list[str] = []
    lines.append("# jarvis-graph-lite health report")
    lines.append("")
    lines.append(f"**Repo**: `{repo_path}`")
    lines.append("")

    # 1. headline
    lines.append("## 1. Headline")
    lines.append("")
    lines.append(f"- **{headline['files']}** files, "
                 f"{headline['files_with_parse_errors']} parse errors")
    lines.append(f"- **{headline['symbols']}** symbols "
                 f"({headline['functions']} functions, "
                 f"{headline['classes']} classes, "
                 f"{headline['methods']} methods)")
    lines.append(f"- **{headline['imports']}** imports "
                 f"({headline['imports_resolution_pct']}% resolved)")
    lines.append(f"- **{headline['calls']}** call sites "
                 f"({headline['calls_resolution_pct']}% resolved)")
    lines.append("")

    # 2. complexity hotspots
    lines.append(f"## 2. Complexity hotspots (cyclomatic ≥ {complexity_threshold})")
    lines.append("")
    lines.append(f"- {cx.total_callables} callables · "
                 f"avg cyclomatic = {cx.average} · "
                 f"high = {cx.high} · extreme = {cx.extreme}")
    lines.append("")
    if cx.hotspots:
        lines.append("| # | complexity | lines | function | file |")
        lines.append("|--:|--:|--:|---|---|")
        for i, h in enumerate(cx.hotspots, 1):
            lines.append(
                f"| {i} | {h.complexity} | {h.line_count} | "
                f"`{h.qualified_name}` | `{h.rel_path}:{h.lineno}` |"
            )
    else:
        lines.append("_None over threshold._")
    lines.append("")

    # 3. long functions
    lines.append(f"## 3. Long functions (≥ {long_threshold} lines)")
    lines.append("")
    lines.append(f"- {lf.over_threshold} of {lf.total_callables} callables "
                 f"over threshold · avg = {lf.average} lines")
    lines.append("")
    if lf.functions:
        lines.append("| # | lines | cyclomatic | function | file |")
        lines.append("|--:|--:|--:|---|---|")
        for i, fn in enumerate(lf.functions, 1):
            lines.append(
                f"| {i} | {fn.line_count} | {fn.complexity} | "
                f"`{fn.qualified_name}` | `{fn.rel_path}:{fn.lineno}` |"
            )
    else:
        lines.append("_None over threshold._")
    lines.append("")

    # 4. god files
    lines.append("## 4. God files (composite of symbols × LOC × fan-in)")
    lines.append("")
    if god.files:
        lines.append("| # | score | symbols | LOC | fan-in | file |")
        lines.append("|--:|--:|--:|--:|--:|---|")
        for i, gf in enumerate(god.files, 1):
            lines.append(
                f"| {i} | {gf.score} | {gf.symbol_count} | {gf.total_loc} | "
                f"{gf.fan_in} | `{gf.rel_path}` |"
            )
    else:
        lines.append("_No files indexed._")
    lines.append("")

    # 5. client hubs (fan-out)
    lines.append(f"## 5. Client hubs — high fan-out (≥ {fan_out_threshold} in-repo imports)")
    lines.append("")
    lines.append(f"- {len(fan_out.files)} file(s) above threshold "
                 f"of {fan_out.total_files} total")
    lines.append("")
    if fan_out.files:
        lines.append("| # | fan-out | %repo | imports | risk | file |")
        lines.append("|--:|--:|--:|--:|---|---|")
        for i, fo in enumerate(fan_out.files, 1):
            pct = round(fo.fan_out_pct * 100, 1)
            lines.append(
                f"| {i} | {fo.fan_out} | {pct}% | {fo.imports_total} | "
                f"{fo.risk} | `{fo.rel_path}` |"
            )
    else:
        lines.append("_None over threshold._")
    lines.append("")

    # 6. dead code
    lines.append("## 6. Dead code candidates")
    lines.append("")
    lines.append(f"- {len(dead.dead)} candidates after filtering "
                 f"{dead.total_checked} symbols")
    lines.append(f"  (excluded: dunder={dead.excluded_dunder}, "
                 f"private={dead.excluded_private}, "
                 f"entrypoint={dead.excluded_entrypoint}, "
                 f"test={dead.excluded_test}, "
                 f"textual={dead.excluded_textual})")
    lines.append("")
    if dead.dead:
        # Group by file, show top files with dead-code counts
        dead_by_file = Counter(d.rel_path for d in dead.dead)
        lines.append("Top files by dead-symbol count:")
        lines.append("")
        lines.append("| count | file |")
        lines.append("|--:|---|")
        for path, n in dead_by_file.most_common(top_n):
            lines.append(f"| {n} | `{path}` |")
    else:
        lines.append("_None._")
    lines.append("")

    # 7. coverage gaps
    lines.append(
        f"## 7. Coverage gaps (untested public symbols, "
        f"min_complexity={coverage_min_complexity})"
    )
    lines.append("")
    if coverage.test_entry_points == 0:
        lines.append(f"_{coverage.note}_")
    else:
        lines.append(
            f"- coverage = **{coverage.coverage_pct}%** "
            f"({coverage.reached_count} reached / "
            f"{coverage.total_public_symbols} public symbols, "
            f"{coverage.test_entry_points} test entry points)"
        )
        lines.append("")
        if coverage.gaps:
            lines.append("Top untested risky symbols (sorted by complexity):")
            lines.append("")
            lines.append("| # | cyclomatic | lines | callers | symbol | file |")
            lines.append("|--:|--:|--:|--:|---|---|")
            for i, g in enumerate(coverage.gaps, 1):
                lines.append(
                    f"| {i} | {g.complexity} | {g.line_count} | {g.caller_count} | "
                    f"`{g.qualified_name}` | `{g.rel_path}:{g.lineno}` |"
                )
        else:
            lines.append("_No gaps over min_complexity._")
    lines.append("")

    # 8. unused imports
    lines.append("## 8. Unused imports")
    lines.append("")
    lines.append(f"- {len(unused.unused)} unused of {unused.total_imports} imports")
    lines.append("")
    if top_unused_files:
        lines.append("Top files by unused-import count:")
        lines.append("")
        lines.append("| count | file |")
        lines.append("|--:|---|")
        for path, n in top_unused_files:
            lines.append(f"| {n} | `{path}` |")
    else:
        lines.append("_None._")
    lines.append("")

    # 9. circular deps
    lines.append("## 9. Circular dependencies")
    lines.append("")
    lines.append(f"- {len(cycles.cycles)} cycle(s) on "
                 f"{cycles.total_files} files / {cycles.total_edges} resolved edges")
    lines.append("")
    if cycles.cycles:
        for i, cyc in enumerate(cycles.cycles, 1):
            lines.append(f"**Cycle {i}** (size {cyc.size})")
            for f in cyc.files:
                lines.append(f"- `{f}`")
            lines.append("")
    else:
        lines.append("_No cycles found._")
    lines.append("")

    summary: dict[str, Any] = {
        "headline": headline,
        "complexity": {
            "total": cx.total_callables,
            "average": cx.average,
            "high": cx.high,
            "extreme": cx.extreme,
            "hotspot_count": len(cx.hotspots),
            "hotspots": [
                {
                    "qname": h.qualified_name,
                    "rel_path": h.rel_path,
                    "lineno": h.lineno,
                    "complexity": h.complexity,
                    "line_count": h.line_count,
                }
                for h in cx.hotspots
            ],
        },
        "long_functions": {
            "total": lf.total_callables,
            "over_threshold": lf.over_threshold,
            "average_lines": lf.average,
            "functions": [
                {
                    "qname": fn.qualified_name,
                    "rel_path": fn.rel_path,
                    "lineno": fn.lineno,
                    "line_count": fn.line_count,
                    "complexity": fn.complexity,
                }
                for fn in lf.functions
            ],
        },
        "god_files": [
            {
                "path": g.rel_path,
                "score": g.score,
                "symbols": g.symbol_count,
                "loc": g.total_loc,
                "fan_in": g.fan_in,
            }
            for g in god.files
        ],
        "fan_out": {
            "count": len(fan_out.files),
            "threshold": fan_out_threshold,
            "files": [
                {
                    "path": fo.rel_path,
                    "fan_out": fo.fan_out,
                    "imports_total": fo.imports_total,
                    "imports_resolved": fo.imports_resolved,
                    "risk": fo.risk,
                }
                for fo in fan_out.files
            ],
        },
        "dead_code": {
            "count": len(dead.dead),
            "symbols": [
                {
                    "qname": d.qualified_name,
                    "rel_path": d.rel_path,
                    "lineno": d.lineno,
                    "kind": d.kind,
                }
                for d in dead.dead[:top_n]
            ],
        },
        "coverage": {
            "test_entry_points": coverage.test_entry_points,
            "reached_count": coverage.reached_count,
            "total_public_symbols": coverage.total_public_symbols,
            "coverage_pct": coverage.coverage_pct,
            "min_complexity": coverage_min_complexity,
            "gap_count": len(coverage.gaps),
            "gaps": [
                {
                    "qname": g.qualified_name,
                    "rel_path": g.rel_path,
                    "lineno": g.lineno,
                    "kind": g.kind,
                    "complexity": g.complexity,
                    "line_count": g.line_count,
                    "caller_count": g.caller_count,
                }
                for g in coverage.gaps
            ],
        },
        "unused_imports": {
            "count": len(unused.unused),
            "top_files": [
                {"path": p, "count": n} for p, n in top_unused_files
            ],
        },
        "cycles": {
            "count": len(cycles.cycles),
            "groups": [
                {"size": c.size, "files": list(c.files)}
                for c in cycles.cycles
            ],
        },
        # Back-compat scalar aliases (older v0.3 consumers).
        "dead_code_count": len(dead.dead),
        "unused_import_count": len(unused.unused),
        "cycle_count": len(cycles.cycles),
    }

    drift_md = ""
    drift = compute_drift(baseline, summary)
    if drift.has_baseline:
        drift_md = render_drift_markdown(drift)
        if drift_md:
            lines.append(drift_md)
        summary["drift"] = {
            "regression_count": drift.regression_count,
            "improvement_count": drift.improvement_count,
            "scalars": [
                {
                    "name": s.name,
                    "baseline": s.baseline,
                    "current": s.current,
                    "delta": s.delta,
                    "direction": s.direction,
                }
                for s in drift.scalars
            ],
            "sets": [
                {
                    "name": sd.name,
                    "regressions": sd.regressions,
                    "improvements": sd.improvements,
                    "unchanged": sd.unchanged,
                    "baseline_size": sd.baseline_size,
                    "current_size": sd.current_size,
                }
                for sd in drift.sets
            ],
        }

    return HealthReport(
        repo_path=str(repo_path),
        markdown="\n".join(lines),
        summary=summary,
    )
