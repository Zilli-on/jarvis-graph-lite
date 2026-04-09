"""Tiny shared helpers. Keep this file small on purpose."""

from __future__ import annotations

import time
from pathlib import Path

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


def iter_python_files(repo_path: Path):
    """Yield (abs_path, rel_path) for every .py file under `repo_path`,
    skipping noise directories. Iterative + low-memory."""
    repo_path = repo_path.resolve()
    stack = [repo_path]
    while stack:
        d = stack.pop()
        try:
            for entry in d.iterdir():
                name = entry.name
                if entry.is_dir():
                    if name in SKIP_DIRS or name.startswith("."):
                        # Allow . prefixed dirs only if not in SKIP_DIRS — but to keep
                        # the index lean, drop all dotted dirs.
                        if name not in (".",):
                            continue
                    stack.append(entry)
                elif entry.is_file() and name.endswith(".py"):
                    yield entry, entry.relative_to(repo_path)
        except (PermissionError, OSError):
            continue
