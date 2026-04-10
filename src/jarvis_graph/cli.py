"""jarvis-graph CLI: index / query / context / impact / find_path / detect_changes
plus the v0.2 + v0.3 health-check engines, v0.5 baseline drift, v0.6 fan-out,
v0.7 call-chain finder, v0.8 test-coverage gaps, and v0.9 coverage in
health_report + drift.

Usage:
    jarvis-graph index <repo>            # incremental
    jarvis-graph index <repo> --full     # wipe + rebuild
    jarvis-graph query <repo> "<question>" [--limit N] [--match-all]
    jarvis-graph context <repo> <symbol-or-file>
    jarvis-graph impact  <repo> <symbol-or-file>
    jarvis-graph find_path <repo> <source> <target> [--max-depth N]
    jarvis-graph detect_changes <repo>
    jarvis-graph summary <repo>
    jarvis-graph find_dead_code       <repo>
    jarvis-graph find_coverage_gaps     <repo> [--min-complexity N] [--limit N]
    jarvis-graph generate_test_skeleton <repo> <symbol> [--out FILE] [--force]
    jarvis-graph find_unused_imports    <repo>
    jarvis-graph find_circular_deps   <repo>
    jarvis-graph find_complexity      <repo> [--threshold N] [--limit N]
    jarvis-graph find_long_functions  <repo> [--threshold N] [--limit N]
    jarvis-graph find_god_files       <repo> [--limit N]
    jarvis-graph find_high_fan_out    <repo> [--threshold N] [--limit N]
    jarvis-graph health_report        <repo> [--out FILE] [--baseline FILE]

All output is plain text by default; pass --json to any subcommand to get a
structured payload (handy for scripts and other agents). ANSI colour is on
when stdout is a TTY; disable with `--no-color` or `NO_COLOR=1`.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

# Windows consoles default to cp1252 — force UTF-8 with replacement so the CLI
# never crashes on a stray non-ASCII char in someone's docstring or file path.
if sys.platform == "win32":
    if isinstance(sys.stdout, io.TextIOWrapper):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# --- ANSI colour helpers --------------------------------------------------
# Honour NO_COLOR (https://no-color.org/) and skip colour when not a TTY,
# unless --color is explicitly forced. Each helper is a no-op when colour
# is disabled, so the formatting strings stay clean.

class _Style:
    enabled: bool = False

    @classmethod
    def configure(cls, mode: str) -> None:
        if mode == "always":
            cls.enabled = True
            return
        if mode == "never":
            cls.enabled = False
            return
        # "auto"
        if os.environ.get("NO_COLOR"):
            cls.enabled = False
            return
        cls.enabled = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _Style.enabled:
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def bold(t: str) -> str:    return _c("1", t)
def dim(t: str) -> str:     return _c("2", t)
def red(t: str) -> str:     return _c("31", t)
def green(t: str) -> str:   return _c("32", t)
def yellow(t: str) -> str:  return _c("33", t)
def blue(t: str) -> str:    return _c("34", t)
def magenta(t: str) -> str: return _c("35", t)
def cyan(t: str) -> str:    return _c("36", t)


_RISK_COLOR = {"low": green, "medium": yellow, "high": red}
_KIND_COLOR = {
    "function": cyan,
    "method":   cyan,
    "class":    magenta,
    "constant": yellow,
    "module":   dim,
    "file":     blue,
}


def _kind(s: str) -> str:
    fn = _KIND_COLOR.get(s)
    return fn(s) if fn else s


def _path(s: str) -> str:
    return blue(s)

from jarvis_graph import __version__
from jarvis_graph.change_detector import detect_changes
from jarvis_graph.circular_deps_engine import find_circular_deps
from jarvis_graph.complexity_engine import find_complexity
from jarvis_graph.context_engine import context as run_context
from jarvis_graph.coverage_gap_engine import find_coverage_gaps
from jarvis_graph.dead_code_engine import find_dead_code
from jarvis_graph.fan_out_engine import find_high_fan_out
from jarvis_graph.find_path_engine import find_path as run_find_path
from jarvis_graph.god_files_engine import find_god_files
from jarvis_graph.health_report_engine import health_report as run_health_report
from jarvis_graph.impact_engine import impact as run_impact
from jarvis_graph.indexer import index_repo
from jarvis_graph.long_functions_engine import find_long_functions
from jarvis_graph.query_engine import query as run_query
from jarvis_graph.refactor_priority_engine import find_refactor_priority
from jarvis_graph.repo_summary import summarize
from jarvis_graph.test_skeleton_engine import (
    SkeletonError,
    generate_test_skeleton,
    write_skeleton,
)
from jarvis_graph.unused_imports_engine import find_unused_imports


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _cmd_index(args) -> int:
    repo = Path(args.repo)
    parallel: bool | None
    if args.no_parallel:
        parallel = False
    elif args.parallel:
        parallel = True
    else:
        parallel = None  # auto
    report = index_repo(
        repo,
        full=args.full,
        parallel=parallel,
        max_workers=args.workers,
    )
    if args.json:
        _print_json(asdict(report))
        return 0
    print(f"jarvis-graph index ({'full' if args.full else 'incremental'})")
    print(f"  repo:              {repo.resolve()}")
    print(f"  files seen:        {report.files_seen}")
    print(f"  files indexed:     {report.files_indexed}")
    print(f"  skipped unchanged: {report.files_skipped_unchanged}")
    print(f"  files removed:     {report.files_removed}")
    print(f"  parse errors:      {report.files_with_errors}")
    print(f"  symbols:           {report.symbols_total}")
    print(f"  imports:           {report.imports_total}")
    print(f"  calls:             {report.calls_total}")
    print(f"  elapsed:           {report.elapsed_seconds}s")
    return 0


def _cmd_query(args) -> int:
    repo = Path(args.repo)
    hits = run_query(repo, args.question, limit=args.limit, match_all=args.match_all)
    if args.json:
        _print_json([asdict(h) for h in hits])
        return 0
    if not hits:
        print(dim("(no hits)"))
        return 0
    print(bold(f"{len(hits)} hit(s) for: {args.question}"))
    for h in hits:
        loc = f"{h.rel_path}:{h.lineno}" if h.lineno else h.rel_path
        qn = f" ({dim(h.qualified_name)})" if h.qualified_name and h.qualified_name != h.name else ""
        print(
            f"  [{bold(str(h.score).rjust(3))}] "
            f"{_kind(h.kind):<8} {bold(h.name)}{qn}  -> {_path(loc)}"
        )
        if h.snippet:
            print(f"        {dim(h.snippet)}")
    return 0


def _cmd_context(args) -> int:
    repo = Path(args.repo)
    res = run_context(repo, args.target)
    if args.json:
        _print_json(asdict(res))
        return 0
    if res.kind == "not_found":
        print(f"(no symbol or file matching: {args.target})")
        return 1

    print(f"context: {args.target} ({res.kind})")
    print(f"  file:        {res.rel_path}")
    if res.qualified_name:
        print(f"  qname:       {res.qualified_name}")
    if res.signature:
        print(f"  signature:   {res.signature}")
    if res.docstring:
        first = res.docstring.strip().splitlines()[0]
        print(f"  doc:         {first}")
    if res.role_note:
        print(f"  role:        {res.role_note}")
    if res.imports_in:
        print(f"  imported by: {len(res.imports_in)} file(s)")
        for p in res.imports_in[:10]:
            print(f"    - {p}")
        if len(res.imports_in) > 10:
            print(f"    ... +{len(res.imports_in) - 10} more")
    if res.imports_out:
        print(f"  imports:     {len(res.imports_out)} statement(s)")
        for line in res.imports_out[:10]:
            print(f"    - {line}")
        if len(res.imports_out) > 10:
            print(f"    ... +{len(res.imports_out) - 10} more")
    if res.callers:
        print(f"  callers:     {len(res.callers)}")
        for q, p, ln in res.callers[:10]:
            print(f"    - {q}  ({p}:{ln})")
        if len(res.callers) > 10:
            print(f"    ... +{len(res.callers) - 10} more")
    if res.callees:
        print(f"  callees:     {len(res.callees)}")
        for name, resolved in res.callees[:10]:
            tail = f" -> {resolved}" if resolved else ""
            print(f"    - {name}{tail}")
        if len(res.callees) > 10:
            print(f"    ... +{len(res.callees) - 10} more")
    if res.siblings:
        print(f"  siblings:    {len(res.siblings)}")
        for name, qn, ln in res.siblings[:10]:
            print(f"    - {name}  (line {ln})")
        if len(res.siblings) > 10:
            print(f"    ... +{len(res.siblings) - 10} more")
    return 0


def _cmd_impact(args) -> int:
    repo = Path(args.repo)
    res = run_impact(repo, args.target)
    if args.json:
        _print_json(asdict(res))
        return 0
    if res.kind == "not_found":
        print(f"(no symbol or file matching: {args.target})")
        return 1

    risk_painter = _RISK_COLOR.get(res.risk, dim)
    print(
        f"{bold('impact:')} {args.target} "
        f"({_kind(res.kind)})  risk={risk_painter(res.risk.upper())}"
    )
    print(f"  file:        {_path(res.rel_path)}")
    if res.qualified_name:
        print(f"  qname:       {dim(res.qualified_name)}")
    print(f"  direct callers:   {bold(str(len(res.direct_callers)))}")
    for q, p, ln in res.direct_callers[:15]:
        print(f"    - {q}  ({_path(f'{p}:{ln}')})")
    if len(res.direct_callers) > 15:
        print(dim(f"    ... +{len(res.direct_callers) - 15} more"))
    print(f"  direct importers: {bold(str(len(res.direct_importers)))}")
    for p in res.direct_importers[:15]:
        print(f"    - {_path(p)}")
    if len(res.direct_importers) > 15:
        print(dim(f"    ... +{len(res.direct_importers) - 15} more"))
    print(f"  second-order:     {bold(str(len(res.second_order)))}")
    for s in res.second_order[:15]:
        print(f"    - {s}")
    if len(res.second_order) > 15:
        print(dim(f"    ... +{len(res.second_order) - 15} more"))
    print("  why:")
    for reason in res.why:
        print(f"    - {dim(reason)}")
    return 0


def _cmd_find_path(args) -> int:
    repo = Path(args.repo)
    res = run_find_path(repo, args.source, args.target, max_depth=args.max_depth)
    if args.json:
        _print_json(asdict(res))
        return 0
    if not res.found:
        print(bold(f"find_path: {args.source} → {args.target}"))
        if res.source_qname:
            print(f"  source: {dim(res.source_qname)}")
        if res.target_qname:
            print(f"  target: {dim(res.target_qname)}")
        print(red(f"  no path: {res.note}"))
        return 1
    print(
        bold(f"find_path: {args.source} → {args.target}")
        + f"  ({res.depth}-step chain, {res.nodes_explored} nodes explored)"
    )
    print(f"  source: {dim(res.source_qname or args.source)}")
    print(f"  target: {dim(res.target_qname or args.target)}")
    print()
    for i, step in enumerate(res.steps):
        loc = _path(f"{step.rel_path}:{step.lineno}")
        prefix = "    " if i == 0 else "  → "
        print(f"  {prefix}{bold(step.qualified_name)}  ({loc})")
    return 0


def _cmd_detect_changes(args) -> int:
    repo = Path(args.repo)
    rep = detect_changes(repo)
    if args.json:
        _print_json(asdict(rep))
        return 0
    print("detect_changes")
    print(f"  total on disk: {rep.total_on_disk}")
    print(f"  total indexed: {rep.total_in_index}")
    print(f"  unchanged:     {rep.unchanged_count}")
    print(f"  added:         {len(rep.added)}")
    for p in rep.added[:20]:
        print(f"    + {p}")
    if len(rep.added) > 20:
        print(f"    ... +{len(rep.added) - 20} more")
    print(f"  modified:      {len(rep.modified)}")
    for p in rep.modified[:20]:
        print(f"    ~ {p}")
    if len(rep.modified) > 20:
        print(f"    ... +{len(rep.modified) - 20} more")
    print(f"  removed:       {len(rep.removed)}")
    for p in rep.removed[:20]:
        print(f"    - {p}")
    if len(rep.removed) > 20:
        print(f"    ... +{len(rep.removed) - 20} more")
    print(f"  recommendation: {rep.recommendation}")
    print(f"  reason:         {rep.reason}")
    return 0


def _cmd_find_dead_code(args) -> int:
    repo = Path(args.repo)
    rep = find_dead_code(repo)
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"find_dead_code: {repo.resolve()}"))
    print(
        dim(
            f"  checked={rep.total_checked} "
            f"excluded(dunder={rep.excluded_dunder} private={rep.excluded_private} "
            f"entrypoint={rep.excluded_entrypoint} test={rep.excluded_test} "
            f"textual={rep.excluded_textual})"
        )
    )
    paint = red if rep.dead else green
    print(f"  dead candidates: {paint(str(len(rep.dead)))}")
    for d in rep.dead[: args.limit]:
        print(
            f"    - {_kind(d.kind):<8} {bold(d.qualified_name)}  "
            f"({_path(f'{d.rel_path}:{d.lineno}')})"
        )
    if len(rep.dead) > args.limit:
        print(dim(f"    ... +{len(rep.dead) - args.limit} more"))
    return 0


def _cmd_find_coverage_gaps(args) -> int:
    repo = Path(args.repo)
    rep = find_coverage_gaps(
        repo,
        limit=args.limit,
        min_complexity=args.min_complexity,
    )
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"find_coverage_gaps: {repo.resolve()}"))
    if rep.test_entry_points == 0:
        print(red(f"  {rep.note}"))
        return 1
    print(
        dim(
            f"  test entry points: {rep.test_entry_points}  "
            f"reached symbols: {rep.reached_count}  "
            f"public pool: {rep.total_public_symbols}"
        )
    )
    paint = green if rep.coverage_pct >= 80 else (yellow if rep.coverage_pct >= 50 else red)
    print(f"  coverage: {paint(f'{rep.coverage_pct}%')}")
    paint = red if rep.gaps else green
    print(f"  gaps shown: {paint(str(len(rep.gaps)))} (min_complexity={args.min_complexity})")
    for g in rep.gaps:
        print(
            f"    - {_kind(g.kind):<8} {bold(g.qualified_name)}  "
            f"cmplx={g.complexity} loc={g.line_count} callers={g.caller_count}  "
            f"({_path(f'{g.rel_path}:{g.lineno}')})"
        )
    return 0


def _cmd_generate_test_skeleton(args) -> int:
    repo = Path(args.repo)
    try:
        skel = generate_test_skeleton(repo, args.symbol)
    except SkeletonError as e:
        print(red(f"generate_test_skeleton: {e}"), file=sys.stderr)
        return 1
    if args.json:
        _print_json(asdict(skel))
        return 0
    if args.out:
        try:
            written = write_skeleton(skel, Path(args.out), force=args.force)
        except SkeletonError as e:
            print(red(f"generate_test_skeleton: {e}"), file=sys.stderr)
            return 1
        print(bold(f"generate_test_skeleton: {repo.resolve()}"))
        print(
            dim(
                f"  target: {skel.symbol_kind} {skel.symbol_qname}  "
                f"(import: {skel.target_module}.{skel.target_name})"
            )
        )
        print(f"  written to {_path(str(written))}")
        return 0
    # No --out: print the skeleton to stdout so the user can pipe it.
    print(bold(f"generate_test_skeleton: {skel.symbol_qname}"))
    print(dim(f"  kind={skel.symbol_kind}  module={skel.target_module}"))
    print(dim(f"  suggested filename: {skel.suggested_filename}"))
    print()
    print(skel.body)
    return 0


def _cmd_find_unused_imports(args) -> int:
    repo = Path(args.repo)
    rep = find_unused_imports(repo)
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"find_unused_imports: {repo.resolve()}"))
    print(dim(f"  total imports: {rep.total_imports}"))
    paint = red if rep.unused else green
    print(f"  unused:        {paint(str(len(rep.unused)))}")
    for u in rep.unused[: args.limit]:
        if u.imported_name:
            stmt = f"from {u.imported_module} import {u.imported_name}"
            if u.alias:
                stmt += f" as {u.alias}"
        else:
            stmt = f"import {u.imported_module}"
            if u.alias:
                stmt += f" as {u.alias}"
        print(f"    - {_path(f'{u.rel_path}:{u.lineno}')}  {stmt}")
    if len(rep.unused) > args.limit:
        print(dim(f"    ... +{len(rep.unused) - args.limit} more"))
    return 0


def _cmd_find_circular_deps(args) -> int:
    repo = Path(args.repo)
    rep = find_circular_deps(repo)
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"find_circular_deps: {repo.resolve()}"))
    print(dim(f"  files: {rep.total_files}  edges: {rep.total_edges}"))
    paint = red if rep.cycles else green
    print(f"  cycles found: {paint(str(len(rep.cycles)))}")
    for i, c in enumerate(rep.cycles[: args.limit], 1):
        print(f"  [{bold(str(i))}] size={c.size}")
        for f in c.files:
            print(f"      - {_path(f)}")
    if len(rep.cycles) > args.limit:
        print(dim(f"    ... +{len(rep.cycles) - args.limit} more"))
    return 0


def _cmd_find_complexity(args) -> int:
    repo = Path(args.repo)
    rep = find_complexity(repo, threshold=args.threshold, limit=args.limit)
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"find_complexity: {repo.resolve()}"))
    print(
        dim(
            f"  callables={rep.total_callables} "
            f"avg={rep.average} "
            f"high(11-20)={rep.high} extreme(>20)={rep.extreme}  "
            f"threshold={args.threshold}"
        )
    )
    paint = red if rep.hotspots else green
    print(f"  hotspots: {paint(str(len(rep.hotspots)))}")
    for h in rep.hotspots:
        risk_paint = _RISK_COLOR.get(h.risk, dim)
        print(
            f"    [{risk_paint(str(h.complexity).rjust(3))}] "
            f"{_kind(h.kind):<8} {bold(h.qualified_name)}  "
            f"({_path(f'{h.rel_path}:{h.lineno}')}, {h.line_count} lines)"
        )
    return 0


def _cmd_find_long_functions(args) -> int:
    repo = Path(args.repo)
    rep = find_long_functions(repo, threshold=args.threshold, limit=args.limit)
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"find_long_functions: {repo.resolve()}"))
    print(
        dim(
            f"  callables={rep.total_callables} "
            f"avg={rep.average} lines  "
            f"over_threshold({args.threshold})={rep.over_threshold}"
        )
    )
    paint = red if rep.functions else green
    print(f"  long functions: {paint(str(len(rep.functions)))}")
    for fn in rep.functions:
        print(
            f"    [{bold(str(fn.line_count).rjust(4))}L cx={fn.complexity:>3}] "
            f"{_kind(fn.kind):<8} {bold(fn.qualified_name)}  "
            f"({_path(f'{fn.rel_path}:{fn.lineno}')})"
        )
    return 0


def _cmd_find_god_files(args) -> int:
    repo = Path(args.repo)
    rep = find_god_files(repo, limit=args.limit)
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"find_god_files: {repo.resolve()}"))
    print(dim(f"  total files: {rep.total_files}"))
    print(f"  god files: {bold(str(len(rep.files)))}")
    for gf in rep.files:
        score_paint = red if gf.score >= 0.5 else (yellow if gf.score >= 0.25 else dim)
        print(
            f"    [{score_paint(f'{gf.score:.3f}')}] "
            f"sym={gf.symbol_count:>3} loc={gf.total_loc:>4} "
            f"in={gf.fan_in:>3}  {_path(gf.rel_path)}"
        )
    return 0


def _cmd_find_high_fan_out(args) -> int:
    repo = Path(args.repo)
    rep = find_high_fan_out(repo, threshold=args.threshold, limit=args.limit)
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"find_high_fan_out: {repo.resolve()}"))
    print(dim(
        f"  total files: {rep.total_files}  threshold: {rep.threshold}"
    ))
    paint = red if rep.files else green
    print(f"  high-fan-out files: {paint(str(len(rep.files)))}")
    for ff in rep.files:
        risk_paint = _RISK_COLOR.get(ff.risk, dim)
        pct = f"{ff.fan_out_pct * 100:.1f}%"
        print(
            f"    [{risk_paint(str(ff.fan_out).rjust(3))}] "
            f"({pct.rjust(6)}) imports_total={ff.imports_total:>3}  "
            f"{_path(ff.rel_path)}"
        )
    return 0


def _cmd_refactor_priority(args) -> int:
    repo = Path(args.repo)
    rep = find_refactor_priority(
        repo,
        min_priority=args.min_priority,
        limit=args.limit,
        include_classes=args.include_classes,
    )
    if args.json:
        _print_json(asdict(rep))
        return 0
    print(bold(f"refactor_priority: {repo.resolve()}"))
    print(dim(
        f"  evaluated: {rep.total_evaluated}  "
        f"skipped_test: {rep.skipped_test}  "
        f"skipped_trivial: {rep.skipped_trivial}  "
        f"threshold: {rep.threshold}"
    ))
    if rep.note:
        print(dim(f"  note: {rep.note}"))
    paint = red if rep.candidates else green
    print(f"  candidates: {paint(str(len(rep.candidates)))}")
    if not rep.candidates:
        return 0
    print()
    print(dim("  rank  score   cplx  lines  callers  symbol"))
    for i, c in enumerate(rep.candidates, 1):
        tags = ", ".join(c.reasons)
        untested_tag = red("UNTEST") if c.is_untested else green(" OK ")
        print(
            f"  {i:>4}  {c.priority:>5.1f}  "
            f"{c.complexity:>4}  {c.line_count:>5}  "
            f"{c.caller_count:>7}  "
            f"{untested_tag}  "
            f"{c.qualified_name}  "
            f"{dim('[' + _path(c.rel_path) + ':' + str(c.lineno) + ']')}"
        )
        print(f"         {dim(tags)}")
    return 0


def _load_baseline_summary(path: Path) -> dict | None:
    """Load a baseline JSON snapshot.

    Accepts both shapes that the CLI emits:
      - the full `health_report --json` output: `{"repo_path": ..., "summary": {...}}`
      - the bare summary payload itself
    Returns None if the file is empty/missing the expected structure.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"error: cannot read baseline {path}: {exc}", file=sys.stderr)
        return None
    if isinstance(raw, dict) and "summary" in raw and isinstance(raw["summary"], dict):
        return raw["summary"]
    if isinstance(raw, dict) and "headline" in raw:
        return raw
    print(f"error: baseline {path} does not look like a health_report snapshot", file=sys.stderr)
    return None


def _cmd_health_report(args) -> int:
    repo = Path(args.repo)
    baseline_summary: dict | None = None
    if args.baseline:
        baseline_summary = _load_baseline_summary(Path(args.baseline))
        if baseline_summary is None:
            return 2
    rep = run_health_report(
        repo,
        complexity_threshold=args.complexity_threshold,
        long_threshold=args.long_threshold,
        top_n=args.top_n,
        fan_out_threshold=args.fan_out_threshold,
        coverage_min_complexity=args.coverage_min_complexity,
        baseline=baseline_summary,
    )
    # Save snapshot to disk if requested. Done before --json/--out so a single
    # invocation can write a baseline AND emit a report in the same pass.
    if args.save_baseline:
        snap_path = Path(args.save_baseline)
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_payload = {"repo_path": rep.repo_path, "summary": rep.summary}
        snap_path.write_text(
            json.dumps(snap_payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    if args.json:
        _print_json({"repo_path": rep.repo_path, "summary": rep.summary})
        return 0
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rep.markdown, encoding="utf-8")
        print(bold(f"health_report written to {_path(str(out_path))}"))
        s = rep.summary
        print(dim(
            f"  files={s['headline']['files']} "
            f"complexity_hotspots={s['complexity']['hotspot_count']} "
            f"fan_out_hubs={s['fan_out']['count']} "
            f"dead_code={s['dead_code']['count']} "
            f"unused_imports={s['unused_imports']['count']} "
            f"cycles={s['cycles']['count']}"
        ))
        if "drift" in s:
            d = s["drift"]
            paint = red if d["regression_count"] > 0 else green
            print(dim(
                f"  drift: {paint(str(d['regression_count']) + ' regression(s)')}, "
                f"{green(str(d['improvement_count']) + ' improvement(s)')}"
            ))
        if args.save_baseline:
            print(dim(f"  snapshot saved to {_path(args.save_baseline)}"))
        return 0
    print(rep.markdown)
    if args.save_baseline:
        print(dim(f"\n[snapshot saved to {args.save_baseline}]"), file=sys.stderr)
    return 0


def _cmd_summary(args) -> int:
    repo = Path(args.repo)
    s = summarize(repo)
    if args.json:
        _print_json(asdict(s))
        return 0
    print(f"summary: {s.repo_path}")
    print(f"  files:        {s.files} ({s.parse_errors} with parse errors)")
    print(f"  symbols:      {s.symbols}  "
          f"(fn={s.functions} cls={s.classes} m={s.methods} const={s.constants})")
    print(f"  imports:      {s.imports}")
    print(f"  call edges:   {s.calls}")
    print("  most imported files:")
    for path, n in s.most_imported_files[:10]:
        print(f"    - {n:>4}  {path}")
    print("  largest files by symbol count:")
    for path, n in s.largest_files_by_symbols[:10]:
        print(f"    - {n:>4}  {path}")
    if s.likely_entrypoints:
        print("  likely entrypoints:")
        for p in s.likely_entrypoints:
            print(f"    - {p}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jarvis-graph",
        description="Lightweight local code-intelligence index (stdlib only).",
    )
    p.add_argument("--version", action="version", version=f"jarvis-graph {__version__}")
    p.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="ANSI colour: auto (TTY), always, never. Honours $NO_COLOR.",
    )
    p.add_argument(
        "--no-color",
        dest="color",
        action="store_const",
        const="never",
        help="alias for --color never",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="Index (or re-index) a repo")
    pi.add_argument("repo")
    pi.add_argument("--full", action="store_true", help="wipe and rebuild from scratch")
    pi.add_argument(
        "--parallel",
        action="store_true",
        help="force parallel parsing (default: auto for repos with >=50 files)",
    )
    pi.add_argument(
        "--no-parallel",
        action="store_true",
        help="force sequential parsing",
    )
    pi.add_argument(
        "--workers",
        type=int,
        default=None,
        help="worker count for parallel mode (default: min(8, cpu-1))",
    )
    pi.add_argument("--json", action="store_true")
    pi.set_defaults(func=_cmd_index)

    pq = sub.add_parser("query", help="Locate where a concept lives")
    pq.add_argument("repo")
    pq.add_argument("question")
    pq.add_argument("--limit", type=int, default=20)
    pq.add_argument(
        "--match-all", "--and",
        dest="match_all",
        action="store_true",
        help="strict AND across keywords; default is soft AND with multi-token bonus",
    )
    pq.add_argument("--json", action="store_true")
    pq.set_defaults(func=_cmd_query)

    pc = sub.add_parser("context", help="Explain a symbol or file's role")
    pc.add_argument("repo")
    pc.add_argument("target")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=_cmd_context)

    pim = sub.add_parser("impact", help="Estimate blast radius of a change")
    pim.add_argument("repo")
    pim.add_argument("target")
    pim.add_argument("--json", action="store_true")
    pim.set_defaults(func=_cmd_impact)

    pfp = sub.add_parser(
        "find_path", help="Find a shortest resolved call chain between two symbols"
    )
    pfp.add_argument("repo")
    pfp.add_argument("source", help="symbol the chain starts from")
    pfp.add_argument("target", help="symbol the chain should reach")
    pfp.add_argument(
        "--max-depth",
        type=int,
        default=8,
        help="BFS depth cap (default 8)",
    )
    pfp.add_argument("--json", action="store_true")
    pfp.set_defaults(func=_cmd_find_path)

    pd = sub.add_parser("detect_changes", help="Diff disk vs index")
    pd.add_argument("repo")
    pd.add_argument("--json", action="store_true")
    pd.set_defaults(func=_cmd_detect_changes)

    ps = sub.add_parser("summary", help="Per-repo deterministic summary")
    ps.add_argument("repo")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=_cmd_summary)

    pdc = sub.add_parser("find_dead_code", help="List functions/classes/methods with no callers")
    pdc.add_argument("repo")
    pdc.add_argument("--limit", type=int, default=50)
    pdc.add_argument("--json", action="store_true")
    pdc.set_defaults(func=_cmd_find_dead_code)

    pcg = sub.add_parser(
        "find_coverage_gaps",
        help="List public symbols that no test entry point can reach via the call graph",
    )
    pcg.add_argument("repo")
    pcg.add_argument(
        "--limit",
        type=int,
        default=50,
        help="cap on returned gaps after sorting (default 50)",
    )
    pcg.add_argument(
        "--min-complexity",
        type=int,
        default=1,
        help="only flag symbols with cyclomatic complexity at least this (default 1, raise to 5+ to focus)",
    )
    pcg.add_argument("--json", action="store_true")
    pcg.set_defaults(func=_cmd_find_coverage_gaps)

    pgts = sub.add_parser(
        "generate_test_skeleton",
        help="Emit a unittest.TestCase skeleton for the given symbol (closes the find_coverage_gaps loop)",
    )
    pgts.add_argument("repo")
    pgts.add_argument(
        "symbol",
        help="qualified_name (preferred) or bare name of a function/method/class",
    )
    pgts.add_argument(
        "--out",
        help="write the generated skeleton to this path; default prints to stdout",
    )
    pgts.add_argument(
        "--force",
        action="store_true",
        help="allow --out to overwrite an existing file",
    )
    pgts.add_argument("--json", action="store_true")
    pgts.set_defaults(func=_cmd_generate_test_skeleton)

    pui = sub.add_parser("find_unused_imports", help="List unused import statements")
    pui.add_argument("repo")
    pui.add_argument("--limit", type=int, default=50)
    pui.add_argument("--json", action="store_true")
    pui.set_defaults(func=_cmd_find_unused_imports)

    pcd = sub.add_parser("find_circular_deps", help="Detect import cycles")
    pcd.add_argument("repo")
    pcd.add_argument("--limit", type=int, default=20)
    pcd.add_argument("--json", action="store_true")
    pcd.set_defaults(func=_cmd_find_circular_deps)

    pcx = sub.add_parser("find_complexity", help="List functions/methods with high cyclomatic complexity")
    pcx.add_argument("repo")
    pcx.add_argument("--threshold", type=int, default=10)
    pcx.add_argument("--limit", type=int, default=30)
    pcx.add_argument("--json", action="store_true")
    pcx.set_defaults(func=_cmd_find_complexity)

    plf = sub.add_parser("find_long_functions", help="List functions/methods over a line-count threshold")
    plf.add_argument("repo")
    plf.add_argument("--threshold", type=int, default=50)
    plf.add_argument("--limit", type=int, default=30)
    plf.add_argument("--json", action="store_true")
    plf.set_defaults(func=_cmd_find_long_functions)

    pgf = sub.add_parser("find_god_files", help="Rank files by symbols × LOC × fan-in")
    pgf.add_argument("repo")
    pgf.add_argument("--limit", type=int, default=20)
    pgf.add_argument("--json", action="store_true")
    pgf.set_defaults(func=_cmd_find_god_files)

    pfo = sub.add_parser(
        "find_high_fan_out",
        help="List files that import many other in-repo files (coupling risk)",
    )
    pfo.add_argument("repo")
    pfo.add_argument(
        "--threshold",
        type=int,
        default=5,
        help="minimum distinct in-repo files imported (default 5)",
    )
    pfo.add_argument("--limit", type=int, default=20)
    pfo.add_argument("--json", action="store_true")
    pfo.set_defaults(func=_cmd_find_high_fan_out)

    prp = sub.add_parser(
        "refactor_priority",
        help="Rank functions by a composite score combining complexity, length, coverage, and caller count",
    )
    prp.add_argument("repo")
    prp.add_argument(
        "--min-priority",
        type=float,
        default=50.0,
        help="only return rows with composite score >= this (default 50)",
    )
    prp.add_argument("--limit", type=int, default=30)
    prp.add_argument(
        "--include-classes",
        action="store_true",
        help="also score `kind=class` symbols (classes have no complexity in this index — off by default)",
    )
    prp.add_argument("--json", action="store_true")
    prp.set_defaults(func=_cmd_refactor_priority)

    phr = sub.add_parser("health_report", help="Generate a Markdown report combining every health signal")
    phr.add_argument("repo")
    phr.add_argument("--complexity-threshold", type=int, default=10)
    phr.add_argument("--long-threshold", type=int, default=50)
    phr.add_argument("--top-n", type=int, default=15)
    phr.add_argument(
        "--fan-out-threshold",
        type=int,
        default=8,
        help="files with this many in-repo imports or more get flagged in section 5",
    )
    phr.add_argument(
        "--coverage-min-complexity",
        type=int,
        default=5,
        help="minimum cyclomatic complexity for a coverage gap to surface in section 7",
    )
    phr.add_argument(
        "--out",
        type=str,
        default=None,
        help="write the markdown to this path instead of stdout",
    )
    phr.add_argument(
        "--baseline",
        type=str,
        default=None,
        help="load a previous --json snapshot from FILE and add a drift section",
    )
    phr.add_argument(
        "--save-baseline",
        type=str,
        default=None,
        help="write a JSON snapshot of this report to FILE for use with --baseline",
    )
    phr.add_argument("--json", action="store_true")
    phr.set_defaults(func=_cmd_health_report)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _Style.configure(getattr(args, "color", "auto"))
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
