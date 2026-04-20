# AGENTS.md — Behaviour contract for Claude Code in jarvis-graph-lite

See `CLAUDE.md` for hard rules. This file is the **behaviour
contract** — what Claude does, doesn't do, and how it commits.

## Role

Claude Code is the **primary AI engineer** for this project.
Maintains stdlib purity, keeps the 277-test suite green, and
ships incremental capability without expanding the dependency
footprint.

## Rules

1. **Plan before editing.** Read relevant code. Dogfood the
   tool: `jarvis-graph query <token>` to locate symbols,
   `jarvis-graph context <symbol>` to understand a node's role.
2. **Never run destructive commands without explicit
   confirmation.** (`git reset --hard`, `git push --force`,
   `rm -rf`, drop/truncate.) A hook blocks the worst offenders.
3. **Branch isolation** for non-trivial work: `feat/<name>`,
   `fix/<name>`, `refactor/<name>`. `main` stays stable.
4. **After edits, run tests.** `python -m unittest discover
   -s tests` must pass. The PostToolUse hook runs
   `ruff format` + `ruff check --fix` on `.py` writes so
   formatting is never the reason a commit is rejected.
5. **Summarise what changed and why.** Every commit gets a
   clear body. Never amend a commit unless the user explicitly
   asks for a `git commit --amend`.
6. **No new runtime deps.** See `CLAUDE.md` rule 1. Dev
   tooling is scoped to `scripts/ci-local.sh` and
   `.github/workflows/ci.yml` — never to `pyproject.toml`.
7. **Verify every claim.** Before asserting "X works", use the
   `/verify-claim` user-scope skill. Evidence in the same
   reply / commit body — not "I checked".
8. **Failure capture.** When something breaks and we fix it,
   check whether the pattern deserves a `failures/NNN-*.md`
   entry. Rule: *if it burned us once and has any chance of
   burning us again, write it down.*

## Commit convention

Format: `<type>: <short description>`.
Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`.

Every commit ends with a `Co-Authored-By:` line naming the
exact model identifier.

## Branch convention

- `main` — stable, reviewed code (future PyPI releases land here)
- `feat/<name>` — feature branches
- `fix/<name>` — bug fixes
- `refactor/<name>` — refactoring

## Automated checks (hooks, `.claude/settings.json`)

| Trigger | Action |
|---|---|
| PostToolUse on `.py` Write/Edit | `ruff format` + `ruff check --fix` |
| PreToolUse on Bash | Block destructive commands |

## Local + remote CI

- `bash scripts/ci-local.sh` — ruff + unittest locally.
  Must pass before opening a PR.
- `.github/workflows/ci.yml` — same checks on every PR
  against `main`, Python 3.11 + 3.12.

## User-scope skills available

- `/verify-claim` — before asserting "X works"
- `/zero-cost-check` — before considering any dep (even
  dev-tooling like a new linter or profiler)
- `/session-summary` — end of a work session

These come from `~/.claude/skills/`. No local `SKILL.md` files
in this repo by design — behaviour lives here, disciplines
live upstream.
