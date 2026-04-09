# Rollback

`jarvis-graph-lite` writes to exactly two kinds of locations. To remove every byte it has produced, delete both.

## 1. The tool itself

```
C:\JARVIS\tools\jarvis-graph-lite\
```

Delete the directory. There is no installer, no service, no scheduled task, no registry entry, no global pip dependency.

If you ran the optional editable install (`pip install -e ...`), uninstall it too:

```bat
C:\JARVIS\.venv\Scripts\python.exe -m pip uninstall jarvis-graph-lite
```

## 2. Per-repo data

For every repo you ran `jarvis-graph index` against, the tool created a single hidden folder:

```
<repo>/.jarvis_graph/
```

Delete that folder. It contains:

- `config.json` — repo metadata
- `index.db` — the SQLite index (and any `index.db-wal` / `index.db-shm` companion files when WAL is mid-checkpoint)
- `logs/operations.log`
- `summaries/repo_summary.json` (only if you ran `jarvis-graph summary`)
- `cache/` (reserved — currently unused)

To find every repo that has one:

```bat
where /R C:\ .jarvis_graph
```

(or use Everything / `fd` / your file manager).

## What is **not** modified

For complete confidence: this tool never touches any of the following. If anything in this list changed, it was not `jarvis-graph-lite`.

- `%USERPROFILE%`, `%APPDATA%`, `%LOCALAPPDATA%`
- `C:\Windows\` or any system path
- The Windows registry
- Environment variables
- Scheduled tasks
- Services
- Hooks (Claude Code, git, pre-commit, etc.)
- The contents of any `*.py` file in the indexed repo — the tool reads, never writes
- `pyproject.toml` / `requirements.txt` of the indexed repo
- Global Python `site-packages` (unless you opted into the editable install above)

## Sanity check after deletion

```bat
:: should print nothing
where jarvis-graph

:: should fail with "No module named jarvis_graph"
C:\JARVIS\.venv\Scripts\python.exe -c "import jarvis_graph"
```

If both succeed, you're clean.
