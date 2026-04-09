"""In-memory dataclasses used by the parser → indexer pipeline.

Mirrors the SQLite schema but without IDs (those are assigned at insert time).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedSymbol:
    name: str
    qualified_name: str
    kind: str  # 'function' | 'class' | 'method' | 'constant'
    lineno: int
    end_lineno: int | None
    col: int
    docstring: str | None
    signature: str | None
    is_private: int  # 0/1
    parent_qname: str | None  # for methods, the parent class qname


@dataclass
class ParsedImport:
    imported_module: str  # e.g. "voie.parsers.terms_parser"
    imported_name: str | None  # e.g. "parse_terms" for `from X import Y`
    alias: str | None
    lineno: int


@dataclass
class ParsedCall:
    caller_qname: str  # qualified name of the function/method making the call
    callee_name: str  # raw textual reference (Name or dotted Attribute)
    lineno: int


@dataclass
class ParsedFile:
    rel_path: str
    abs_path: str
    module_path: str
    sha256: str
    size_bytes: int
    mtime: int
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    calls: list[ParsedCall] = field(default_factory=list)
    parse_error: str | None = None
