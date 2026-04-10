# jarvis-graph-lite

A tiny **local** code-intelligence index for Python repos.
Stdlib only. No embeddings. No daemons. No external services.
Runs on Win10, Python 3.11, in seconds, on a 9-year-old i5 with 8 GB RAM.

It answers fourteen questions about a repo:

1. **`query`** — *where does this concept live?*  (with `--and` for strict AND across tokens, plus a recency boost)
2. **`context`** — *what is this symbol or file's role?*
3. **`impact`** — *what might break if I change this?*  (works on classes, methods, and `Class.method` dotted names)
4. **`find_path`** — *how does my code get from A to B?*  (BFS shortest call chain across resolved call edges — answers the question that `impact` leaves open: not "what does X reach" but "how do I reach Y from here")
5. **`detect_changes`** — *what's drifted since I last indexed?*
6. **`find_dead_code`** — *which functions/classes/methods are never referenced anywhere?*
7. **`find_coverage_gaps`** — *which public symbols are never reached from a test?*  (multi-source forward BFS from every test entry point — flags the high-complexity untested code first)
8. **`find_unused_imports`** — *which imports are never used in their file?*
9. **`find_circular_deps`** — *are there import cycles in the repo?*
10. **`find_complexity`** — *which functions are too tangled?* (McCabe cyclomatic, low → extreme buckets)
11. **`find_long_functions`** — *which functions are over the line-count threshold?*
12. **`find_god_files`** — *which files do too much?* (composite of symbol count × LOC × fan-in)
13. **`find_high_fan_out`** — *which files import too much of the rest of the repo?* (the symmetric counterpart to `find_god_files` — high fan-out flags client hubs that break first when their many dependencies move)
14. **`health_report`** — *one Markdown file aggregating all of the above. With `--baseline FILE` it diffs against a previous JSON snapshot and adds a "Drift since baseline" section.*

Plus a free helper: **`summary`** — a deterministic per-repo snapshot.

Output is colorized (kind, risk level, paths) when stdout is a TTY. Use `--color always|never` or `--no-color` to override; the `NO_COLOR` env var is also honored.

---

## Why this exists

Most "code intelligence" tools either embed everything (slow, hungry, fragile) or run as a daemon with a UI on top. For this machine, neither is acceptable. `jarvis-graph-lite` is the **smallest** thing that's still genuinely useful: a SQLite-backed AST index per repo, scored by hand-rolled lexical heuristics, with a single CLI.

If you want fancy ranking, semantic search, or a graph UI, see [`docs/deferred-features.md`](docs/deferred-features.md). Those features are **deliberately omitted** until they pull their weight.

---

## Install

Nothing to install — this is stdlib only.
You can run it from source via `python -m jarvis_graph ...`.

```bat
:: optional editable install
C:\JARVIS\.venv\Scripts\python.exe -m pip install -e C:\JARVIS\tools\jarvis-graph-lite
```

After install, the entry point `jarvis-graph` is on your PATH.

---

## Quick start

```bat
:: 1. index a repo
python -m jarvis_graph index C:\JARVIS

:: 2. find things
python -m jarvis_graph query    C:\JARVIS "voice recognition"
python -m jarvis_graph query    C:\JARVIS "telegram bot send" --and
python -m jarvis_graph context  C:\JARVIS handle_voice
python -m jarvis_graph impact   C:\JARVIS detect_backend
python -m jarvis_graph impact   C:\JARVIS GreetingService.greet
python -m jarvis_graph find_path C:\JARVIS jarvis_brain.main execute_tool
python -m jarvis_graph detect_changes C:\JARVIS
python -m jarvis_graph summary  C:\JARVIS

:: 3. find rot
python -m jarvis_graph find_dead_code       C:\JARVIS --limit 20
python -m jarvis_graph find_coverage_gaps   C:\JARVIS --min-complexity 10
python -m jarvis_graph find_unused_imports  C:\JARVIS --limit 20
python -m jarvis_graph find_circular_deps   C:\JARVIS

:: 4. find complexity / size / coupling hotspots
python -m jarvis_graph find_complexity     C:\JARVIS --threshold 10
python -m jarvis_graph find_long_functions C:\JARVIS --threshold 50
python -m jarvis_graph find_god_files      C:\JARVIS --limit 15
python -m jarvis_graph find_high_fan_out   C:\JARVIS --threshold 8

:: 5. one report to rule them all
python -m jarvis_graph health_report       C:\JARVIS --out HEALTH.md

:: 6. take a snapshot today, diff it tomorrow
python -m jarvis_graph health_report C:\JARVIS --save-baseline snap.json --out HEALTH.md
:: ... time passes, code changes ...
python -m jarvis_graph health_report C:\JARVIS --baseline snap.json --out HEALTH.md
```

Every command accepts `--json` for machine-readable output.

The convenience `.bat` wrappers in [`scripts/`](scripts/) take only the repo path as the first arg, so you can drop them on your taskbar.

---

## Where the data lives

`jarvis-graph-lite` writes **only** into the target repo, never into your home directory or anywhere global. Every indexed repo grows one folder:

```
<repo>/
└── .jarvis_graph/
    ├── config.json          # repo name, indexer version, last-indexed timestamp
    ├── index.db             # SQLite — the actual index
    ├── logs/operations.log  # one line per index pass
    └── summaries/           # repo_summary.json after `summary`
```

Add `.jarvis_graph/` to that repo's `.gitignore` if you don't want to commit it.

---

## What gets indexed

Per `*.py` file:

- **symbols** — top-level functions, classes, methods (one nesting level), `UPPER_CASE` constants, plus a synthetic `<module>` symbol per file so module-level scripts get a caller identity
- **imports** — `import X`, `import X as Y`, `from X import Y`, relative imports (`from .X import Y` is recorded with leading dot levels)
- **calls** — every `ast.Call` reachable from a function/method body or from module-level execution; the textual callee name is stored, then resolved to a real symbol id during a second pass

The parser performs a small amount of **local type tracking** so method calls on instance variables can be resolved:

- `svc = GreetingService()` followed by `svc.greet(...)` is rewritten to `GreetingService.greet` at parse time (only when the callee starts with an uppercase letter, to avoid garbage like `conn = sqlite3.connect()` rewriting `conn.execute` → `connect.execute`).
- `self.method(...)` inside a class body is rewritten to `ClassName.method` using the enclosing class context.

Resolution is best-effort and uses five heuristics, in order:

1. exact `module_path == imported_module`
2. suffix-match (`module_path LIKE '%.X'`) **only** when exactly one candidate exists — handles flat `sys.path` layouts like `JARVIS/`
3. for plain `from X import bar; bar()` calls, the call edge is bound to the symbol named `bar` in the resolved import target
4. **m1**: dotted `Cls.method` where `Cls` was imported via `import_edge` — bound to the matching method symbol in the imported file
5. **m2**: dotted `Cls.method` where `Cls` is defined in the caller's own file (covers the `self.method` rewrite path)

These heuristics are wrong sometimes — see `deferred-features.md` for what's deliberately not handled.

---

## CLI shape

```
jarvis-graph [--color auto|always|never] [--no-color] <subcommand> ...

  index               <repo> [--full] [--parallel|--no-parallel] [--workers N] [--json]
  query               <repo> "<question>" [--limit N] [--and] [--json]
  context             <repo> <symbol-or-file>          [--json]
  impact              <repo> <symbol-or-file>          [--json]
  find_path           <repo> <source-symbol> <target-symbol> [--max-depth N] [--json]
  detect_changes      <repo>                           [--json]
  summary             <repo>                           [--json]
  find_dead_code      <repo> [--limit N]               [--json]
  find_coverage_gaps  <repo> [--min-complexity N] [--limit N] [--json]
  find_unused_imports <repo> [--limit N]               [--json]
  find_circular_deps  <repo>                           [--json]
  find_complexity     <repo> [--threshold N] [--limit N] [--json]
  find_long_functions <repo> [--threshold N] [--limit N] [--json]
  find_god_files      <repo> [--limit N]               [--json]
  find_high_fan_out   <repo> [--threshold N] [--limit N] [--json]
  health_report       <repo> [--out FILE] [--top-n N] [--fan-out-threshold N] [--baseline FILE] [--save-baseline FILE]  [--json]
```

`<symbol-or-file>` resolves in this order: exact qualified name → qualified-name suffix (for dotted `Class.method`) → parent-qname suffix (for `Class.method` where `Class` is in another module) → exact symbol name → file path substring.

---

## Performance on this machine

Tested on Windows 10, i5-6600K, 8 GB DDR4, no SSD heroics:

| Repo                       | Files | Symbols | Calls  | Full reindex |
|----------------------------|------:|--------:|-------:|-------------:|
| `tests/sample`             |     5 |      18 |     14 |       <0.1 s |
| `C:\JARVIS\` (sequential)  |   407 |   ~4.3k |   ~29k |        5.0 s |
| `C:\JARVIS\` (parallel ×3) |   407 |   ~4.3k |   ~29k |        3.8 s |

Incremental reindex on the same JARVIS repo with no changes: <1 s (sha256 short-circuit). `find_dead_code` on JARVIS: ~2 s (per-file token scan is the dominant cost). `find_circular_deps`: <0.5 s (Tarjan's SCC on the import graph). `health_report`: ~6 s end-to-end (runs all six engines).

The walker honours `.gitignore` files at the repo root and any subdirectory, so generated junk (e.g. `workspace/generated_projects/`) doesn't pollute the index. Disable the layered matchers by passing `respect_gitignore=False` from a script — the CLI always honours them.

---

## Tests

Pure stdlib `unittest`. From the project root:

```bat
C:\JARVIS\.venv\Scripts\python.exe -m unittest discover -s tests
```

There's no pytest dependency on purpose — tests have to run with the same interpreter you ship with.

---

## Rollback

Everything this tool creates lives under either:

- `C:\JARVIS\tools\jarvis-graph-lite\` (the tool itself), or
- `<some-repo>\.jarvis_graph\` (per-repo data).

To uninstall completely: delete those two paths. There is no global state.
See [`docs/rollback.md`](docs/rollback.md) for the explicit checklist.

---

## Further reading

- [`docs/architecture.md`](docs/architecture.md) — how the index is shaped, why
- [`docs/deferred-features.md`](docs/deferred-features.md) — what was left out, and why
- [`docs/rollback.md`](docs/rollback.md) — how to remove every byte this tool wrote
