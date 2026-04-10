"""refactor_priority: rank functions/methods by a composite "fix this first"
score that combines cyclomatic complexity, function length, test coverage,
and caller count.

The other engines each answer one question:
  - find_complexity       — "what's hard to read?"
  - find_long_functions   — "what's unwieldy?"
  - find_coverage_gaps    — "what's untested?"
  - find_dead_code        — "what's not called at all?"

refactor_priority answers "given ALL of those at once, what should I fix
FIRST?". It computes a per-symbol composite score so the list surfaces
the rows where every warning lines up: long AND complex AND untested AND
has several callers that would break if you touched it badly.

Scoring model (each sub-score is 0-100):
  complexity_score = min(complexity / 50, 1) * 100
  size_score       = min(line_count / 500, 1) * 100
  weight_factor    = (complexity_score + size_score) / 200  # 0..1
  untested_penalty = 100 * weight_factor   # tiny functions barely get a penalty
  caller_score     = min(log2(caller_count + 1) * 25, 100) * weight_factor

Composite priority = round(
      1.0 * complexity_score
    + 0.5 * size_score
    + 1.0 * untested_penalty
    + 0.5 * caller_score
, 1)

The weight_factor is the key insight that distinguishes refactor_priority
from a naive "everything untested gets 100 points" engine. Trivial one-
line helpers don't need refactoring even if they're untested or called
everywhere — they're already as simple as they can be. By scaling the
untested and caller penalties by the function's intrinsic complexity+size,
we surface the rows that actually need work: non-trivial code that is
risky to touch AND hard to verify.

Pre-filters (applied before scoring so the list stays actionable):
  - test files skipped entirely (refactoring tests is a different concern)
  - symbols with complexity < 3 AND line_count < 20 skipped (trivial)
  - dunders and private `_` names skipped
  - `__init__` constructors skipped (too boilerplate-heavy)

The engine expects the repo to already be indexed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.coverage_gap_engine import (
    _collect_test_entry_points,
    _multi_source_forward_bfs,
)
from jarvis_graph.db import connect


@dataclass
class RefactorCandidate:
    qualified_name: str
    name: str
    kind: str
    rel_path: str
    lineno: int
    complexity: int
    line_count: int
    caller_count: int
    is_untested: bool
    complexity_score: float
    size_score: float
    untested_penalty: float
    caller_score: float
    priority: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class RefactorPriorityReport:
    repo_path: str
    total_evaluated: int = 0
    skipped_test: int = 0
    skipped_trivial: int = 0
    threshold: float = 0.0
    candidates: list[RefactorCandidate] = field(default_factory=list)
    note: str = ""


# Weights — see module docstring.
_W_COMPLEXITY = 1.0
_W_SIZE = 0.5
_W_UNTESTED = 1.0
_W_CALLERS = 0.5


def _is_test_path(rel_path: str) -> bool:
    """Match common test file naming conventions.

    We normalize to forward slashes first so Windows `tests\\foo.py` and
    POSIX `tests/foo.py` both match. Fixtures under `tests/fixtures/` are
    also excluded — they look like real code to the parser but exist only
    to exercise the engines.
    """
    p = rel_path.replace("\\", "/").lower()
    if "/tests/" in p or p.startswith("tests/"):
        return True
    if "/test/" in p or p.startswith("test/"):
        return True
    # fixtures under tests/ directories also count
    if "/fixtures/" in p:
        return True
    # file-level naming: test_*.py or *_test.py
    fname = p.rsplit("/", 1)[-1]
    if fname.startswith("test_") and fname.endswith(".py"):
        return True
    if fname.endswith("_test.py"):
        return True
    return False


def _complexity_score(complexity: int) -> float:
    # Cap at 50; beyond that every function is "extreme" anyway.
    return round(min(complexity / 50.0, 1.0) * 100.0, 1)


def _size_score(line_count: int) -> float:
    return round(min(line_count / 500.0, 1.0) * 100.0, 1)


def _caller_score(caller_count: int) -> float:
    # log2 curve so the first few callers matter more than the next ten,
    # and it saturates near ~16 callers (log2(17)*25 ≈ 102 → capped to 100).
    return round(min(math.log2(caller_count + 1) * 25.0, 100.0), 1)


def _reasons(
    complexity: int,
    line_count: int,
    caller_count: int,
    is_untested: bool,
) -> list[str]:
    out: list[str] = []
    if complexity >= 20:
        out.append(f"extreme complexity ({complexity})")
    elif complexity >= 10:
        out.append(f"high complexity ({complexity})")
    if line_count >= 200:
        out.append(f"very long ({line_count} lines)")
    elif line_count >= 80:
        out.append(f"long ({line_count} lines)")
    if is_untested:
        out.append("untested (no test path reaches it)")
    if caller_count >= 10:
        out.append(f"high fan-in ({caller_count} callers — risky refactor)")
    elif caller_count >= 3:
        out.append(f"{caller_count} callers")
    elif caller_count == 0:
        out.append("no callers — might be dead")
    if not out:
        out.append("borderline — consider lower-priority cleanup")
    return out


def find_refactor_priority(
    repo_path: Path,
    min_priority: float = 50.0,
    limit: int = 30,
    include_classes: bool = False,
) -> RefactorPriorityReport:
    """Rank functions/methods by composite refactor urgency.

    Args:
      min_priority: only return candidates whose composite score is >= this.
                    The default 50 surfaces "clearly worth fixing" rows
                    while filtering the long tail of borderline cases.
      limit: cap on returned rows after sorting.
      include_classes: if True, also score `kind='class'` symbols. Default
                       False because classes don't have complexity scores
                       in this index (parser_python stores complexity on
                       functions/methods only).
    """
    repo_path = repo_path.resolve()
    rep = RefactorPriorityReport(
        repo_path=str(repo_path),
        threshold=min_priority,
    )
    conn = connect(repo_path)
    try:
        # Test coverage: figure out which symbols are reached. If no test
        # entry points exist, every symbol is "untested" — we still rank
        # but the untested penalty becomes a constant.
        test_file_ids, entry_ids = _collect_test_entry_points(conn)
        reached: set[int]
        if entry_ids:
            reached = _multi_source_forward_bfs(conn, entry_ids)
        else:
            reached = set()
            rep.note = "no test entry points found — every symbol scored as untested"

        kinds = ("function", "method", "class") if include_classes else ("function", "method")
        placeholders = ",".join("?" * len(kinds))
        rows = conn.execute(
            f"""
            SELECT s.symbol_id, s.qualified_name, s.name, s.kind,
                   s.lineno, s.is_private, s.complexity, s.line_count,
                   f.rel_path
              FROM symbol s
              JOIN file f ON f.file_id = s.file_id
             WHERE s.kind IN ({placeholders})
            """,
            kinds,
        ).fetchall()

        # Pre-compute caller counts in one query to avoid N+1.
        caller_rows = conn.execute(
            """
            SELECT resolved_symbol_id, COUNT(*) AS n
              FROM call_edge
             WHERE resolved_symbol_id IS NOT NULL
             GROUP BY resolved_symbol_id
            """
        ).fetchall()
        caller_map: dict[int, int] = {
            int(r["resolved_symbol_id"]): int(r["n"]) for r in caller_rows
        }

        evaluated: list[RefactorCandidate] = []
        skipped_trivial = 0
        skipped_test = 0
        for r in rows:
            name = r["name"]
            rel_path = r["rel_path"]
            # Pre-filter: skip test files entirely — refactoring tests is
            # a different concern than refactoring production code.
            if _is_test_path(rel_path):
                skipped_test += 1
                continue
            # Skip dunder (catches __init__ too) and private `_name` symbols.
            if name.startswith("__") and name.endswith("__"):
                continue
            if r["is_private"]:
                continue
            complexity = int(r["complexity"] or 0)
            line_count = int(r["line_count"] or 0)
            # Pre-filter: trivial symbols never need refactoring, no matter
            # how many callers they have or whether they're untested.
            if complexity < 3 and line_count < 20:
                skipped_trivial += 1
                continue
            sid = int(r["symbol_id"])
            caller_count = caller_map.get(sid, 0)
            is_untested = sid not in reached if entry_ids else True

            c_score = _complexity_score(complexity)
            s_score = _size_score(line_count)
            # weight_factor in 0..1 — scales "is it risky to touch" penalties
            # by how non-trivial the symbol actually is. A tiny 3-line helper
            # with 100 callers gets weight_factor ≈ 0.03 → barely any penalty,
            # while a 400-line god-function with complexity 40 gets ~0.9.
            weight_factor = (c_score + s_score) / 200.0
            u_penalty = (100.0 * weight_factor) if is_untested else 0.0
            call_score = _caller_score(caller_count) * weight_factor

            priority = (
                _W_COMPLEXITY * c_score
                + _W_SIZE * s_score
                + _W_UNTESTED * u_penalty
                + _W_CALLERS * call_score
            )
            priority = round(priority, 1)

            if priority < min_priority:
                continue

            evaluated.append(
                RefactorCandidate(
                    qualified_name=r["qualified_name"],
                    name=name,
                    kind=r["kind"],
                    rel_path=r["rel_path"],
                    lineno=int(r["lineno"]),
                    complexity=complexity,
                    line_count=line_count,
                    caller_count=caller_count,
                    is_untested=is_untested,
                    complexity_score=c_score,
                    size_score=s_score,
                    untested_penalty=u_penalty,
                    caller_score=call_score,
                    priority=priority,
                    reasons=_reasons(complexity, line_count, caller_count, is_untested),
                )
            )

        rep.total_evaluated = len(rows)
        rep.skipped_test = skipped_test
        rep.skipped_trivial = skipped_trivial
        evaluated.sort(key=lambda c: c.priority, reverse=True)
        rep.candidates = evaluated[:limit]
    finally:
        conn.close()

    return rep
