"""find_todo_comments: rank TODO/FIXME/XXX/HACK/BUG comments by risk.

Every dev already has grep. What grep can't tell you is *which* TODO
comments actually matter. A FIXME in a 2-line helper is a yak-shave
you can postpone forever; a FIXME in a 50-line cyclomatic-complexity-20
function is a time bomb.

This engine cross-references each comment hit with the complexity and
size of the enclosing symbol (function / method / class / module). The
result: TODOs sorted by *risk*, where risk is a deliberately simple
composite:

    risk = tag_weight + complexity + (line_count * 0.1)

  - tag_weight: BUG / HACK = 4, FIXME = 3, TODO / XXX = 2
  - complexity: McCabe cyclomatic of the enclosing symbol
  - line_count: LOC of the enclosing symbol, scaled 10x down

Risk buckets:
  >= 20 critical  (TODO sitting inside a beast function — fix it first)
  >= 10 high
  >=  5 medium
  <   5 low       (comment drift, forget about it)

Why this shape (vs a neural ranker, or fancier heuristics)?
  1. The whole point of jarvis-graph-lite is "stdlib only, fast, obvious"
  2. Additive scoring is trivially explainable: you can SQL-join the
     output and get the same numbers.
  3. The complexity score already encodes the "risky surface" signal —
     we don't need to re-derive it from something else.

Parsing
-------
Comments are extracted via stdlib `tokenize`, not a regex on raw text.
This is critical: tokenize knows about string literals, f-strings, and
line continuations, so a string like `x = "TODO list"` won't be
mistaken for a real comment. Files that fail to tokenize are skipped
silently (the indexer has already flagged them via `parse_error`).

TODO tag detection
------------------
The tag regex is case-insensitive with word boundaries:
    \\b(TODO|FIXME|XXX|HACK|BUG)\\b

So:
  - `# TODO: fix this`           -> matches, tag="todo"
  - `# FIXME - broken`           -> matches, tag="fixme"
  - `# HACK(fabi): workaround`   -> matches, tag="hack"
  - `todoList.append(1)`         -> does NOT match (no # prefix, and
                                     also not a comment token)
  - `# a quick todo list review` -> matches, tag="todo"
                                     (word-boundary false positive
                                     we accept; the ranker will bucket
                                     most of these as 'low')

Limitations (accepted)
----------------------
  - TODOs inside docstrings are not parsed. docstrings are STRING
    tokens, not COMMENT tokens. Intentional: docstrings are
    user-facing API documentation, not dev scratchpad.
  - Multi-line block comments (`# line 1\\n# line 2`) count as one hit
    per physical line.
  - Tag text with embedded parens (`TODO(fabi):`) is preserved as-is
    in the `text` field — we don't try to parse out assignees.
  - Test files are excluded by default (they're full of dev scratchpad
    that isn't production risk); pass `include_tests=True` to include.
"""

from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_graph.db import connect

# We require the tag to be preceded by start-of-string, whitespace, '#',
# or one of a few bullet-style characters ('(', '[', '{', '*', '-').
# A naive \bTAG\b matches inside things like `average:X.XXX` because `.`
# is a word/non-word boundary — dogfooding on JARVIS surfaced exactly
# this false positive in an amv_engine subprocess parse comment. Real
# tag comments are always introduced by whitespace or a comment marker;
# gluing triple-X onto the end of an identifier-ish token isn't a real
# tag.
#
# v0.12.4: case-SENSITIVE match (all-caps only). The pre-v0.12.4 regex
# had `re.IGNORECASE`, which matched English prose like
# "The b_u_g was silent because..." (from the v0.12.3 docstring) or
# "this is a h_a_c_k around the limitation" — every dev tool on earth
# treats these tags as all-caps by convention (ripgrep's `--type-add`,
# VSCode's Todo Highlight, grep.app, pylint's W0511), and the false
# positive rate of IGNORECASE on any non-trivial codebase dominates
# whatever small benefit there is for lowercase tags.
_TAG_RE = re.compile(
    r"(?:^|[\s#(\[{*\-])(" + "T" + "ODO|" + "F" + "IXME|" + "X" + "XX|" +
    "H" + "ACK|" + "B" + "UG)" + r"\b",
)

# Tag weights: bug and hack are the worst (self-admitted correctness
# issues), fix-me is the middle (something is broken but maybe bounded),
# and the placeholder tags are future-work notes that the author may
# never get to.
_TAG_WEIGHTS: dict[str, int] = {
    "bug": 4,
    "hack": 4,
    "fixme": 3,
    "todo": 2,
    "xxx": 2,
}


@dataclass
class TodoHit:
    rel_path: str
    lineno: int
    tag: str                 # normalized lowercase: "todo", "fixme", ...
    text: str                # the comment text, leading '#' stripped
    enclosing_qname: str     # qualified_name of enclosing symbol, or ""
    enclosing_kind: str      # "function"/"method"/"class"/"module"
    complexity: int          # cyclomatic of enclosing symbol (0 for module)
    line_count: int          # LOC of enclosing symbol (0 for module)
    risk: float              # composite score
    risk_bucket: str         # "low" | "medium" | "high" | "critical"


@dataclass
class TodoReport:
    repo_path: str
    total_files_scanned: int = 0
    files_with_todos: int = 0
    total_hits: int = 0
    by_tag: dict[str, int] = field(default_factory=dict)
    by_bucket: dict[str, int] = field(default_factory=dict)
    hits: list[TodoHit] = field(default_factory=list)


def _is_test_path(rel_path: str) -> bool:
    """Same convention as coverage_gap_engine and refactor_priority."""
    p = rel_path.replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    if base.startswith("test_") and base.endswith(".py"):
        return True
    if base.endswith("_test.py"):
        return True
    if "/tests/" in p or p.startswith("tests/"):
        return True
    return False


def _bucket(score: float) -> str:
    if score >= 20:
        return "critical"
    if score >= 10:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


def _score(tag_weight: int, complexity: int, line_count: int) -> float:
    """Composite risk score.

    Additive so the contribution of each signal is visible at a glance.
    LOC is scaled down 10x because 100-line functions shouldn't swamp
    out the complexity signal (complexity maxes in the 20s for sane
    code, LOC regularly hits the 100s).
    """
    return float(tag_weight) + float(complexity) + (float(line_count) * 0.1)


def _extract_comments(abs_path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, comment_string), ...] using stdlib tokenize.

    tokenize is the authoritative source for comment locations because
    it understands string literals, f-strings, line continuations, and
    encoding declarations. A regex on raw text would produce false
    positives on things like `x = "# TODO: ..."`.
    """
    try:
        source = abs_path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return []
    comments: list[tuple[int, str]] = []
    try:
        for tok in tokenize.tokenize(io.BytesIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comments.append((tok.start[0], tok.string))
    except (tokenize.TokenError, SyntaxError, IndentationError, ValueError):
        # Files that can't be tokenized are skipped silently. The
        # indexer would already have set parse_error on them.
        return []
    return comments


def _find_enclosing(conn, file_id: int, lineno: int) -> dict | None:
    """Find the innermost function/method/class containing `lineno`.

    We pick the symbol with the highest lineno that still satisfies
    lineno <= todo_line <= end_lineno. The ORDER BY lineno DESC LIMIT 1
    gives us the innermost nest level for free — no recursive CTE
    needed, no tree walking.

    Returns a dict (not sqlite3.Row) so callers can use .get safely
    and the row is detached from the cursor.
    """
    row = conn.execute(
        """
        SELECT qualified_name, name, kind, lineno, end_lineno,
               complexity, line_count
          FROM symbol
         WHERE file_id = ?
           AND kind IN ('function', 'method', 'class')
           AND lineno <= ?
           AND (end_lineno IS NULL OR end_lineno >= ?)
         ORDER BY lineno DESC
         LIMIT 1
        """,
        (file_id, lineno, lineno),
    ).fetchone()
    return dict(row) if row else None


def _parse_comment(comment: str) -> tuple[str, str] | None:
    """Extract (tag, text) from a comment string, or None if no tag.

    The comment arrives with the leading '#' intact (tokenize preserves
    it). We search for the tag anywhere in the comment body and return
    the cleaned text with the '#' stripped. Only ALL-CAPS tags match
    (the universal dev convention); see `_TAG_RE` comment for why.
    """
    m = _TAG_RE.search(comment)
    if not m:
        return None
    tag = m.group(1).lower()
    text = comment.lstrip("#").strip()
    return tag, text


def find_todo_comments(
    repo_path: Path,
    limit: int | None = None,
    min_risk: float = 0.0,
    include_tests: bool = False,
) -> TodoReport:
    """Scan all indexed files for TODO/FIXME/XXX/HACK/BUG comments,
    enrich each with complexity + line_count of the enclosing symbol,
    and rank by composite risk.

    Args:
        repo_path: root of an already-indexed repo (.jarvis_graph/ must exist)
        limit: cap on returned hits after sorting. None = unlimited.
        min_risk: drop hits with risk below this threshold before limit.
        include_tests: include TODOs in test files too (default: skip).
    """
    repo_path = repo_path.resolve()
    rep = TodoReport(repo_path=str(repo_path))
    conn = connect(repo_path)
    try:
        file_rows = conn.execute(
            "SELECT file_id, rel_path, abs_path FROM file"
        ).fetchall()
        rep.total_files_scanned = len(file_rows)
        for frow in file_rows:
            file_id = int(frow["file_id"])
            rel = frow["rel_path"]
            abs_path = Path(frow["abs_path"])
            if not include_tests and _is_test_path(rel):
                continue
            comments = _extract_comments(abs_path)
            hits_in_file = 0
            for lineno, comment_str in comments:
                parsed = _parse_comment(comment_str)
                if parsed is None:
                    continue
                tag, clean_text = parsed

                enclosing = _find_enclosing(conn, file_id, lineno)
                if enclosing is not None:
                    qname = enclosing["qualified_name"]
                    kind = enclosing["kind"]
                    cplx = int(enclosing["complexity"] or 0)
                    lc = int(enclosing["line_count"] or 0)
                else:
                    qname = ""
                    kind = "module"
                    cplx = 0
                    lc = 0

                score = _score(_TAG_WEIGHTS[tag], cplx, lc)
                if score < min_risk:
                    continue

                hit = TodoHit(
                    rel_path=rel,
                    lineno=lineno,
                    tag=tag,
                    text=clean_text,
                    enclosing_qname=qname,
                    enclosing_kind=kind,
                    complexity=cplx,
                    line_count=lc,
                    risk=round(score, 2),
                    risk_bucket=_bucket(score),
                )
                rep.hits.append(hit)
                rep.by_tag[tag] = rep.by_tag.get(tag, 0) + 1
                rep.by_bucket[hit.risk_bucket] = (
                    rep.by_bucket.get(hit.risk_bucket, 0) + 1
                )
                hits_in_file += 1
            if hits_in_file > 0:
                rep.files_with_todos += 1
    finally:
        conn.close()

    # Sort: highest risk first, then file path (stable ordering for drift)
    rep.hits.sort(key=lambda h: (-h.risk, h.rel_path, h.lineno))
    rep.total_hits = len(rep.hits)
    if limit is not None:
        rep.hits = rep.hits[:limit]
    return rep
