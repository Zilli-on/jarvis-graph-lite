"""Tiny scoring helpers shared by query/context engines.

Deterministic, no fuzzy libs. Higher = better match.
"""

from __future__ import annotations


def score_symbol_name(name: str, query: str) -> int:
    """Symbol-name match score in [0, 100]."""
    n = name.lower()
    q = query.lower()
    if n == q:
        return 100
    if n.startswith(q):
        return 75
    if q in n:
        return 55
    return 0


def score_qname(qname: str, query: str) -> int:
    n = qname.lower()
    q = query.lower()
    if n == q:
        return 90
    if n.endswith("." + q):
        return 65
    if q in n:
        return 40
    return 0


def score_path(rel_path: str, query: str) -> int:
    n = rel_path.lower()
    q = query.lower().replace(" ", "_")
    if q in n:
        return 35
    return 0


def score_docstring(doc: str | None, query: str) -> int:
    if not doc:
        return 0
    return 20 if query.lower() in doc.lower() else 0
