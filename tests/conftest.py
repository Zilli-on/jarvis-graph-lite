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


def cleanup(tmp_root: Path) -> None:
    shutil.rmtree(tmp_root, ignore_errors=True)
