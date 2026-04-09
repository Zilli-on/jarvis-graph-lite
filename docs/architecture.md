# Architecture

## Goals

1. Run on Windows 10 with stdlib only.
2. One SQLite file per indexed repo, nothing global.
3. Index a 600-file Python tree in seconds, on a cold cache, on a 9-year-old i5.
4. Be **honest** about what it can and can't resolve. No hallucinated graph edges.

## Module map

```
src/jarvis_graph/
  __init__.py            # version
  __main__.py            # `python -m jarvis_graph`
  cli.py                 # argparse → engine functions
  config.py              # .jarvis_graph/config.json
  db.py                  # sqlite3 connection (FK on, WAL, NORMAL sync)
  schema.py              # DDL + indexes (4 tables + meta)
  hashing.py             # 64 KiB chunked sha256
  models.py              # ParsedFile / ParsedSymbol / ParsedImport / ParsedCall
  parser_python.py       # ast.parse → ParsedFile
  indexer.py             # walk → parse → diff → upsert → resolve
  ranker.py              # score_symbol_name / score_qname / score_path / score_docstring
  query_engine.py        # locate (LIKE pool → ranker → top-N)
  context_engine.py      # explain (resolve → callers/callees/siblings/role_note)
  impact_engine.py       # blast radius (direct + second-order + risk score)
  change_detector.py     # disk vs db diff (added / modified / removed)
  repo_summary.py        # deterministic snapshot
  utils.py               # iter_python_files, to_module_path, repo_data_dir, ...
  logging_utils.py       # one-line append-only operations log
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
7. `config.save` + `logs/operations.log` append.

The whole pipeline runs in a single SQLite connection and a single transaction at the end.

## Engines

### query

Tokenize, drop stopwords, take up to 5 tokens. Run two LIKE-based candidate pools (symbols + files), score them in Python with `ranker.score_*`, sort by `(-score, rel_path, lineno)`, return the top N. The hot path is `LIKE` against indexed columns; total candidates are bounded by `LIMIT 500` / `LIMIT 200` to keep memory tiny.

### context

Resolve target → fetch the relevant rows (callers, callees, siblings, imports in/out) → build a `ContextResult`. The "role note" is a hand-rolled heuristic over the file path (e.g. `/db/`, `/cli/`, `/services/`) plus a fan-in count.

### impact

For a symbol: `direct_callers` from `call_edge.resolved_symbol_id`, `direct_importers` from the file's import-in set, `second_order` from callers-of-callers (one hop). For a file: aggregate the callers across every symbol defined in the file, but **filter out same-file callers** so internal coupling doesn't inflate the count. Risk is bucketed (`low / medium / high`) on `direct + second_order` totals.

This is intentionally a heuristic — call resolution is best-effort, so the numbers are a guide, not a proof.

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
