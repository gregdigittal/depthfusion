# DepthFusion Release Process

## Pre-Release Checklist

1. All tests pass:
   ```bash
   .venv/bin/python -m pytest --tb=short -q
   ```
2. Type checking clean:
   ```bash
   .venv/bin/python -m mypy src/
   ```
3. Linting clean:
   ```bash
   .venv/bin/python -m ruff check src/ tests/
   ```
4. C1-C11 compatibility GREEN:
   ```bash
   .venv/bin/python -m depthfusion.analyzer.compatibility
   ```
5. README.md updated (version table, MCP tools table, feature flags)
6. BACKLOG.md updated (relevant items marked [x])
7. CIQS benchmark runs completed (3 runs, documented in docs/benchmarks/)

## Tagging

```bash
git tag -a v{VERSION} -m "Release v{VERSION}: {one-line summary}"
git push origin v{VERSION}
```

## Version Locations

Update version in these files:
- `src/depthfusion/mcp/server.py` (serverInfo.version in _process_request)
- `pyproject.toml` (project.version)
- `README.md` (version table header)

## Post-Release

- Write discovery file: `~/.claude/shared/discoveries/{date}-depthfusion-v{VERSION}-release.md`
- Update BACKLOG.md DoD section to [x]
- Run `/learn` to persist release context
