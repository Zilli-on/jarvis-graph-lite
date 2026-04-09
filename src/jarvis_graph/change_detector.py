"""detect_changes: report what changed since the last index pass.

Compares each *.py file currently on disk to its sha256 in the index DB.
Reports added / modified / removed / unchanged groups and recommends
incremental vs full reindex based on the size of the diff.

No git required — works for dirty trees, untracked files, and repos that
are not git checkouts at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect
from jarvis_graph.hashing import sha256_file
from jarvis_graph.utils import iter_python_files


@dataclass
class ChangeReport:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged_count: int = 0
    total_on_disk: int = 0
    total_in_index: int = 0
    recommendation: str = "incremental"  # 'incremental' | 'full' | 'no_changes'
    reason: str = ""


def detect_changes(repo_path: Path) -> ChangeReport:
    repo_path = repo_path.resolve()
    report = ChangeReport()

    conn = connect(repo_path)
    try:
        index_state: dict[str, str] = {}
        for row in conn.execute("SELECT rel_path, sha256 FROM file"):
            index_state[row["rel_path"]] = row["sha256"]
    finally:
        conn.close()

    report.total_in_index = len(index_state)

    seen: set[str] = set()
    for abs_path, rel_path in iter_python_files(repo_path):
        rel_str = str(rel_path).replace("\\", "/")
        seen.add(rel_str)
        report.total_on_disk += 1

        prev_sha = index_state.get(rel_str)
        if prev_sha is None:
            report.added.append(rel_str)
            continue
        try:
            cur_sha = sha256_file(abs_path)
        except OSError:
            # Treat unreadable files as modified — caller should investigate.
            report.modified.append(rel_str)
            continue
        if cur_sha != prev_sha:
            report.modified.append(rel_str)
        else:
            report.unchanged_count += 1

    for rel_str in index_state.keys():
        if rel_str not in seen:
            report.removed.append(rel_str)

    report.added.sort()
    report.modified.sort()
    report.removed.sort()

    changes = len(report.added) + len(report.modified) + len(report.removed)
    if changes == 0:
        report.recommendation = "no_changes"
        report.reason = "index is up to date"
    elif report.total_in_index == 0:
        report.recommendation = "full"
        report.reason = "no prior index found — run a full index pass"
    elif changes > max(20, report.total_in_index // 2):
        report.recommendation = "full"
        report.reason = (
            f"{changes} changes vs {report.total_in_index} indexed files — "
            "full reindex is faster than many incremental updates"
        )
    else:
        report.recommendation = "incremental"
        report.reason = (
            f"{changes} changes ({len(report.added)} added, "
            f"{len(report.modified)} modified, {len(report.removed)} removed) — "
            "an incremental `index` will catch up cheaply"
        )

    return report
