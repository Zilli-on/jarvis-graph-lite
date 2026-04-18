# jarvis-graph-lite

Repo-local code-intelligence index for Python. Stdlib only,
zero runtime dependencies, Python 3.11+. Target box: Win10,
i5, 8 GB RAM — seconds per query.

Product identity: **no runtime deps, ever**. This is the
zero-dep twin of the meta-workspace's zero-cost policy — here
it is a hard product constraint, not a policy choice.

## Hard rules

1. **Zero runtime dependencies.** `pyproject.toml`
   `dependencies` stays `[]`. `requirements.txt` stays a
   comment file. The stdlib-only promise is what makes the
   tool shippable to any Python 3.11+ box without setup.
2. **277 tests stay green.** Run `python -m unittest discover
   -s tests` before any commit. Local CI runs the same, GH
   Actions runs it on 3.11 + 3.12 on every PR.
3. **Test follows source.** Every new public symbol in `src/`
   ships with a matching test in `tests/` in the same commit.
   The tool's own `find_coverage_gaps` would catch the drift.
4. **Windows-compatible first, OS-independent everywhere.**
   Primary box is Win10. `.bat` scripts for local use, `bash`
   (git-bash compatible) for CI. No POSIX-only shell features
   in anything that ships.
5. **No fake certainty.** Before claiming "X works", verify.
   Use the `/verify-claim` user-scope skill. Honest
   `Blocked: X` beats a silent `Done`.
6. **Branch isolation.** Non-trivial work on `feat/*`,
   `fix/*`, `refactor/*`. `main` stays stable.
7. **MIT-compatible licences only.** Upstream is MIT. Any
   dev-tooling dep (e.g. `ruff`) must be MIT / Apache-2.0 /
   BSD / public-domain. Dev-tooling deps do NOT go into
   `pyproject.toml` — they live in `scripts/ci-local.sh` and
   `.github/workflows/ci.yml` only.

## Where things go

| Need | Goes to |
|---|---|
| Broadly applicable rule | this file |
| Behaviour contract | `AGENTS.md` |
| Deterministic automation | `.claude/settings.json` hooks |
| Failure patterns | `failures/NNN-*.md` |
| Release notes | `CHANGELOG.md` |
| Architecture | `docs/architecture.md` |
| Self-measurements | `HEALTH_REPORT_*.md` |

## Inherited skills (user-scope)

`/verify-claim`, `/zero-cost-check`, `/session-summary` live
at `~/.claude/skills/` and are available here without a local
`SKILL.md`. Re-provision with
`bash ../../Users/Fabien/Documents/Arbeit/CLAUDE.MD/scripts/provision-user-skills.sh`
(or from the workspace directly) if they disappear on a
fresh machine.
