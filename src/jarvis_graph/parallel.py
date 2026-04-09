"""Optional parallel parsing helpers for the indexer.

The single bottleneck of `index_repo` is `ast.parse` per file: it's CPU-bound,
pure-Python, and trivially parallelisable since each file is independent.
This module provides a small `parse_in_parallel()` helper that fans out
parsing across a `ProcessPoolExecutor` and yields completed `ParsedFile`s in
arbitrary order. The caller (the indexer) is still responsible for SQLite
inserts in the main process — sqlite3 connections do not survive a fork.

The pool is opt-in: callers fall back to a sequential path for small repos
where worker startup costs dominate.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Iterator, Tuple

from jarvis_graph.models import ParsedFile

# Captured at import time so the worker initializer can rebuild a `sys.path`
# matching the parent process — covers test runners that manually inject
# `src/` and editable installs alike.
_PARENT_SYS_PATH: tuple[str, ...] = tuple(sys.path)


def _worker_init(extra_paths: tuple[str, ...]) -> None:
    """Make sure workers can `import jarvis_graph` even on spawn-mode Windows."""
    for p in extra_paths:
        if p and p not in sys.path:
            sys.path.insert(0, p)


def _parse_worker(args: Tuple[str, str]) -> ParsedFile:
    """Top-level worker — must be picklable. Strings instead of `Path` to
    avoid surprising path resolution differences across platforms."""
    from jarvis_graph.parser_python import parse_python_file  # local import

    abs_str, rel_str = args
    return parse_python_file(Path(abs_str), Path(rel_str))


def default_workers() -> int:
    """A safe default that scales with CPU count but caps at 8 to avoid
    contention with the SQLite writer in the main process."""
    cpu = os.cpu_count() or 4
    return max(1, min(8, cpu - 1))


def should_parallelize(file_count: int) -> bool:
    """Worker startup costs dominate small repos; parallelism only helps
    once the parse fan-out is large enough to amortise the spawn delay."""
    return file_count >= 50


def parse_in_parallel(
    files: Iterable[Tuple[Path, Path]],
    max_workers: int | None = None,
) -> Iterator[ParsedFile]:
    """Parse `files` in a process pool. Yields ParsedFiles as they complete.

    On any unrecoverable executor error (e.g. broken pool), the iteration
    stops cleanly so the caller can fall back to sequential parsing.
    """
    workers = max_workers or default_workers()
    payload = [(str(a), str(r)) for a, r in files]
    if not payload:
        return
    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(_PARENT_SYS_PATH,),
        ) as ex:
            futures = [ex.submit(_parse_worker, item) for item in payload]
            for fut in as_completed(futures):
                try:
                    yield fut.result()
                except Exception:  # noqa: BLE001 — defensive: skip bad files
                    continue
    except Exception:  # noqa: BLE001 — pool died entirely; let caller retry
        return
