# 001 — `import X; X.fn()` call resolution broken since v0.1

## Task

jarvis-graph-lite's core job is to build an accurate call
graph of a Python repo so downstream commands
(`find_coverage_gaps`, `impact`, `refactor_priority`, `find_path`)
can reason about which symbols reach which others. The
resolver in `_resolve_calls` is supposed to match every
call-site in the indexed code to the definition it targets.

## What failed

Plain-import calls — `import X; X.fn()` — silently never
matched their target. Path (a) of `_resolve_calls` used a
**wrong substring formula** and returned no match for the
most common import shape in the Python ecosystem. The bug
had been in the resolver since v0.1.

It was a **silent false-negative**: no exception, no warning,
no failing test. The only symptom was that downstream metrics
ran, completed, and reported values that were *too low* —
dead-code lists included functions that were in fact called,
coverage percentages were under-reported by factors of 2–3×.

Impact measured post-fix, same repos, same test suites:

| Repo | coverage_pct before | coverage_pct after |
|---|---|---|
| `lyrc-local` | 1.8 % | 5.5 % |
| `JARVIS`     | 3.4 % | 5.3 % |

## Root cause

Wrong `substr` formula in `_resolve_calls` path (a). The
string-slicing arithmetic that was supposed to isolate the
module-qualified callee name was off in a way that made the
slice match *nothing* rather than *something wrong*. Type
checks passed, lint passed, 274 tests passed — because **no
test exercised the plain-import shape end-to-end against a
known-good graph**.

The failure shape is the canonical one: a computation that
silently returns the empty set is indistinguishable from a
computation that correctly finds no matches, unless there's
an independent test asserting the expected non-empty set.

## Fix

Commit landed in v0.12.3 (2026-04-10, see `CHANGELOG.md`
under `## [0.12.3]`). Plus 3 regression tests that assert the
plain-import shape resolves to the expected target.

## Prevention rule

1. **For every resolver path, ship at least one end-to-end
   test that asserts a concrete non-empty result.** Unit
   tests that only verify "path A returns a list" are not
   enough — the list has to contain the known-good element.
2. **Coverage-metric swings > 2× between releases are a
   smell, not a victory.** When `coverage_pct` jumps that
   much, treat it as a resolver fix signal (or a resolver
   regression signal) and investigate before celebrating.
3. **Dogfood the tool on its own graph.** Running
   `jarvis-graph find_coverage_gaps` on this repo before
   each release would have caught the drift earlier — a
   public function that no test path reaches is the exact
   shape of the bug.

## See also

- `CHANGELOG.md` `[0.12.3]` entry
- `src/jarvis_graph/resolver.py` (or wherever `_resolve_calls`
  currently lives — use `jarvis-graph query _resolve_calls`)
- The 3 regression tests added in the same commit
- Workspace `failures/` for the meta-pattern: silent-
  false-negative bugs are the class most likely to ship
