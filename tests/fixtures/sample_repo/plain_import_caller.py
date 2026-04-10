"""Exercises the `import X; X.func()` resolution path.

Pre-v0.12.3 the resolver's path (a) used a broken `substr` formula that
computed `substr(callee, num_dots + 1)` (chopping chars from the FRONT),
so `helpers.format_greeting` never resolved even though the import edge
was fine. This fixture exists so that any future regression of that
specific bug fails the `PlainImportCallResolutionTests` unit test.

Two patterns to cover:
  1. Plain `import X` + `X.func()`                → path (a) single-dot
  2. Aliased `import X as Y` + `Y.func()`         → path (a) with alias
"""

from __future__ import annotations

import helpers                # pattern 1: plain import
import helpers as h_alias     # pattern 2: aliased import


def call_plain() -> str:
    return helpers.format_greeting("plain")


def call_aliased() -> str:
    return h_alias.format_greeting("aliased")


# Top-level executions so both callers are anchored from the <module> symbol.
_PLAIN = call_plain()
_ALIAS = call_aliased()
