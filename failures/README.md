# failures/

Catalogue of failure patterns we have hit in jarvis-graph-lite:
the root cause, the fix, and the rule added to prevent
recurrence.

This is the project's memory of things that burned us. Not a
bug tracker (use GitHub issues for that). The place where
**patterns** live so the next session — human or AI — does
not step on the same rake.

## Rule for what goes here

*"If it burned me once and has any chance of burning me
again, write it down."*

Do NOT add:
- one-off typos
- transient network hiccups
- library bugs already fixed upstream
- environmental noise

DO add:
- a computation that silently returned wrong results for a
  long time (the canonical case — see 001)
- a test that passed while the code was wrong
- a platform / language / shell gotcha
- a hard rule we violated that the linter doesn't catch

## File format

`failures/NNN-<short-slug>.md` — NNN is monotonic 3 digits,
never reused. Slug is kebab-case, ≤ 6 words.

Each file:

```markdown
# NNN — <title>

## Task
What we were trying to do.

## What failed
The symptom.

## Root cause
The real reason, not the symptom.

## Fix
One-line summary + pointer to the commit.

## Prevention rule
What rule, test, or hook we added so it cannot happen
silently again.
```

## Index

| # | Title | Discovery |
|---|---|---|
| 001 | `import X; X.fn()` call resolution broken since v0.1 | 2026-04-10, v0.12.3 CHANGELOG |
