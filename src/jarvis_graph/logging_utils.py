"""Append-only operations log inside .jarvis_graph/logs/.

Keep dependency-free: just open + write. No threading, no rotation. The log is
plain text, one line per action, human-readable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jarvis_graph.utils import repo_data_dir


def log_path(repo_path: Path) -> Path:
    return repo_data_dir(repo_path) / "logs" / "operations.log"


def log(repo_path: Path, action: str, detail: str = "") -> None:
    p = log_path(repo_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    line = f"{ts}\t{action}\t{detail}\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)
