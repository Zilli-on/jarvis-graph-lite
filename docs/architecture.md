# Architecture

## Goals

1. Run on Windows 10 with stdlib only.
2. One SQLite file per indexed repo, nothing global.
3. Index a 600-file Python tree in seconds, on a cold cache, on a 9-year-old i5.
4. Be **honest** about what it can and can't resolve. No hallucinated graph edges.

## Module map

```
src/jarvis_graph/
  __init__.py              # version
  __main__.py              # `python -m jarvis_graph`
  cli.py                   # argparse → engine functions, ANSI color helpers
  config.py                # .jarvis_graph/config.json
  db.py                    # sqlite3 connection (FK on, WAL, NORMAL sync) + migrations
  schema.py                # DDL + indexes (4 tables + meta) — schema v2
  hashing.py               # 64 KiB chunked sha256
  models.py                # ParsedFile / ParsedSymbol / ParsedImport / ParsedCall
  parser_python.py         # ast.parse → ParsedFile  (incl. local-type rewrite + cyclomatic)
  indexer.py               # walk → parse → diff → upsert → resolve (parallel-aware)
  parallel.py              # ProcessPoolExecutor wrapper for parse fan-out
  gitignore.py             # GitignoreMatcher + GitignoreStack (stdlib regex)
  ranker.py                # score_symbol_name / score_qname / score_path / score_docstring
  query_engine.py          # locate (LIKE pool → ranker → AND/recency → top-N)
  context_engine.py        # explain (resolve → callers/callees/siblings/role_note)
  impact_engine.py         # blast radius (direct + second-order + risk score)
  change_detector.py       # disk vs db diff (added / modified / removed)
  repo_summary.py          # deterministic snapshot
  dead_code_engine.py      # find_dead_code (kind+filter+textual call+global token scan)
  unused_imports_engine.py # find_unused_imports (token scan minus import lines)
  circular_deps_engine.py  # find_circular_deps (Tarjan SCC on resolved imports)
  complexity_engine.py     # find_complexity (McCabe per callable, bucketed)
  long_functions_engine.py # find_long_functions (line_count over threshold)
  god_files_engine.py      # find_god_files (composite of symbols × LOC × fan-in)
  fan_out_engine.py        # find_high_fan_out (distinct in-repo imports per file)
  health_report_engine.py  # health_report (Markdown aggregator over all engines)
  drift_engine.py          # compute_drift / render_drift_markdown (v0.5)
  utils.py                 # iter_python_files, to_module_path, repo_data_dir, ...
  logging_utils.py         # one-line append-only operations log
```

Each engine is a single function plus a result dataclass. The CLI never calls a private helper from another module — it goes through the engine's public entry point. This keeps the seam between "logic" and "presentation" sharp.

## Storage

```
<repo>/.jarvis_graph/
  config.json
  index.db          # 4 tables + meta
  logs/operations.log
  summaries/repo_summary.json
```

WAL is enabled and `synchronous=NORMAL` so concurrent reads (e.g. CLI + an editor running a query) don't deadlock and writes still survive a clean shutdown.

## Schema

```
meta(key, value)

file(file_id, rel_path UNIQUE, abs_path, module_path, sha256, size_bytes,
     mtime, indexed_at, parse_error)

symbol(symbol_id, file_id → file CASCADE, name, qualified_name, kind,
       parent_qname, lineno, end_lineno, col, docstring, signature, is_private,
       complexity, line_count)        -- columns added in schema v2

import_edge(edge_id, file_id → file CASCADE, imported_module, imported_name,
            alias, lineno, resolved_file_id → file SET NULL)

call_edge(edge_id, caller_symbol_id → symbol CASCADE, callee_name,
          resolved_symbol_id → symbol SET NULL, lineno)
```

A few indexes on `file.module_path`, `symbol.name`, `symbol.qualified_name`, `import_edge.imported_module`, `call_edge.callee_name`, plus the obvious join columns (`call_edge.resolved_symbol_id`, `import_edge.resolved_file_id`) and `symbol.complexity` for the v0.3 hotspot scan. That's it. No FTS, no virtual tables — they were measured and didn't beat plain LIKE on this data size.

### Schema migrations

`schema.py` carries a `SCHEMA_VERSION` constant. On every connection `db.connect` reads `meta.schema_version`; if it's lower than the current value, `_migrate(conn, current)` runs the forward-only steps in order (currently: v1→v2 adds `complexity` / `line_count` columns and the `symbol(complexity)` index). Fresh databases skip the version check but still run `_migrate(conn, 0)` so the post-DDL index always gets created. Migrations never drop columns and never modify data, only schema.

`kind` includes a synthetic `"module"` row per file, with `qualified_name = module_path` and `name = "<module>"`. Without it, calls made at module scope (script bodies, `if __name__ == "__main__"` blocks) have nowhere to attach, and impact analysis silently undercounts.

## Indexing pipeline

`indexer.index_repo(repo, full=False, parallel=None, max_workers=None)`:

1. Snapshot `(rel_path → (file_id, sha256))` from the existing index.
2. Walk `*.py` files via `iter_python_files` (skips `.git`, `.venv`, `__pycache__`, `.jarvis_graph`, dotted dirs, **plus anything matched by a layered `.gitignore`** — see *Walker* below).
3. Decide whether to fan parsing out into a `ProcessPoolExecutor`:
   - `parallel=True` forces it on, `parallel=False` forces it off
   - `parallel=None` (default) → on iff `len(files) >= 50` (`should_parallelize`)
4. Parallel pass (when enabled): `parallel.parse_in_parallel` submits one `_parse_worker` per file. Workers re-import `parser_python` and run `parse_python_file` in isolation, returning fully-populated `ParsedFile`s. `as_completed` streams results back to the main process where the SQLite writer applies them via `_ingest_parsed`. The pool initializer rebuilds the parent `sys.path` so editable installs and test runners that prepend `src/` keep working.
5. Sequential safety net: any file the pool dropped silently (broken pool, pickling failure, …) is parsed sequentially right after — the writer is the same `_ingest_parsed` helper, so totals always reconcile.
6. For each parsed file: if its sha256 matches the existing row and `--full` is off → **skip**; else delete the old file row (cascades children) and insert the new one.
7. Files in the index but not on disk → delete.
8. `_resolve_imports`:
   - exact match `module_path = imported_module`
   - suffix fallback `module_path LIKE '%.X'` **only** when exactly one candidate exists
9. `_resolve_calls`:
   - same-file undotted (cheap, safe)
   - cross-module via resolved import edges (`from X import bar; bar()`)
   - dotted last-segment via imports (`mod.bar.baz()` → `baz` in some imported file)
   - **m1**: dotted `Cls.method` where `Cls` was imported via `import_edge` — bound to a method symbol with a matching `parent_qname` suffix in the imported file
   - **m2**: dotted `Cls.method` where `Cls` is defined in the caller's own file (covers the parser's `self.method` rewrite path)
10. `config.save` + `logs/operations.log` append.

The parser also performs a small **local-type rewrite** before emitting calls, so this pattern resolves correctly:

```python
svc = GreetingService()   # collected: svc → GreetingService
svc.greet()               # rewritten:   GreetingService.greet
```

The rewrite is gated on the callee starting with an uppercase letter (PEP-8 class convention). This avoids `conn = sqlite3.connect()` causing `conn.execute(...)` to be rewritten to `connect.execute`. Inside class bodies, `self.method(...)` is rewritten to `EnclosingClass.method` using the AST's surrounding `ClassDef`.

The whole pipeline runs in a single SQLite connection and a single transaction at the end.

## Engines

### query

Tokenize, drop stopwords, take up to 5 tokens. Run two LIKE-based candidate pools (symbols + files), score them in Python with `ranker.score_*`, sort by `(-score, rel_path, lineno)`, return the top N. The hot path is `LIKE` against indexed columns; total candidates are bounded by `LIMIT 500` / `LIMIT 200` to keep memory tiny.

Two extra signals shape the final score:

- **soft AND multiplier**: `coverage = 0.5 + 0.5 * (matched_tokens / n_tokens)`. A result that hits every query token gets the full score; a partial hit is dampened. `--and` (alias `--match-all`) turns this into a strict AND filter, dropping any candidate that doesn't hit every token.
- **recency boost**: `mtime` is min-max normalized across the *whole repo* (not just candidates) and scaled to a small additive bonus (`_RECENCY_BONUS_MAX = 8`). The most-recently-touched file gets the full +8; files at the oldest mtime get 0. The whole-repo normalization keeps the boost stable when the candidate pool changes between queries.

### context

Resolve target → fetch the relevant rows (callers, callees, siblings, imports in/out) → build a `ContextResult`. The "role note" is a hand-rolled heuristic over the file path (e.g. `/db/`, `/cli/`, `/services/`) plus a fan-in count.

### impact

For a symbol: `direct_callers` from `call_edge.resolved_symbol_id`, `direct_importers` from the file's import-in set, `second_order` from callers-of-callers (one hop). For a file: aggregate the callers across every symbol defined in the file, but **filter out same-file callers** so internal coupling doesn't inflate the count. Risk is bucketed (`low / medium / high`) on `direct + second_order` totals.

`impact` accepts dotted names (`Class.method`, `module.Class.method`). Resolution falls through `qualified_name` → `qualified_name LIKE '%.target'` → `parent_qname` suffix match for `Class.method` patterns where the class lives in another module.

This is intentionally a heuristic — call resolution is best-effort, so the numbers are a guide, not a proof.

### find_dead_code

A symbol is flagged as dead only if all of these hold:

- kind ∈ {function, method, class}
- name is not private (no leading underscore)
- name is not a dunder
- name is not in `{main, run, cli, app}` (well-known entrypoints)
- name does not start with `test_` or `Test`
- no `call_edge.callee_name` matches the name as the last segment
- AND the name does not appear as an identifier or string literal in any *other* file in the repo

The final cross-file token check is the expensive one — it builds `dict[file_id, set[token]]` lazily, only after the cheap filters reduce the candidate set, then checks for the name in any file other than the symbol's defining file. This catches dispatch-dict registrations like `tools["bash_exec"] = bash_exec` that pure call-graph analysis can't see, eliminating ~140 false positives on JARVIS.

False positives are the cardinal sin here; false negatives are tolerated.

### find_unused_imports

For each `import_edge`, compute the local binding name (`alias` if present, else `imported_name` for `from X import Y`, else the first segment of `imported_module` for `import X`). Then scan the file's source with a token regex, **after stripping import lines** (including multi-line `from X import (a, b, c)` paren-wrapped forms). If the binding name is not in the resulting token set and the module is not in `_SIDE_EFFECT_MODULES` (`__future__`, `warnings`, `logging.config`, …), flag it.

The strip-imports step is critical: without it, every `from typing import Dict` would look used because `Dict` appears on its own definition line.

### find_circular_deps

Build a directed graph from `import_edge.resolved_file_id`. Run iterative Tarjan's SCC. Report any SCC of size ≥ 2, plus single-node SCCs that have a self-loop. Sorted by size descending. The graph only contains *resolved* edges, so unresolved imports (stdlib, third-party) can't manufacture phantom cycles.

### find_complexity

Per-symbol McCabe cyclomatic is materialised at parse time (`parser_python._complexity`) and stored on `symbol.complexity`. The engine is therefore a single SQL select with a threshold filter and a `name NOT LIKE '\_\_%' ESCAPE '\\'` exclusion for dunder methods; no AST work happens at query time. Hotspots are bucketed `low (1-5)` / `medium (6-10)` / `high (11-20)` / `extreme (21+)` and sorted by complexity descending.

### find_long_functions

Same shape as `find_complexity` but sorts by `line_count`, also stored on the symbol row at parse time. Exclusions: dunders, `<module>` synthetic rows, anything below the threshold (default 50 lines).

### find_god_files

Composite score in a single SQL with a LEFT JOIN and a fan-in subquery counting resolved imports targeting each file. Columns: symbol count, max line offset (used as a LOC proxy), and resolved fan-in. Each component is min-max normalised across the candidate set, then averaged: `score = (sym_n/max_sym + loc/max_loc + fan_in/max_fan) / 3`. Files with zero symbols (empty `__init__.py`) are dropped before scoring.

### find_high_fan_out

Symmetric counterpart to `find_god_files`. Where god files measures fan-**in** (how many other files import this one), `find_high_fan_out` measures fan-**out** (how many distinct in-repo files this one imports). High fan-out flags client hubs: every change in any of their many dependencies has a non-zero chance of breaking them first. The query is one SQL select against `import_edge`:

```sql
COUNT(DISTINCT CASE
    WHEN ie.resolved_file_id IS NOT NULL
     AND ie.resolved_file_id != f.file_id
    THEN ie.resolved_file_id
END) AS fan_out
```

The `DISTINCT` collapses duplicate imports of the same file (e.g. `from x import a; from x import b` should still count as one fan-out edge), and the self-comparison drops file's-own-resolved-imports. Two side-counters travel along: `imports_total` (every recorded edge, incl. unresolved stdlib) and `imports_resolved` (subset that resolved to a file id). `fan_out_pct = fan_out / total_files`, so risk buckets generalise across repos of any size (`high` = ≥20% or ≥30 absolute, `medium` = ≥8% or ≥12, else `low`).

### health_report

Calls every other engine in turn (complexity, long functions, god files, **fan-out**, dead code, unused imports, circular deps), assembles their reports into an 8-section Markdown document, and computes a "summary" payload for JSON consumers (so other tools don't need to parse the Markdown). Top-N defaults to 15. Output goes to a file via `--out` or to stdout.

When `--baseline FILE` is supplied, the report loads a previous JSON snapshot and adds a section 9 ("Drift since baseline") via `drift_engine`. The summary payload also gains a `drift` key with `regression_count`, `improvement_count`, and structured per-metric details. The baseline loader accepts both shapes the CLI emits: the full `{"repo_path": ..., "summary": {...}}` envelope and the bare summary payload.

### drift_engine

A pure function pair (`compute_drift` + `render_drift_markdown`) that compares two `health_report` summary snapshots. It does not touch the database — that keeps it deterministic and trivially testable from JSON.

Two flavours of drift:

- **Scalar drift** — numeric metrics (`hotspot_count`, `dead_code.count`, `cycles.count`, resolution percentages, …) compared as `(baseline, current, delta)`. Each metric is tagged `worsened` (delta moved in the bad direction), `improved` (moved in the good direction), `unchanged`, or `neutral` (informational totals like file/symbol counts where no direction is meaningful). The direction comes from a small per-metric table inside the engine, not from inference.
- **Set drift** — ranked lists (top hotspots, top god files, dead-code symbols, cycles) compared as sets keyed by a stable id (`qualified_name` for symbols, `rel_path` for files, sorted member tuple for cycles). The result is `regressions` (newly in the list), `improvements` (left the list), and `unchanged` (count of overlap). The engine deliberately skips a set diff if either side is missing the path entirely — that prevents an older baseline from a tool version that didn't track these lists from showing every current entry as a regression.

To make set drift work, `health_report` had to enrich its summary payload to include the actual top-N entries with stable ids (not just counts). The old `dead_code_count` / `unused_import_count` / `cycle_count` scalar fields are still emitted as back-compat aliases.

### detect_changes

Walk the disk, hash each `*.py`, compare against `file.sha256`. Group into `added / modified / removed / unchanged`. Recommend `incremental` for small diffs, `full` if the diff is bigger than half the index, `no_changes` if everything matches.

### summary

A flat heuristic snapshot — file/symbol counts by kind, top 15 most-imported files, top 15 largest files, likely entrypoints (`__main__.py`, `cli.py`, `manage.py`, …). Written to `summaries/repo_summary.json`.

## Walker (gitignore-aware)

`utils.iter_python_files(repo_path, respect_gitignore=True)` is the single source of truth for "what files are in this repo". It descends recursively (so layered `.gitignore`s can be pushed/popped naturally) and applies three filters in order:

1. **Hardcoded `SKIP_DIRS`** — `.git`, `.venv`, `__pycache__`, `.jarvis_graph`, `node_modules`, `build`, `dist`, IDE caches, …
2. **Dotted directories** — anything starting with `.` is dropped.
3. **Layered gitignore** — every `.gitignore` encountered along the descent path contributes its rules to a `GitignoreStack`. Inner rules can re-include via `!pattern`. The stack is anchored per directory: a rule like `workspace/generated_projects/` in the root `.gitignore` resolves against root-relative paths, while a `*.tmp` in a subdirectory only affects that subdirectory's descendants.

`gitignore.GitignoreMatcher` compiles each pattern into a Python `re.Pattern` covering the subset of git's wildmatch semantics that matters for Python repos: `*` (no `/`), `?`, `**` (cross-segment), `[abc]` character classes, anchored vs unanchored, `dir_only` (trailing `/`), and `!negation`. Unsupported patterns are silently dropped — the walker is best-effort, not a full git implementation.

The motivating case: JARVIS has 200 auto-generated `*.py` files under `workspace/generated_projects/`, including a single 131k-line file that dominated the LOC ranking before the walker landed. Adding `workspace/generated_projects/` to the root `.gitignore` shrinks the visible tree from 610 → 407 files (-33%) and removes the noisiest entries from every health-check engine.

## Parallel parsing

`parallel.parse_in_parallel(files, max_workers=None)` is a thin wrapper around `concurrent.futures.ProcessPoolExecutor`. Each worker calls `parser_python.parse_python_file(Path(abs), Path(rel))` and returns the resulting `ParsedFile`. Choices:

- **Worker count**: `min(8, os.cpu_count() - 1)` by default. Capped at 8 because beyond that the SQLite writer in the main process becomes the bottleneck and extra workers just steal CPU from it.
- **Threshold**: `should_parallelize(file_count)` returns `True` only at ≥50 files. Below that, the spawn cost (~150 ms on Windows) eats the entire savings.
- **Initializer**: workers re-import `jarvis_graph.parser_python` on first call, which means `sys.path` must contain the package. The pool initializer rebuilds the parent's `sys.path` so test runners that prepend `src/` (and editable installs that add the egg link) keep working without env-var fiddling.
- **Failure mode**: per-file exceptions in workers are caught and dropped from the result stream — the main loop's "sequential safety net" pass picks them up afterwards. A completely broken pool (e.g. cannot spawn) returns no results, which the safety net also handles correctly.

On JARVIS (407 files, 4.3k symbols, 29k calls): sequential = 5.04 s, parallel ×3 workers = 3.83 s — a ~24% wall-clock improvement. The gain scales with file count; for `tests/sample_repo` (5 files) parallel is *slower* due to spawn overhead, hence the 50-file threshold.

## What's deliberately not here

See [`deferred-features.md`](deferred-features.md) for the explicit list. Headlines:
- no embeddings, no semantic search
- no syntax tree caching, no incremental AST diff
- no daemon, no watcher, no IDE integration
- no graph UI
- no hooks into the host machine — every byte lives under the target repo or the tool's own folder
