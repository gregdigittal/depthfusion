# DepthFusion — Agent Build Protocol

## Identity
You are building DepthFusion for Greg Morris. Greg is a pro coder — no over-explanation, no filler. He runs parallel Claude Code sessions via tmux + Git worktrees on Hetzner VPS, orchestrated by Ruflo v3.5.

## Execution Protocol

### Phase 0: Orient (every session)
```bash
git log --oneline -10
git status && git diff --stat
```
If starting a build sprint (not a quick fix), also read:
- `docs/depthfusion-mega-prompt.md` — full architecture + integration contracts
- `docs/depthfusion-build-plan.md` — current task breakdown
- `docs/honest-assessment-2026-03-28.md` — known bottlenecks

### Phase 1: Plan
Break task into atomic units (max 2h each). Define input/output/test/rollback for each. **Present plan. Wait for confirmation.**

### Phase 2: Build
- TDD for core algorithms (scoring, fusion, graph). Test-after acceptable for plumbing.
- Commit after each passing unit. Format: `feat|fix|test|docs(scope): message`
- Hit a blocker → stop and report. Do not hack around it.

### Phase 3: Verify
```bash
pytest && mypy src/ && ruff check src/ tests/ && python -m depthfusion.analyzer.compatibility
```
All must pass before committing.

### Phase 4: Document
Update CLAUDE.md if commands/conventions changed. Update README.md if MCP tools/flags/install steps changed.

## Quality Gates
| Gate | Criteria |
|------|----------|
| Tests | ≥80% coverage on new code. 100% on scoring/fusion/graph core. |
| Types | `mypy src/` clean. No `# type: ignore` without justification. |
| Lint | `ruff check` zero warnings. |
| Compat | C1-C11 all GREEN. |
| Flags | `FEATURE=false` → identical output to previous version. |
| No secrets | No API keys in code. Use `DEPTHFUSION_API_KEY`, never `ANTHROPIC_API_KEY`. |
| Logging | `structlog` only. No PII. No API keys at any log level. |

## Anti-Patterns
- No bare `except:`. No swallowed errors. No `print()` in production.
- No new base dependencies (local mode stays zero-dep beyond numpy/pyyaml/structlog).
- No modifications to `~/.claude/settings.json` or `~/.claude/commands/`.
- GEPA evolution never synchronous in request path. Haiku calls never in local mode hot path.
- RRF/Pareto selection never replaced with single-objective sorting.
