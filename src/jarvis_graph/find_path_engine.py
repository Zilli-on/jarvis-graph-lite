"""find_path: shortest call-chain between two symbols.

`impact` already tells you "if I touch X, what's the blast radius?". The
gap it leaves is "how does my code *get to* this expensive call?". This
engine answers that — given a source symbol and a target symbol, find a
chain of resolved call_edges connecting them. Useful when you want to:

  - confirm that an entrypoint actually reaches a deep helper
  - trace the path from a public API to a slow database call
  - understand "why is this lock acquired here?" via the call stack

Both endpoints are resolved through the same lookup that `context` and
`impact` use, so dotted names (`Cls.method`, `pkg.mod.fn`), bare names,
and file paths all work.

Algorithm: forward BFS over `call_edge.resolved_symbol_id`. Each level
expands to "every symbol the current frontier can call (directly,
resolved)". The first level that contains the target wins. The path is
reconstructed via a parent map. We bound the search by `max_depth`
(default 8) and abort early as soon as the target is found.

Limitations:
  - Unresolved call edges are invisible. If the path goes through a
    `getattr`-style dispatch, we won't see it.
  - We return *one* shortest path, not all shortest paths. The first one
    BFS finds is good enough for "show me a route" UX.
  - Cycles are handled (visited set), so revisits don't loop.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.context_engine import _resolve_target
from jarvis_graph.db import connect


@dataclass
class PathStep:
    qualified_name: str
    rel_path: str
    lineno: int  # the line where the call edge OUT of this symbol was made


@dataclass
class FindPathResult:
    source: str
    target: str
    found: bool
    source_qname: str | None = None
    target_qname: str | None = None
    steps: list[PathStep] = field(default_factory=list)
    depth: int = 0
    nodes_explored: int = 0
    note: str = ""


def _symbol_id_for_target(conn, target: str) -> tuple[int, str, str, int] | None:
    """Resolve a user-supplied symbol token. Returns (symbol_id, qname,
    rel_path, lineno) or None if it's a file (find_path is symbol-only)
    or unresolvable."""
    resolved = _resolve_target(conn, target)
    if resolved is None:
        return None
    kind, data = resolved
    if kind != "symbol":
        return None
    return (
        int(data["symbol_id"]),
        str(data["qualified_name"]),
        str(data["rel_path"]),
        int(data["lineno"]),
    )


def _resolved_callees(conn, symbol_id: int) -> list[tuple[int, int]]:
    """Direct call_edges out of `symbol_id` that resolved to a real
    symbol. Returns (callee_symbol_id, lineno) pairs."""
    rows = conn.execute(
        """
        SELECT resolved_symbol_id, lineno
          FROM call_edge
         WHERE caller_symbol_id = ?
           AND resolved_symbol_id IS NOT NULL
        """,
        (symbol_id,),
    ).fetchall()
    return [(int(r["resolved_symbol_id"]), int(r["lineno"])) for r in rows]


def _symbol_lookup(conn, symbol_id: int) -> tuple[str, str, int]:
    row = conn.execute(
        """
        SELECT s.qualified_name, f.rel_path, s.lineno
          FROM symbol s
          JOIN file f ON f.file_id = s.file_id
         WHERE s.symbol_id = ?
        """,
        (symbol_id,),
    ).fetchone()
    return (str(row["qualified_name"]), str(row["rel_path"]), int(row["lineno"]))


def find_path(
    repo_path: Path,
    source: str,
    target: str,
    max_depth: int = 8,
) -> FindPathResult:
    """Find a shortest resolved call path from `source` to `target`.

    Args:
      max_depth: BFS frontier cap. The default of 8 covers most realistic
                 call stacks; higher values let you trace deeper but cost
                 wall-clock on big repos.
    """
    repo_path = repo_path.resolve()
    result = FindPathResult(source=source, target=target, found=False)
    conn = connect(repo_path)
    try:
        src = _symbol_id_for_target(conn, source)
        if src is None:
            result.note = f"source not resolvable as a symbol: {source}"
            return result
        dst = _symbol_id_for_target(conn, target)
        if dst is None:
            result.note = f"target not resolvable as a symbol: {target}"
            return result

        src_id, src_qname, src_path, src_line = src
        dst_id, dst_qname, dst_path, dst_line = dst
        result.source_qname = src_qname
        result.target_qname = dst_qname

        if src_id == dst_id:
            # Trivial: source == target. Return a single-step "path".
            result.found = True
            result.depth = 0
            result.steps = [PathStep(src_qname, src_path, src_line)]
            result.nodes_explored = 1
            return result

        # BFS frontier with parent map for path reconstruction.
        # parent[sym_id] = (prev_sym_id, lineno_of_call_edge_into_this_node)
        parent: dict[int, tuple[int, int]] = {src_id: (-1, src_line)}
        queue: deque[tuple[int, int]] = deque([(src_id, 0)])
        explored = 0
        found_at: int | None = None

        while queue:
            cur_id, depth = queue.popleft()
            explored += 1
            if depth >= max_depth:
                continue
            for callee_id, call_line in _resolved_callees(conn, cur_id):
                if callee_id in parent:
                    continue
                parent[callee_id] = (cur_id, call_line)
                if callee_id == dst_id:
                    found_at = callee_id
                    break
                queue.append((callee_id, depth + 1))
            if found_at is not None:
                break

        result.nodes_explored = explored
        if found_at is None:
            result.note = (
                f"no resolved call path within depth {max_depth} "
                f"({explored} nodes explored)"
            )
            return result

        # Reconstruct: walk parent map back from target to source.
        chain_ids: list[int] = []
        chain_lines: list[int] = []
        cur = found_at
        while cur != -1:
            chain_ids.append(cur)
            chain_lines.append(parent[cur][1])
            prev = parent[cur][0]
            if prev == -1:
                break
            cur = prev
        chain_ids.reverse()
        chain_lines.reverse()

        steps: list[PathStep] = []
        for i, sid in enumerate(chain_ids):
            qname, rel_path, def_line = _symbol_lookup(conn, sid)
            # For non-source nodes, use the call-edge line (the line where
            # the previous frame called into this one). For the source, use
            # its definition line.
            line = chain_lines[i] if i > 0 else def_line
            steps.append(PathStep(qname, rel_path, line))

        result.found = True
        result.depth = len(steps) - 1
        result.steps = steps
    finally:
        conn.close()
    return result
