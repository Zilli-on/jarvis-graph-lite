"""Behaviour tests for jarvis_graph.hashing.

Digests are pinned against precomputed literal values (and against hashlib
applied to the *in-memory* contents) so a chunking change, an off-by-one in
the chunk loop, or a switch to a weaker hash makes a test fail.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from conftest import ROOT  # noqa: F401  ensures src/ is on sys.path

from jarvis_graph.hashing import sha256_file

# sha256 of the exact byte literal pinned below, computed independently.
_PINNED_CONTENT = b"jarvis-graph-lite pin test\n"
_PINNED_DIGEST = "3ef429d4a0b6f5bcfd8a6367dcc16f8339c5aa7151027f7537e933640ea64127"


class Sha256FileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="jgl_hash_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, data: bytes) -> Path:
        p = self._tmp / "payload.bin"
        p.write_bytes(data)
        return p

    def test_known_content_matches_pinned_digest(self) -> None:
        self.assertEqual(sha256_file(self._write(_PINNED_CONTENT)), _PINNED_DIGEST)

    def test_empty_file_is_sha256_empty_string(self) -> None:
        p = self._write(b"")
        self.assertEqual(sha256_file(p), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_multi_chunk_file_matches_hashlib(self) -> None:
        # 200 KiB > 64 KiB chunk: forces several read() rounds, so a chunking
        # regression (dropping the tail, re-reading the head) shows up here.
        data = bytes(range(256)) * 800
        self.assertEqual(sha256_file(self._write(data)), hashlib.sha256(data).hexdigest())

    def test_chunk_boundary_exact_size(self) -> None:
        # Sizes straddling the 64 KiB chunk boundary must hash the full
        # contents: one chunk, one chunk + 1 byte, and one chunk - 1 byte.
        for size in (64 * 1024 - 1, 64 * 1024, 64 * 1024 + 1):
            data = bytes(i % 256 for i in range(size))
            p = self._write(data)
            self.assertEqual(
                sha256_file(p),
                hashlib.sha256(data).hexdigest(),
                f"size={size}",
            )

    def test_digest_is_64_lowercase_hex_chars(self) -> None:
        digest = sha256_file(self._write(b"anything"))
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, digest.lower())
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_different_content_yields_different_digest(self) -> None:
        short = sha256_file(self._write(b"jarvis-graph-lite pin test\n"))
        long = sha256_file(self._write(b"jarvis-graph-lite pin test"))
        self.assertNotEqual(short, long)


if __name__ == "__main__":
    unittest.main()
