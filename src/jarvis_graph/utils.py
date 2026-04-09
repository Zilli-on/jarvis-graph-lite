"""Tiny shared helpers. Keep this file small on purpose."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

from jarvis_graph.gitignore import GitignoreStack

# Directories we never descend into when indexing.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".jarvis_graph",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        "build",
        "dist",
        ".idea",
        ".vs",
        ".vscode",
        "site-packages",
    }
)

JARVIS_GRAPH_DIRNAME = ".jarvis_graph"


def now_epoch() -> int:
    return int(time.time())


def repo_data_dir(repo_path: Path) -> Path:
    return repo_path / JARVIS_GRAPH_DIRNAME


def to_module_path(rel_path: Path) -> str:
    """Convert `pkg/sub/mod.py` (or `pkg/sub/__init__.py`) to `pkg.sub.mod`.

    Uses POSIX separators internally so Windows backslashes don't leak in.
    """
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def iter_python_files(
    repo_path: Path,
    respect_gitignore: bool = True,
) -> Iterator[tuple[Path, Path]]:
    """Yield ``(abs_path, rel_path)`` for every `.py` file under ``repo_path``.

    Skips:
      * hardcoded noise dirs (`SKIP_DIRS`)
      * dotted directories (`.git`, `.cache`, …)
      * paths matched by any active `.gitignore` (when ``respect_gitignore``)

    The walker is recursive so a `GitignoreStack` can layer rules naturally
    when entering / leaving subdirectories.
    """
    repo_path = repo_path.resolve()
    stack = GitignoreStack()

    def _walk(d: Path) -> Iterator[tuple[Path, Path]]:
        added = False
        if respect_gitignore:
            gi = d / ".gitignore"
            if gi.is_file():
                added = stack.push(d, gi)
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError):
            if added:
                stack.pop()
            return
        # Stable order: dirs after files matters less than determinism.
        entries.sort(key=lambda p: p.name)
        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if name in SKIP_DIRS or name.startswith("."):
                    continue
                if respect_gitignore and stack.is_ignored(entry, True):
                    continue
                yield from _walk(entry)
            else:
                if not name.endswith(".py"):
                    continue
                if respect_gitignore and stack.is_ignored(entry, False):
                    continue
                yield entry, entry.relative_to(repo_path)
        if added:
            stack.pop()

    yield from _walk(repo_path)
