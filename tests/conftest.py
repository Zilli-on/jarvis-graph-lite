"""Shared test helpers (no pytest — stdlib unittest only).

`prepare_sample_repo` copies the canned fixture into a tmp dir and indexes
it once. Each test class typically calls it in `setUp`.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

# Make `src/` importable without installing the package.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_graph.indexer import index_repo  # noqa: E402

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def prepare_sample_repo() -> tuple[Path, Path]:
    """Copy fixture into a fresh tmp dir, index it, return (tmp_root, repo_dir)."""
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_test_"))
    dst = tmp_root / "sample_repo"
    shutil.copytree(FIXTURE_REPO, dst)
    index_repo(dst, full=True)
    return tmp_root, dst


def prepare_extended_repo() -> tuple[Path, Path]:
    """Sample repo + extra files for the v0.2 health-check engines.

    Adds:
      - `dead_function.py`  — defines a function nothing calls
      - `unused_import.py`  — imports something it never uses
      - `cycle_a.py` / `cycle_b.py` — mutually-recursive imports
    """
    tmp_root, repo = prepare_sample_repo()

    (repo / "dead_function.py").write_text(
        "def really_dead() -> int:\n"
        "    return 1\n"
        "\n"
        "def used_function() -> int:\n"
        "    return 42\n"
        "\n"
        "# Top-level call so used_function has a caller in the index.\n"
        "_PRELOADED = used_function()\n",
        encoding="utf-8",
    )
    (repo / "unused_import.py").write_text(
        "import os\n"
        "from helpers import format_greeting, load_config\n"
        "\n"
        "def go() -> str:\n"
        "    return format_greeting('hi')\n"
        "\n"
        "_RESULT = go()\n",
        encoding="utf-8",
    )
    (repo / "cycle_a.py").write_text(
        "from cycle_b import b_func\n"
        "\n"
        "def a_func() -> int:\n"
        "    return b_func() + 1\n",
        encoding="utf-8",
    )
    (repo / "cycle_b.py").write_text(
        "from cycle_a import a_func\n"
        "\n"
        "def b_func() -> int:\n"
        "    return a_func() + 1\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


def cleanup(tmp_root: Path) -> None:
    shutil.rmtree(tmp_root, ignore_errors=True)
