# Deferred features

Things that were considered and **explicitly left out**, with the trigger that would justify adding them later.

## Promoted out of this file in v0.2

- **Method-level call resolution** (parser local-type rewrite + indexer m1/m2 passes). Implemented because it was blocking accurate impact analysis on class methods on JARVIS.
- **Dead-code detection** (`find_dead_code`). Implemented with a two-stage textual filter to keep false positives near zero.
- **Unused-import detection** (`find_unused_imports`). Implemented with import-line-stripped token scanning to keep type-only imports out of the false-positive bucket.
- **Circular-dependency detection** (`find_circular_deps`). Tarjan's SCC over resolved import edges.
- **Strict AND query mode + recency boost** (`query --and`). Implemented because lexical recall on multi-word questions was too noisy.
- **ANSI color output**. Implemented because the CLI's primary consumer is now humans skimming impact/dead-code reports.

## Promoted out of this file in v0.3

- **Per-callable cyclomatic complexity** (`find_complexity`). Materialised on the symbol row at parse time so the engine is a single SQL select. Bucketed `low/medium/high/extreme`. Surfaced `lyrc-local/amv_engine.render_amv` (cyclomatic 184) on JARVIS.
- **Long-function detection** (`find_long_functions`). Same shape — `line_count` is a parse-time column. Default threshold 50 lines.
- **God-file scoring** (`find_god_files`). Composite normalised score over symbol count × LOC × resolved fan-in. Top entry on JARVIS: `agents/agent_tools.py` (40 fan-in, 37 symbols).
- **Aggregated health report** (`health_report`). One Markdown file with seven sections covering every health-check engine plus a headline summary. Ships with a structured `summary` payload for JSON consumers.
- **Forward-only schema migrations** (`db._migrate`). Schema bumped to v2 to add `complexity` / `line_count` columns; old v1 databases migrate cleanly without a wipe.

## Promoted out of this file in v0.4

- **Gitignore-aware walker** (`utils.iter_python_files` + `gitignore.GitignoreStack`). Recursive descent with layered matchers; supports `**`, anchored vs unanchored, dir-only, negation, character classes. Cut JARVIS from 610 → 407 visible files by excluding `workspace/generated_projects/`.
- **Parallel parsing** (`parallel.parse_in_parallel`). `ProcessPoolExecutor` fan-out with a `min(8, cpu-1)` worker cap, a 50-file activation threshold, and a sequential safety net for any worker drops. Cut JARVIS full reindex from 5.04 s → 3.83 s (~24%).

## Still deferred

| Feature | Why deferred | Trigger to add |
|---|---|---|
| **Embedding-based semantic search** | Needs an ONNX/transformers runtime, big disk + RAM cost. The current LIKE+ranker pipeline already finds the right symbols on a 4 000-symbol repo. | Lexical recall noticeably misses real questions on a 10 k+ symbol repo. |
| **Newer Python syntax than the indexer's interpreter** | The indexer parses files using the `ast` module of the *running* interpreter. If the venv runs Python 3.11 but a target file uses 3.12-only syntax (e.g. backslash inside an f-string expression), parsing fails and the file is recorded with `parse_error` set. Workaround: run the indexer under at least the highest Python version used by any indexed repo. | A repo of mixed-version files where parse errors actually hide important call edges. |
| **Variable-level data flow** | We track *call edges*, not assignments or returns. "Where is `self.config` written?" is out of scope. | A concrete refactor task needs reaching-defs and the lexical query workflow can't do it. |
| **Full type inference** | We store the textual signature only — no annotations, no inferred types, no overload resolution. | Same as above — when the lexical/structural workflow stops being enough. |
| **Cross-language indexing** | Python only. JS/TS/Go callers are invisible. | A real cross-language refactor lands on the queue. |
| **Watch mode / daemon** | Hard constraint: no background processes on this machine. The CLI is fast enough that an explicit `index` step is fine. | Index latency on a real repo crosses 30 s and someone runs it ≥ 5×/day. |
| **IDE integration / LSP** | Out of scope for v1 — the CLI is the contract. | An editor workflow becomes the primary use case. |
| **Graph UI** | Adds a server, a frontend, and dependency tree we can't justify yet. The CLI's text + `--json` output is enough for an LLM agent to consume. | A human is regularly looking at the same set of edges and the text view is the bottleneck. |
| **Git history / blame integration** | `git log` and `git blame` already exist and are authoritative. Duplicating them inside this tool would only add staleness. | A workflow needs *historical* impact (e.g. "who has touched this symbol in the last 30 days") and shelling out to git is too slow. |
| **CallGraph for dynamic dispatch** | `getattr(self, name)()`, `dispatch[key]()`, decorator-rewritten methods → all unresolved. We don't pretend otherwise: unresolved calls are recorded but `resolved_symbol_id IS NULL`. | A specific class of dynamic dispatch is recurring and missing it costs real correctness. |
| **Per-symbol full-text snippet store** | We store the docstring, not the body. The CLI prints `file:line` so the editor handles the rest. | A workflow genuinely needs body-text grep and tooling around it has to live inside this index. |
| **Sub-file ranges in impact analysis** | Impact reports `file:line` for the call site, not which line of the *target* symbol someone is calling. | A workflow needs "if I change line 240, what breaks?" granularity. |
| **Multi-repo cross-references** | Each repo has its own `.jarvis_graph/`. A symbol in repo A calling repo B is invisible. | Multiple JARVIS-adjacent repos start sharing live code paths. |

The principle: **every feature must justify itself by an immediate, measurable use case on this machine**. When an entry above starts hurting, it gets promoted out of this file and into the actual codebase. Until then, every line saved is a line that doesn't have to be debugged.
