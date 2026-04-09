"""find_circular_deps: detect import cycles between files.

Builds a directed graph of file → file from resolved import_edges, then
finds strongly-connected components (SCCs) using an iterative Tarjan
implementation. Any SCC with more than one node — or a single node with a
self-loop — is reported as a cycle.

Why SCCs and not naive DFS-cycle-finding: in a non-trivial repo the import
graph contains many cycles that share nodes. Tarjan groups them into the
minimal set of mutually-recursive node clusters, which is what someone
trying to fix the cycles actually wants to see.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect


@dataclass
class CircularDep:
    files: list[str]  # the cluster of mutually-recursive files
    size: int


@dataclass
class CircularDepsReport:
    repo_path: str
    total_files: int = 0
    total_edges: int = 0
    cycles: list[CircularDep] = field(default_factory=list)


def _build_graph(conn) -> tuple[dict[int, list[int]], dict[int, str]]:
    files: dict[int, str] = {}
    for row in conn.execute("SELECT file_id, rel_path FROM file"):
        files[int(row["file_id"])] = row["rel_path"]
    graph: dict[int, list[int]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT DISTINCT file_id, resolved_file_id
          FROM import_edge
         WHERE resolved_file_id IS NOT NULL
        """
    ):
        src = int(row["file_id"])
        dst = int(row["resolved_file_id"])
        if src == dst:
            # self-loop counts as a 1-node cycle
            graph[src].append(dst)
            continue
        graph[src].append(dst)
    return graph, files


def _tarjan_scc(graph: dict[int, list[int]], nodes: list[int]) -> list[list[int]]:
    """Iterative Tarjan SCC. Avoids Python recursion-limit for large repos."""
    index_of: dict[int, int] = {}
    lowlink: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    index_counter = 0
    sccs: list[list[int]] = []

    for start in nodes:
        if start in index_of:
            continue
        # work stack of (node, iterator-over-children)
        work: list[tuple[int, iter]] = [(start, iter(graph.get(start, [])))]
        index_of[start] = index_counter
        lowlink[start] = index_counter
        index_counter += 1
        stack.append(start)
        on_stack.add(start)

        while work:
            v, it = work[-1]
            try:
                w = next(it)
            except StopIteration:
                work.pop()
                if lowlink[v] == index_of[v]:
                    comp: list[int] = []
                    while True:
                        x = stack.pop()
                        on_stack.discard(x)
                        comp.append(x)
                        if x == v:
                            break
                    sccs.append(comp)
                if work:
                    parent = work[-1][0]
                    if lowlink[v] < lowlink[parent]:
                        lowlink[parent] = lowlink[v]
                continue
            if w not in index_of:
                index_of[w] = index_counter
                lowlink[w] = index_counter
                index_counter += 1
                stack.append(w)
                on_stack.add(w)
                work.append((w, iter(graph.get(w, []))))
            elif w in on_stack:
                if index_of[w] < lowlink[v]:
                    lowlink[v] = index_of[w]
    return sccs


def find_circular_deps(repo_path: Path) -> CircularDepsReport:
    repo_path = repo_path.resolve()
    rep = CircularDepsReport(repo_path=str(repo_path))
    conn = connect(repo_path)
    try:
        graph, files = _build_graph(conn)
        rep.total_files = len(files)
        rep.total_edges = sum(len(v) for v in graph.values())
        nodes = sorted(files.keys())
        sccs = _tarjan_scc(graph, nodes)
        for comp in sccs:
            if len(comp) > 1:
                paths = sorted(files.get(fid, f"<file_id {fid}>") for fid in comp)
                rep.cycles.append(CircularDep(files=paths, size=len(paths)))
                continue
            # Single-node SCC: only a cycle if there's a self-loop.
            (fid,) = comp
            if fid in graph and fid in graph[fid]:
                rep.cycles.append(
                    CircularDep(files=[files.get(fid, f"<file_id {fid}>")], size=1)
                )
    finally:
        conn.close()
    rep.cycles.sort(key=lambda c: (-c.size, c.files[0] if c.files else ""))
    return rep
