# jarvis-graph-lite

A tiny **local** code-intelligence index for Python repos.
Stdlib only. No embeddings. No daemons. No external services.
Runs on Win10, Python 3.11, in seconds, on a 9-year-old i5 with 8 GB RAM.

It answers four questions about a repo:

1. **`query`** — *where does this concept live?*
2. **`context`** — *what is this symbol or file's role?*
3. **`impact`** — *what might break if I change this?*
4. **`detect_changes`** — *what's drifted since I last indexed?*

Plus a fifth, free helper: **`summary`** — a deterministic per-repo snapshot.

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
python -m jarvis_graph context  C:\JARVIS handle_voice
python -m jarvis_graph impact   C:\JARVIS detect_backend
python -m jarvis_graph detect_changes C:\JARVIS
python -m jarvis_graph summary  C:\JARVIS
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

Resolution is best-effort and uses three heuristics, in order:

1. exact `module_path == imported_module`
2. suffix-match (`module_path LIKE '%.X'`) **only** when exactly one candidate exists — handles flat `sys.path` layouts like `JARVIS/`
3. for plain `from X import bar; bar()` calls, the call edge is bound to the symbol named `bar` in the resolved import target

These heuristics are wrong sometimes — see `deferred-features.md` for what's deliberately not handled.

---

## CLI shape

```
jarvis-graph index            <repo> [--full] [--json]
jarvis-graph query            <repo> "<question>" [--limit N] [--json]
jarvis-graph context          <repo> <symbol-or-file>     [--json]
jarvis-graph impact           <repo> <symbol-or-file>     [--json]
jarvis-graph detect_changes   <repo>                      [--json]
jarvis-graph summary          <repo>                      [--json]
```

`<symbol-or-file>` resolves in this order: exact qualified name → exact symbol name → file path substring.

---

## Performance on this machine

Tested on Windows 10, i5-6600K, 8 GB DDR4, no SSD heroics:

| Repo            | Files | Symbols | Calls   | Full reindex |
|-----------------|------:|--------:|--------:|-------------:|
| `tests/sample`  |     5 |      18 |      14 |       <0.1 s |
| `C:\JARVIS\`    |   591 |   4 589 |  28 753 |        ~3 s  |

Incremental reindex on the same JARVIS repo with no changes: <1 s (sha256 short-circuit).

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
