"""Lightweight `.gitignore` matcher (stdlib only).

Implements the subset of git's wildmatch semantics that matters for indexing
Python repos:

- `*`           — matches anything except `/`
- `?`           — matches a single non-`/` char
- `**`          — matches across path separators (with surrounding `/` handling)
- `[abc]`       — character class
- leading `/`   — anchors the pattern to the directory of the gitignore file
- internal `/`  — anchors the pattern (relative to gitignore dir)
- trailing `/`  — match directories only
- leading `!`   — negation (re-include)
- comments      — lines starting with `#` (after stripping)

Each `.gitignore` file is parsed once into a `GitignoreMatcher`, which can
then be queried with `match(rel_path, is_dir)`. Results compose via the
`GitignoreStack` helper, which layers multiple matchers (outer → inner) so
nested `.gitignore` files behave like git itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class _Rule:
    negate: bool
    dir_only: bool
    anchored: bool
    regex: re.Pattern[str]


def _compile_pattern(pat: str) -> tuple[re.Pattern[str], bool, bool]:
    """Compile a gitignore glob to a regex.

    Returns ``(compiled_regex, anchored, dir_only)``.
    """
    dir_only = pat.endswith("/")
    if dir_only:
        pat = pat[:-1]

    if pat.startswith("/"):
        pat = pat[1:]
        anchored = True
    else:
        # `**/` prefixes don't anchor; check the rest for slashes.
        stripped = pat
        while stripped.startswith("**/"):
            stripped = stripped[3:]
        anchored = "/" in stripped

    out: list[str] = ["^"]
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                # `**` segment
                if i + 2 < n and pat[i + 2] == "/":
                    # `**/` — match zero or more path segments + trailing slash
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pat[j] == "!":
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            if j < n:
                content = pat[i + 1 : j]
                # Convert leading `!` to `^` for regex char-class negation.
                if content.startswith("!"):
                    content = "^" + content[1:]
                out.append("[" + content + "]")
                i = j + 1
            else:
                out.append(re.escape(c))
                i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out)), anchored, dir_only


class GitignoreMatcher:
    """Compiled rules from a single `.gitignore` file.

    The matcher is anchor-agnostic: callers pass paths already relative to the
    directory containing the `.gitignore`. Empty paths are never matched.
    """

    __slots__ = ("_rules",)

    def __init__(self, lines: Iterable[str]):
        self._rules: list[_Rule] = []
        for raw in lines:
            line = raw.rstrip("\r\n").rstrip()
            if not line or line.startswith("#"):
                continue
            negate = False
            if line.startswith("!"):
                negate = True
                line = line[1:]
            if not line:
                continue
            try:
                regex, anchored, dir_only = _compile_pattern(line)
            except re.error:
                # Skip patterns we can't translate; never crash the walker.
                continue
            self._rules.append(_Rule(negate, dir_only, anchored, regex))

    def __bool__(self) -> bool:
        return bool(self._rules)

    def match(self, rel_path: str, is_dir: bool) -> Optional[bool]:
        """Return ``True`` if ignored, ``False`` if explicitly re-included,
        ``None`` if no rule matched.

        ``rel_path`` is interpreted relative to the directory holding the
        `.gitignore`. Path separators may be `/` or `\\`.
        """
        rel_path = rel_path.replace("\\", "/").strip("/")
        if not rel_path:
            return None
        parts = rel_path.split("/")
        basename = parts[-1]
        parents = parts[:-1]

        result: Optional[bool] = None
        for rule in self._rules:
            matched = False
            if rule.anchored:
                # Try the path itself (only if dir_only is satisfied).
                if (not rule.dir_only or is_dir) and rule.regex.match(rel_path):
                    matched = True
                else:
                    # Then try ancestor prefixes — every prefix is a directory.
                    for i in range(1, len(parts)):
                        if rule.regex.match("/".join(parts[:i])):
                            matched = True
                            break
            else:
                if (not rule.dir_only or is_dir) and rule.regex.match(basename):
                    matched = True
                else:
                    for p in parents:
                        if rule.regex.match(p):
                            matched = True
                            break
            if matched:
                result = not rule.negate
        return result


class GitignoreStack:
    """Layered matchers, applied outer-to-inner.

    Used during a recursive walk: each entered directory pushes its
    `.gitignore` (if any) and the corresponding anchor; on exit it pops.
    Inner rules can re-include via `!pattern`.
    """

    __slots__ = ("_layers",)

    def __init__(self) -> None:
        self._layers: list[tuple[Path, GitignoreMatcher]] = []

    def push(self, anchor: Path, gitignore_path: Path) -> bool:
        """Read `gitignore_path` and push its rules anchored at `anchor`.

        Returns True if a layer was actually added.
        """
        try:
            text = gitignore_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        matcher = GitignoreMatcher(text.splitlines())
        if not matcher:
            return False
        self._layers.append((anchor.resolve(), matcher))
        return True

    def pop(self) -> None:
        if self._layers:
            self._layers.pop()

    def is_ignored(self, abs_path: Path, is_dir: bool) -> bool:
        result = False
        abs_path = abs_path.resolve()
        for anchor, matcher in self._layers:
            try:
                rel = abs_path.relative_to(anchor)
            except ValueError:
                continue
            verdict = matcher.match(str(rel), is_dir)
            if verdict is not None:
                result = verdict
        return result
