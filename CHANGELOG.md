# Changelog

All notable changes to jarvis-graph-lite.

## [0.12.4] — 2026-04-10

- Case-sensitive TODO/FIXME/HACK/BUG tag detection in `find_todo_comments`

## [0.12.3] — 2026-04-10

- **Critical bugfix**: `import X; X.fn()` call resolution was broken since v0.1
  - `_resolve_calls` path (a) used wrong `substr` formula, never matched plain-import callees
  - Impact: lyrc-local coverage 1.8% → 5.5%, JARVIS 3.4% → 5.3%
- 3 regression tests added

## [0.12.2] — 2026-04-10

- `# noqa: F401` support in `find_unused_imports`
- Multi-line import handling via `_logical_import_line`

## [0.12.1] — 2026-04-09

- XXX false-positive fix: format specifiers like `X.XXX` no longer flagged
- Tag regex requires whitespace/`#`/line-start before tag keyword

## [0.12.0] — 2026-04-09

- New engine: `find_todo_comments` (alias `ptc`)
- Comment extraction via stdlib `tokenize` (not regex) — skips strings/docstrings
- Risk formula: `tag_weight + complexity + (line_count * 0.1)`
- Tag weights: BUG=HACK=4, FIXME=3, TODO=XXX=2
- Risk buckets: critical >=20, high >=10, medium >=5, low <5

## [0.11.0] — 2026-04-09

- New engine: `refactor_priority` — composite scoring meta-engine
- Score: complexity + size + untested_penalty + caller_score
- Weight factor suppresses trivial helpers
- Pre-filters: test files, dunders, private `_*`, trivial functions

## [0.10.1] — 2026-04-09

- `generate_test_skeleton`: snake_case function names now render PascalCase test class

## [0.10.0] — 2026-04-09

- New engine: `generate_test_skeleton` — coverage gap → unittest stub
- Emits correct imports, `<Subject>Tests` class, `test_*_smoke` per public method
- `--force` to overwrite, `--out FILE` to save

## [0.9.2] — 2026-04-08

- Test suffix detection fix in `coverage_gap_engine` (both `Test*` and `*Tests`)

## [0.9.1] — 2026-04-08

- Dead-code false-positive fix: same-file dispatch dicts no longer flagged
- `<Subject>Tests` suffix detection added (not just `Test*` prefix)

## [0.9.0] — 2026-04-08

- `health_report` section 7: coverage gaps (headline + top-N table)
- Drift engine tracks coverage metrics (%, gap count, gap set diff)
- `--coverage-min-complexity N` flag (default 5)

## [0.8.0] — 2026-04-08

- New engine: `find_coverage_gaps` — static test reachability via multi-source BFS
- Flags high-complexity untested code first
- Detects `test_*` functions, `Test*` classes, setUp/tearDown as entry points

## [0.7.0] — 2026-04-07

- New engine: `find_path` — BFS shortest call-chain between two symbols
- Forward BFS with parent-map reconstruction, max_depth default 8

## [0.6.0] — 2026-04-07

- New engine: `find_high_fan_out` — which files import too many others
- `health_report` section 5: "Client hubs"
- Drift tracks `fan_out.count` and file list

## [0.5.1] — 2026-04-07

- `--save-baseline FILE` flag for `health_report`

## [0.5.0] — 2026-04-07

- Baseline drift tracking via `drift_engine.py`
- Compares two health_report snapshots (scalar vs set drift)
- `health_report --baseline FILE` appends "Drift since baseline" section

## [0.4.0] — 2026-04-06

- Gitignore-aware file walker (`GitignoreMatcher` + `GitignoreStack`)
- Parallel parsing via `ProcessPoolExecutor` (auto-scales, 50-file threshold)
- CLI flags: `--parallel`, `--no-parallel`, `--workers N`

## [0.3.0] — 2026-04-06

- New engines: `find_complexity`, `find_long_functions`, `find_god_files`
- `health_report` — 7-section aggregated Markdown report
- Schema v2 migration (adds complexity & line_count columns)

## [0.2.0] — 2026-04-05

- Class instantiation tracking
- 3 rot-detection engines: dead code, unused imports, circular deps
- Query AND mode + recency sorting
- ANSI color output

## [0.1.0] — 2026-04-04

- Initial release: local code-intelligence index for Python repos
- stdlib only, no external dependencies
- 4 core commands: query, context, impact, detect_changes
