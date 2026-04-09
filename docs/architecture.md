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
  db.py                    # sqlite3 connection (FK on, WAL, NORMAL sync)
  schema.py                # DDL + indexes (4 tables + meta)
  hashing.py               # 64 KiB chunked sha256
  models.py                # ParsedFile / ParsedSymbol / ParsedImport / ParsedCall
  parser_python.py         # ast.parse → ParsedFile  (incl. local-type rewrite)
  indexer.py               # walk → parse → diff → upsert → resolve
  ranker.py                # score_symbol_name / score_qname / score_path / score_docstring
  query_engine.py          # locate (LIKE pool → ranker → AND/recency → top-N)
  context_engine.py        # explain (resolve → callers/callees/siblings/role_note)
  impact_engine.py         # blast radius (direct + second-order + risk score)
  change_detector.py       # disk vs db diff (added / modified / removed)
  repo_summary.py          # deterministic snapshot
  dead_code_engine.py      # find_dead_code (kind+filter+textual call+global token scan)
  unused_imports_engine.py # find_unused_imports (token scan minus import lines)
  circular_deps_engine.py  # find_circular_deps (Tarjan SCC on resolved imports)
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
       parent_qname, lineno, end_lineno, col, docstring, signature, is_private)

import_edge(edge_id, file_id → file CASCADE, imported_module, imported_name,
            alias, lineno, resolved_file_id → file SET NULL)

call_edge(edge_id, caller_symbol_id → symbol CASCADE, callee_name,
          resolved_symbol_id → symbol SET NULL, lineno)
```

A few indexes on `file.module_path`, `symbol.name`, `symbol.qualified_name`, `import_edge.imported_module`, `call_edge.callee_name`, plus the obvious join columns (`call_edge.resolved_symbol_id`, `import_edge.resolved_file_id`). That's it. No FTS, no virtual tables — they were measured and didn't beat plain LIKE on this data size.

`kind` includes a synthetic `"module"` row per file, with `qualified_name = module_path` and `name = "<module>"`. Without it, calls made at module scope (script bodies, `if __name__ == "__main__"` blocks) have nowhere to attach, and impact analysis silently undercounts.

## Indexing pipeline

`indexer.index_repo(repo, full=False)`:

1. Snapshot `(rel_path → (file_id, sha256))` from the existing index.
2. Walk `*.py` files via `iter_python_files` (skips `.git`, `.venv`, `__pycache__`, `.jarvis_graph`, dotted dirs).
3. For each file:
   - sha256 (64 KiB chunks)
   - if hash matches the existing row and `--full` is off → **skip**
   - else parse with `parse_python_file`, delete the old file row (cascades children), insert the new one
4. Files in the index but not on disk → delete.
5. `_resolve_imports`:
   - exact match `module_path = imported_module`
   - suffix fallback `module_path LIKE '%.X'` **only** when exactly one candidate exists
6. `_resolve_calls`:
   - same-file undotted (cheap, safe)
   - cross-module via resolved import edges (`from X import bar; bar()`)
   - dotted last-segment via imports (`mod.bar.baz()` → `baz` in some imported file)
   - **m1**: dotted `Cls.method` where `Cls` was imported via `import_edge` — bound to a method symbol with a matching `parent_qname` suffix in the imported file
   - **m2**: dotted `Cls.method` where `Cls` is defined in the caller's own file (covers the parser's `self.method` rewrite path)
7. `config.save` + `logs/operations.log` append.

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

### detect_changes

Walk the disk, hash each `*.py`, compare against `file.sha256`. Group into `added / modified / removed / unchanged`. Recommend `incremental` for small diffs, `full` if the diff is bigger than half the index, `no_changes` if everything matches.

### summary

A flat heuristic snapshot — file/symbol counts by kind, top 15 most-imported files, top 15 largest files, likely entrypoints (`__main__.py`, `cli.py`, `manage.py`, …). Written to `summaries/repo_summary.json`.

## What's deliberately not here

See [`deferred-features.md`](deferred-features.md) for the explicit list. Headlines:
- no embeddings, no semantic search
- no syntax tree caching, no incremental AST diff
- no daemon, no watcher, no IDE integration
- no graph UI
- no hooks into the host machine — every byte lives under the target repo or the tool's own folder
