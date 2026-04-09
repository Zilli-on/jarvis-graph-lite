"""Per-repo config: tiny JSON file inside .jarvis_graph/.

Stores only what is needed to make a repo's index reproducible — repo name,
version of the indexer that wrote it, last index epoch, and any user toggles.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jarvis_graph import __version__
from jarvis_graph.utils import now_epoch, repo_data_dir


@dataclass
class RepoConfig:
    repo_name: str
    indexer_version: str = __version__
    last_indexed_at: int = 0
    skip_extra_dirs: list[str] = field(default_factory=list)


def config_path(repo_path: Path) -> Path:
    return repo_data_dir(repo_path) / "config.json"


def load(repo_path: Path) -> RepoConfig:
    p = config_path(repo_path)
    if not p.exists():
        return RepoConfig(repo_name=repo_path.name)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return RepoConfig(
        repo_name=data.get("repo_name", repo_path.name),
        indexer_version=data.get("indexer_version", __version__),
        last_indexed_at=int(data.get("last_indexed_at", 0)),
        skip_extra_dirs=list(data.get("skip_extra_dirs", [])),
    )


def save(repo_path: Path, cfg: RepoConfig) -> None:
    p = config_path(repo_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cfg.last_indexed_at = now_epoch()
    cfg.indexer_version = __version__
    with p.open("w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
