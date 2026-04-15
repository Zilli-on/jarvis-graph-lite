# Launch Post Drafts — jarvis-graph-lite v0.12.4

Drafts for the three surfaces Fabi can decide to post on. Each is
tuned to the norms of the target community. Do NOT post verbatim —
read once, tweak tone where it reads too marketing-y, remove any
claim you can't defend in a follow-up comment.

All three posts are **zero-cost-friendly**: they link to the free
PyPI package + MIT-licenced GitHub repo, no paywalled tier exists.

---

## 1 — Hacker News (Show HN)

**Title (keep under 80 chars, no emoji, no exclamation):**

```
Show HN: jarvis-graph-lite – local code intelligence for Python, stdlib only
```

**Body (plain text, short paragraphs, no marketing speak):**

```
jarvis-graph-lite is a small CLI I built for my own use. It answers
seventeen questions about a Python repo — find_dead_code,
find_coverage_gaps, find_circular_deps, refactor_priority,
find_todo_comments, health_report, etc. — without an LLM, without
embeddings, without a daemon.

Stdlib only. SQLite-backed AST index. Runs on a 9-year-old i5 with
8 GB RAM in a few seconds. No network calls, no phone-home, MIT.

Why: every "code intelligence" tool I tried either embedded
everything (slow, hungry, broke on weak hardware) or ran as a
daemon with a UI I didn't need. I wanted the smallest thing that
was still useful. This is it.

Typical use: index the repo once (`jarvis-graph index .`), then ask
questions:
  jarvis-graph find_coverage_gaps
  jarvis-graph refactor_priority
  jarvis-graph health_report --baseline prev.json

The refactor_priority meta-engine composes complexity, size, test
coverage, and caller count; a weight_factor suppresses
trivial-but-popular helpers so only non-trivial risky-to-touch
code surfaces.

Coverage-gap detection does multi-source forward BFS from every test
entry point and flags the highest-complexity unreached code first.

TODO/FIXME/HACK/BUG detection uses stdlib `tokenize` so it
correctly distinguishes `# TODO` from `x = "# TODO"` and ignores
TODOs inside docstrings. Each hit is cross-referenced with the
complexity of its enclosing function.

277 tests. Pure stdlib, no runtime deps.

GitHub: https://github.com/Zilli-on/jarvis-graph-lite
pip: `pip install jarvis-graph-lite` (pending PyPI upload)

Not trying to replace Sourcegraph or Codacy — those are great for
teams and CI. This is for a solo dev who wants a local, fast,
inspectable index that works offline.

Feedback welcome, especially on the refactor_priority scoring — the
weight_factor tuning has been the hardest part to get right.
```

**Tone notes:**
- No emoji, no exclamation marks, no "awesome", no "🚀", no
  "revolutionary" — HN hates those.
- First-person, honest about what it ISN'T ("not trying to
  replace Sourcegraph…").
- Include concrete example commands.
- Mention the weak-hardware origin story — HN respects constraint-
  driven engineering.
- One soft ask at the end (feedback on tuning) — invites comments.

---

## 2 — /r/Python (reddit)

**Title:**

```
I built a stdlib-only local code-intelligence tool — 17 commands, 277 tests, MIT
```

**Body (Reddit allows more structure):**

```
**jarvis-graph-lite** — a tiny local AST + call-graph index for
Python repos. Asks 17 questions about your code without ever
leaving the machine:

- `find_dead_code` — functions/classes/methods with no callers
- `find_coverage_gaps` — public symbols never reached from any test
  entry point (multi-source forward BFS)
- `find_circular_deps` — import cycles
- `find_complexity` — McCabe cyclomatic, bucketed low → extreme
- `find_long_functions` / `find_god_files` / `find_high_fan_out`
- `find_todo_comments` — uses `tokenize` to ignore TODOs in strings
  and docstrings; each hit scored by enclosing function's
  complexity + LOC
- `refactor_priority` — meta-engine that composes all the above +
  caller count into a single "fix this first" ranking
- `health_report` — one Markdown file with all 10 sections, with
  `--baseline FILE` for drift-since-last-run
- `generate_test_skeleton` — emits a unittest.TestCase stub for an
  untested symbol; closes the "find → start writing the test" loop
- `query` / `context` / `impact` / `find_path` — the basic lookup +
  blast-radius + BFS-to-reach questions

**What it deliberately is NOT:**

- Not an LLM wrapper. No embeddings. No external API calls.
- Not a daemon. Not a language server. One CLI, one SQLite file.
- Not Sourcegraph. Don't use it for a 50-person team's monorepo.

**What it is:**

- Stdlib only. No runtime deps. `pip install jarvis-graph-lite`
  and it just works.
- 277 tests.
- Runs on my 9-year-old i5-6600K + 8 GB RAM in a few seconds.
- MIT. Inspect the whole thing in an afternoon.

Example:

```
jarvis-graph index .
jarvis-graph refactor_priority --top 10
jarvis-graph health_report --out health.md --baseline prev.json
```

Repo: https://github.com/Zilli-on/jarvis-graph-lite

Happy to answer questions about the scoring heuristics or the
stdlib-only constraint. The weight_factor tuning in
refactor_priority was the hardest part.
```

**Tone notes:**
- Bold + bullet structure is fine on reddit.
- Explicitly calls out what it's NOT — reddit values that a lot.
- Command snippets.
- Soft ask at the end.

---

## 3 — X (formerly Twitter)

**Thread, 4 posts, each under 280 chars:**

**Post 1 (the hook):**
```
jarvis-graph-lite v0.12.4 — a tiny local code-intelligence CLI for
Python. 17 commands, stdlib only, 277 tests, MIT. No LLM, no
daemon, no cloud.

Runs on a 9-year-old i5 in a few seconds.

https://github.com/Zilli-on/jarvis-graph-lite
```

**Post 2 (the demo):**
```
It answers questions like:
· find_dead_code
· find_coverage_gaps (multi-source BFS from every test)
· find_circular_deps
· refactor_priority (composite: complexity × size × fan-in)
· find_todo_comments (via tokenize, ignores docstring TODOs)

All local. All fast.
```

**Post 3 (the origin):**
```
Why: every "code intelligence" tool I tried either embedded
everything (too slow on weak hardware) or needed a daemon with a
UI I didn't want.

jarvis-graph-lite is the smallest thing that's still useful. SQLite
+ stdlib AST. Done.
```

**Post 4 (the ask):**
```
Install:
  pip install jarvis-graph-lite

Use:
  jarvis-graph index .
  jarvis-graph refactor_priority

Feedback welcome, especially on the weight_factor tuning in
refactor_priority — that was the hardest part.
```

**Tone notes:**
- X rewards threads over single posts for this kind of content.
- Each post stands alone if someone sees only one.
- Keep under 280 chars including the handle / link.
- No hashtag spam. Maybe `#python` on post 1 if anywhere.

---

## Checklist before posting

- [ ] PyPI upload done (`twine upload dist/jarvis_graph_lite-0.12.4*`).
      The HN / reddit posts both say "pip install" — that must work.
- [ ] README top badge or paragraph mentions PyPI install when live.
- [ ] Sanity-run on a fresh venv:
        pip install jarvis-graph-lite
        jarvis-graph --version
        jarvis-graph --help
- [ ] Screenshot of `health_report` output on a real repo, attached
      to the HN / reddit / X posts where visuals matter.
- [ ] Decide posting order. Suggested: HN first (most scrutiny, best
      feedback on technical claims), then reddit /r/Python the
      following day, then X thread after either gets traction.
- [ ] Be available to respond for the first 2-3 hours on HN — threads
      that get author-engagement-within-the-first-hour rank better.

## What NOT to post

- Don't post on /r/programming — too broad, HN-like without the
  curation.
- Don't post on /r/learnpython — wrong audience.
- Don't cross-post the same text on 3 subreddits — reddit
  anti-spam flags it.
- Don't use any marketing voice ("the best", "revolutionary",
  "game-changer"). Every one of these triggers HN flags.
- Don't mention Claude or AI-assistance in any of the posts. This
  tool is engineered, not generated.

---

## 4 — MCP-angle variant (/r/LocalLLaMA, /r/ClaudeAI)

Per `memory/launch_2026-04-10.md`, the primary strategic bet is the
MCP ecosystem. `jarvis-graph-mcp` (`Zilli-on/jarvis-graph-mcp` v0.1.0)
is a FastMCP wrapper exposing the same 17 tools to Claude Desktop,
Claude Code, Cursor, Windsurf, Cline. These audiences care about
"local + MCP + no cloud" more than generic Python devs.

**Title:**

```
jarvis-graph-mcp — 18 local code-intelligence tools for Claude / Cursor / Cline, zero-cloud
```

**Body:**

```
Small MCP server I built because every "code intelligence" MCP
I tried either embedded everything (slow on my weak hardware) or
phoned home to a cloud backend.

jarvis-graph-mcp is a thin FastMCP wrapper over jarvis-graph-lite
(Python stdlib only, 277 tests, MIT). It exposes 17 local tools to
any MCP client:

- index_repo, query, context, impact, find_path
- find_dead_code, find_coverage_gaps, generate_test_skeleton
- find_circular_deps, find_complexity, find_long_functions
- find_god_files, find_high_fan_out, find_todo_comments
- refactor_priority (composite ranker)
- health_report (one-file project health with baseline-diff mode)
- detect_changes (index-vs-disk drift)

No telemetry. No cloud. No API costs. Your source never leaves
your machine.

Tested with Claude Desktop + Claude Code. Integration guides for
Cursor / Windsurf / Cline in the repo's README (written against
each client's documented MCP config; field reports from real
users welcome).

Install:
  pip install -e git+https://github.com/Zilli-on/jarvis-graph-mcp.git
  (PyPI publish pending)

Then add to your client's MCP config:
  {
    "jarvis-graph": {
      "command": "python",
      "args": ["-m", "jarvis_graph_mcp"]
    }
  }

Repos:
- Server: https://github.com/Zilli-on/jarvis-graph-mcp (v0.1.0)
- Backend: https://github.com/Zilli-on/jarvis-graph-lite (v0.12.4)

Feedback especially welcome on the refactor_priority tool — the
scoring heuristic is the part I'm least sure about.
```

**Tone notes:**
- This audience is hungry for MCP content — state it clearly in
  the title.
- Lead with the *why not cloud* angle (resonates with local-LLM
  subreddits specifically).
- Keep the tool list digestible — don't list all 17, maybe 10.
- Include the exact MCP config snippet. These audiences expect it.

**Where to post:**
- /r/LocalLLaMA — strong match, respects zero-cloud
- /r/ClaudeAI — emerging, good for MCP-specific tools
- Do NOT post on /r/MachineLearning — wrong audience for tooling.

## Posting order recommendation

Given two repos + four surfaces, a sensible sequence:

1. **PyPI publish both packages** (pending Fabi)
2. **HN Show HN** with the lite post (broadest technical
   scrutiny; if lite passes HN scrutiny, mcp version piggybacks
   on the credibility)
3. **/r/Python** same day, lite-focused
4. **/r/LocalLLaMA + /r/ClaudeAI** the next day, mcp-focused
5. **X thread** two days later, short-form summary of HN / reddit
   feedback

Each surface reaches a different audience; posting spaced out
also lets Fabi respond to feedback without context-switching
across platforms in parallel.
