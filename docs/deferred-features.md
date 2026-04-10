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

## Promoted out of this file in v0.5

- **Baseline drift on `health_report`** (`drift_engine.compute_drift`). Loads a previous `health_report --json` snapshot via `--baseline FILE` and renders a "Drift since baseline" section showing scalar drift (worsened / improved / neutral per metric) and set drift (newly-entered and newly-left lists for hotspots, dead code, cycles, …). The summary payload was enriched to carry the top-N lists with stable ids so set diff is keyed by `qualified_name` / `rel_path`. Skips set diff entirely when the baseline is missing a list — protects against false-positive regressions when the baseline came from an older tool version.

## Promoted out of this file in v0.6

- **High fan-out detection** (`find_high_fan_out`). Symmetric counterpart to `find_god_files`: instead of asking "which file is imported by too many others?", it asks "which file imports too many others?". Same SQL pattern (one select against `import_edge`) with `COUNT(DISTINCT CASE WHEN ie.resolved_file_id ...)` so duplicate imports of the same file collapse to a single fan-out edge. `health_report` gained a section 5 ("Client hubs") and `drift_engine` tracks `fan_out.count` as a scalar metric and the file list as a set diff. Top hub on JARVIS today: `tools/jarvis-graph-lite/src/jarvis_graph/cli.py` with fan_out=14 — legitimate (the CLI aggregates every engine).

## Promoted out of this file in v0.7

- **Shortest call-chain finder** (`find_path`). `impact` shows the blast radius if you change a symbol; the gap it left is "how does my code *get to* this expensive helper?". `find_path` is a forward BFS over `call_edge.resolved_symbol_id` with parent-map path reconstruction, bounded by `max_depth` (default 8). Reuses `_resolve_target` from `context_engine` so dotted names, bare names, and `Class.method` all work as endpoints. Validation on JARVIS: `jarvis_brain.main → execute_tool` resolves to a 4-step cross-module chain (`main → mode_auto → _execute_task → AutonomousAgent.run → execute_tool`) in ~44 nodes explored. Catches the bare-name pitfall in `_resolve_target`: when a file `entry.py` defines `def entry()`, the `<module>` synthetic row's `qualified_name` collides with the function — fixed with a fall-through that prefers a callable over a module-kind hit when both share the bare name.

## Promoted out of this file in v0.8

- **Test-coverage gaps** (`find_coverage_gaps`). Static reachability analysis — *not* runtime coverage. Multi-source forward BFS over `call_edge.resolved_symbol_id` with a single shared visited set, seeded from every test entry point (functions named `test_*` in test files, plus `setUp`/`tearDown` on `Test*` classes). Anything in the public-symbol pool that the BFS never visited is a coverage gap. Sorted by complexity desc → line_count desc so the most *risky* untested code surfaces first. `--min-complexity` lets you focus on the high-cyclomatic bucket. Validation on JARVIS today: 3.4% coverage (well-known) — top gap is `lyrc-local/amv_engine.render_amv` at cyclomatic 184, exactly the function we already wanted to test. Catches dynamic-dispatch blind spots like `StatusHandler.do_GET` (cmplx 91, callers=0 because `BaseHTTPRequestHandler` dispatches via reflection) — surfaced because static reachability is honest about what it can and can't see.

## Promoted out of this file in v0.9

- **Coverage gaps in `health_report` + drift**. Section 7 of the aggregated report now embeds the top untested risky symbols, sandwiched between dead code (6) and unused imports (8) — the natural place because dead code answers "never called from anywhere" and coverage gaps answer the more useful "never called *from a test*". `drift_engine` gained two scalar metrics (`coverage.coverage_pct` with direction `up`, and `coverage.gap_count` with direction `down`) and a set diff over `coverage.gaps` keyed by `qualified_name`. The whole drift section header bumped from `## 9` to `## 10` to make room. The CLI got a new `--coverage-min-complexity N` flag (default 5) so the section 7 table only shows the high-risk untested code and not every leaf helper. Validation on JARVIS today: drift correctly reports `coverage % 5.00 → 3.40 worsened (-1.60)` and lists the 5 newly-uncovered symbols by qname when fed a mutated baseline. The full test suite is now 120 passing.

## Promoted out of this file in v0.10

- **`generate_test_skeleton`**. Closes the loop from `find_coverage_gaps` ("which symbols have no test?") to "here's a unittest.TestCase to start filling in". Given a symbol qname, emit a module-level skeleton with `<Subject>Tests` class-naming convention, one `test_<method>_smoke` per public method, and `raise NotImplementedError` in every body so the stub fails until the user fills it in. The `--force` flag overwrites an existing file; without it, the command refuses to clobber. Includes a small snake→PascalCase helper so `snake_case` function names render as `SnakeCaseTests` (fixed in v0.10.1 after dogfooding surfaced a lowercase class name).

## Promoted out of this file in v0.11

- **`refactor_priority` meta-engine**. Until v0.11 the CLI returned a half-dozen answers to a half-dozen variants of "what's rotten here" — `find_complexity`, `find_long_functions`, `find_dead_code`, `find_coverage_gaps`, `find_god_files`, `find_high_fan_out`, … — and left the human to triangulate the true "fix this first" from six separate lists. `refactor_priority` is the glue: one SQL-heavy engine that cross-joins every signal into a single composite priority score (`complexity + line_count/10 + gap_boost + caller_count_factor`) with a `weight_factor` that actively *suppresses* trivial-but-widely-called helpers (the `len()`-style 2-line functions that would otherwise dominate because they're called everywhere). Pre-filters drop anything that's obviously not worth listing: test files, dunders (`__init__`, `__repr__`, …), private symbols (leading underscore), and trivial helpers (`complexity < 3 AND line_count < 20`). The result is a top-N list that answers the meta-question without the user having to run and diff six commands. Validation on JARVIS: top hit matches the dead-obvious top-offender (`lyrc-local/amv_engine.render_amv`) that every other engine was also flagging — the cross-signal consistency is the point. Test suite at v0.11: 200 passing.

## Promoted out of this file in v0.12

- **TODO/FIXME/HACK/BUG comment risk ranking** (`find_todo_comments`, CLI alias `ptc`). Every dev already has `grep`. What `grep` can't tell you is *which* TODO comments actually matter — a FIXME in a 2-line helper is a yak-shave, a FIXME in a 50-line cyclomatic-20 function is a time bomb. This engine cross-references each comment hit with the complexity + size of the enclosing symbol (found via `SELECT … FROM symbol WHERE file_id = ? AND lineno <= ? ORDER BY lineno DESC LIMIT 1` — innermost function for free, no recursive CTE). Risk = `tag_weight + complexity + (line_count * 0.1)`, bucketed `critical ≥ 20 / high ≥ 10 / medium ≥ 5 / low`. Comments are extracted via stdlib `tokenize` (not regex on raw text) so `x = "# TODO: ..."` is correctly NOT flagged as a comment, and TODOs inside docstrings stay invisible (they're STRING tokens, not COMMENT tokens — and docstrings are API documentation, not dev scratchpad). Test files are excluded by default. Tag weights: BUG=HACK=4, FIXME=3, TODO=XXX=2. Validation on JARVIS today: 422 files scanned, **4** real TODOs found (the tokenize accuracy is real — refactor_priority's top offender `lyrc-local/amv_engine.beat_match_edit` has a HACK comment ranked `critical` with risk 207.4, cross-validating both tools). Dogfooded via sparring_partner: the decision to build *this* feature (vs `find_god_classes`) was run through the devil's-advocate LLM first, which correctly flagged god-classes as redundant with refactor_priority. The new CLI subcommand is `ptc` (short for the semantic name) with `--limit`, `--min-risk`, `--include-tests` flags. The full test suite is now **252 passing** (+52).

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
