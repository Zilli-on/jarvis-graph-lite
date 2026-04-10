"""find_coverage_gaps: which public symbols are never reached from a test?

Static reachability over the call graph, NOT runtime coverage. Take every
function defined in a test file (name starts with `test_`, or method on a
class starting with `Test`) as a BFS source. Walk forward across resolved
`call_edge`s with a single shared visited set, marking every transitively
reachable symbol. Anything in the public-symbol pool that the BFS never
visited is a coverage gap.

The point is to surface untested business logic — particularly the high-
complexity, long-function variety where a missing test is most expensive.

Limitations (intentional, same family as the rest of jarvis-graph-lite):
  - Only static `call_edge`s count. Dynamic dispatch through getattr or
    a registry dict is invisible: a test that drives `dispatcher["fn"]()`
    doesn't mark `fn` as reached. Same caveat as `find_dead_code`.
  - "Public" means non-private, non-dunder, kind in {function, method,
    class}, file is NOT a test file. Constants and modules are excluded.
  - Test discovery is path/name based. Test runners that use other
    conventions (e.g. files in a sibling repo) are out of scope.

Result is sorted by complexity desc then line_count desc — the most
*risky* untested code first, which is what you actually want to fix.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


@dataclass
class CoverageGap:
    qualified_name: str
    name: str
    kind: str
    rel_path: str
    lineno: int
    complexity: int
    line_count: int
    caller_count: int  # how many distinct callers reference this symbol


@dataclass
class CoverageGapReport:
    repo_path: str
    test_entry_points: int = 0
    total_public_symbols: int = 0
    reached_count: int = 0
    coverage_pct: float = 0.0
    gaps: list[CoverageGap] = field(default_factory=list)
    note: str = ""


# A file is treated as a test file if its rel_path matches any of these
# patterns. The walker stores rel_path with forward slashes.
_TEST_FILE_PATTERNS = (
    "test_",        # test_foo.py at any level
    "_test.py",     # foo_test.py at any level (suffix check below)
)


def _is_test_path(rel_path: str) -> bool:
    p = rel_path.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    if base.startswith("test_") and base.endswith(".py"):
        return True
    if base.endswith("_test.py"):
        return True
    if "/tests/" in p or p.startswith("tests/"):
        return True
    return False


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and len(name) > 4


def _collect_test_entry_points(conn) -> tuple[set[int], set[int]]:
    """Return (test_file_ids, test_entry_symbol_ids).

    A test entry point is any function/method in a test file whose name
    starts with `test_`, OR a method on a class whose name starts with
    `Test` OR ends with `Tests` (the `<Subject>Tests` suffix convention,
    e.g. `RepoSummaryTests(unittest.TestCase)`). We also count
    `setUp`/`tearDown` because they pull dependencies into the reachable
    set even though they don't have `test_` prefix — skipping them would
    falsely flag fixtures as coverage gaps.
    """
    test_file_ids: set[int] = set()
    file_rows = conn.execute("SELECT file_id, rel_path FROM file").fetchall()
    for r in file_rows:
        if _is_test_path(r["rel_path"]):
            test_file_ids.add(int(r["file_id"]))

    if not test_file_ids:
        return test_file_ids, set()

    placeholders = ",".join("?" * len(test_file_ids))
    sym_rows = conn.execute(
        f"""
        SELECT symbol_id, name, kind, parent_qname
          FROM symbol
         WHERE file_id IN ({placeholders})
           AND kind IN ('function', 'method')
        """,
        tuple(test_file_ids),
    ).fetchall()

    entry_ids: set[int] = set()
    for r in sym_rows:
        name = r["name"]
        kind = r["kind"]
        parent = r["parent_qname"] or ""
        if name.startswith("test_"):
            entry_ids.add(int(r["symbol_id"]))
        elif kind == "method":
            cls = parent.rsplit(".", 1)[-1]
            if cls.startswith("Test") or cls.endswith("Tests"):
                # methods on TestFoo / FooTests classes — including
                # setUp/tearDown — pull fixtures into the reachable set
                entry_ids.add(int(r["symbol_id"]))
    return test_file_ids, entry_ids


def _multi_source_forward_bfs(conn, sources: set[int]) -> set[int]:
    """Walk forward through resolved call edges from every source. Single
    shared visited set so each symbol is expanded at most once even when
    many test entries reach it. No depth cap — we want the full transitive
    closure of "reachable from any test"."""
    visited: set[int] = set(sources)
    queue: deque[int] = deque(sources)
    while queue:
        cur = queue.popleft()
        rows = conn.execute(
            """
            SELECT DISTINCT resolved_symbol_id
              FROM call_edge
             WHERE caller_symbol_id = ?
               AND resolved_symbol_id IS NOT NULL
            """,
            (cur,),
        ).fetchall()
        for r in rows:
            sid = int(r["resolved_symbol_id"])
            if sid in visited:
                continue
            visited.add(sid)
            queue.append(sid)
    return visited


def _caller_count(conn, symbol_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT caller_symbol_id) AS n
          FROM call_edge
         WHERE resolved_symbol_id = ?
        """,
        (symbol_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def find_coverage_gaps(
    repo_path: Path,
    limit: int | None = None,
    min_complexity: int = 1,
) -> CoverageGapReport:
    """Find public symbols that no test entry point can reach.

    Args:
      limit: cap on returned gaps (after sorting). None = unlimited.
      min_complexity: only flag symbols whose stored cyclomatic complexity
                      is at least this value. Default 1 returns everything;
                      raise it (e.g. 5) to focus on risky untested code.
    """
    repo_path = repo_path.resolve()
    rep = CoverageGapReport(repo_path=str(repo_path))
    conn = connect(repo_path)
    try:
        test_file_ids, entry_ids = _collect_test_entry_points(conn)
        rep.test_entry_points = len(entry_ids)

        if not entry_ids:
            rep.note = (
                "no test entry points found — looked for files matching "
                "test_*.py / *_test.py / tests/* with functions starting "
                "with test_"
            )
            return rep

        reached = _multi_source_forward_bfs(conn, entry_ids)
        rep.reached_count = len(reached)

        # Public symbol pool: function/method/class, not private, not dunder,
        # not in a test file. We *include* the test entry points in the
        # reached set so the percentage reflects "non-test code that tests
        # touch", not "tests test themselves".
        if test_file_ids:
            placeholders = ",".join("?" * len(test_file_ids))
            public_rows = conn.execute(
                f"""
                SELECT s.symbol_id, s.qualified_name, s.name, s.kind,
                       s.lineno, s.is_private, s.complexity, s.line_count,
                       f.rel_path
                  FROM symbol s
                  JOIN file f ON f.file_id = s.file_id
                 WHERE s.kind IN ('function', 'method', 'class')
                   AND s.file_id NOT IN ({placeholders})
                """,
                tuple(test_file_ids),
            ).fetchall()
        else:
            public_rows = conn.execute(
                """
                SELECT s.symbol_id, s.qualified_name, s.name, s.kind,
                       s.lineno, s.is_private, s.complexity, s.line_count,
                       f.rel_path
                  FROM symbol s
                  JOIN file f ON f.file_id = s.file_id
                 WHERE s.kind IN ('function', 'method', 'class')
                """
            ).fetchall()

        public_pool: list[dict] = []
        for r in public_rows:
            if _is_dunder(r["name"]):
                continue
            if r["is_private"]:
                continue
            public_pool.append(dict(r))

        rep.total_public_symbols = len(public_pool)
        if rep.total_public_symbols == 0:
            rep.coverage_pct = 0.0
            return rep

        reached_public = sum(1 for r in public_pool if int(r["symbol_id"]) in reached)
        rep.coverage_pct = round(100.0 * reached_public / rep.total_public_symbols, 1)

        gaps: list[CoverageGap] = []
        for r in public_pool:
            sid = int(r["symbol_id"])
            if sid in reached:
                continue
            cmplx = int(r["complexity"] or 0)
            if cmplx < min_complexity:
                continue
            gaps.append(
                CoverageGap(
                    qualified_name=r["qualified_name"],
                    name=r["name"],
                    kind=r["kind"],
                    rel_path=r["rel_path"],
                    lineno=int(r["lineno"]),
                    complexity=cmplx,
                    line_count=int(r["line_count"] or 0),
                    caller_count=_caller_count(conn, sid),
                )
            )

        # Sort: most risky first (high complexity, then long, then path).
        gaps.sort(
            key=lambda g: (-g.complexity, -g.line_count, g.rel_path, g.lineno)
        )
        if limit is not None:
            gaps = gaps[:limit]
        rep.gaps = gaps
    finally:
        conn.close()
    return rep
