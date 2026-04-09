"""jarvis-graph CLI: index / query / context / impact / detect_changes / summary.

Usage:
    jarvis-graph index <repo>            # incremental
    jarvis-graph index <repo> --full     # wipe + rebuild
    jarvis-graph query <repo> "<question>" [--limit N]
    jarvis-graph context <repo> <symbol-or-file>
    jarvis-graph impact <repo> <symbol-or-file>
    jarvis-graph detect_changes <repo>
    jarvis-graph summary <repo>

All output is plain text by default; pass --json to any subcommand to get
a structured payload (handy for scripts and other agents).
"""

from __future__ import annotations

import argparse
import io
import json
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

from jarvis_graph import __version__
from jarvis_graph.change_detector import detect_changes
from jarvis_graph.context_engine import context as run_context
from jarvis_graph.impact_engine import impact as run_impact
from jarvis_graph.indexer import index_repo
from jarvis_graph.query_engine import query as run_query
from jarvis_graph.repo_summary import summarize


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _cmd_index(args) -> int:
    repo = Path(args.repo)
    report = index_repo(repo, full=args.full)
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
    hits = run_query(repo, args.question, limit=args.limit)
    if args.json:
        _print_json([asdict(h) for h in hits])
        return 0
    if not hits:
        print("(no hits)")
        return 0
    print(f"{len(hits)} hit(s) for: {args.question}")
    for h in hits:
        loc = f"{h.rel_path}:{h.lineno}" if h.lineno else h.rel_path
        qn = f" ({h.qualified_name})" if h.qualified_name and h.qualified_name != h.name else ""
        print(f"  [{h.score:>3}] {h.kind:<8} {h.name}{qn}  -> {loc}")
        if h.snippet:
            print(f"        {h.snippet}")
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

    print(f"impact: {args.target} ({res.kind})  risk={res.risk.upper()}")
    print(f"  file:        {res.rel_path}")
    if res.qualified_name:
        print(f"  qname:       {res.qualified_name}")
    print(f"  direct callers:   {len(res.direct_callers)}")
    for q, p, ln in res.direct_callers[:15]:
        print(f"    - {q}  ({p}:{ln})")
    if len(res.direct_callers) > 15:
        print(f"    ... +{len(res.direct_callers) - 15} more")
    print(f"  direct importers: {len(res.direct_importers)}")
    for p in res.direct_importers[:15]:
        print(f"    - {p}")
    if len(res.direct_importers) > 15:
        print(f"    ... +{len(res.direct_importers) - 15} more")
    print(f"  second-order:     {len(res.second_order)}")
    for s in res.second_order[:15]:
        print(f"    - {s}")
    if len(res.second_order) > 15:
        print(f"    ... +{len(res.second_order) - 15} more")
    print("  why:")
    for reason in res.why:
        print(f"    - {reason}")
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
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="Index (or re-index) a repo")
    pi.add_argument("repo")
    pi.add_argument("--full", action="store_true", help="wipe and rebuild from scratch")
    pi.add_argument("--json", action="store_true")
    pi.set_defaults(func=_cmd_index)

    pq = sub.add_parser("query", help="Locate where a concept lives")
    pq.add_argument("repo")
    pq.add_argument("question")
    pq.add_argument("--limit", type=int, default=20)
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

    pd = sub.add_parser("detect_changes", help="Diff disk vs index")
    pd.add_argument("repo")
    pd.add_argument("--json", action="store_true")
    pd.set_defaults(func=_cmd_detect_changes)

    ps = sub.add_parser("summary", help="Per-repo deterministic summary")
    ps.add_argument("repo")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=_cmd_summary)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
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
