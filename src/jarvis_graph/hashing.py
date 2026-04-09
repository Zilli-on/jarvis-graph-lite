"""sha256 file hashing in 64 KiB chunks. Low-RAM friendly."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 64 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()
