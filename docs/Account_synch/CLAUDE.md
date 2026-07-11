# DepthFusion

Cross-session memory for Claude Code. Python 3.10+. Tiered retrieval: BM25 → Haiku reranker → ChromaDB vectors. Knowledge graph entity linking (v0.4.0).

## Commands
```bash
pytest                                       # 328+ tests
pytest --cov=depthfusion                    # coverage
mypy src/                                    # type check
ruff check src/ tests/                       # lint
python -m depthfusion.analyzer.compatibility # C1-C11 check
python -m depthfusion.install.install --mode local
python -m depthfusion.install.migrate        # Tier 1 → Tier 2
```

## Conventions
- Type hints on all public functions. Docstrings on public classes.
- `structlog` for logging — never `print()` in production code.
- All new features behind env-var feature flags. `FLAG=false` must produce identical output to previous version.
- Tests in `tests/test_<package>/`. TDD for core algorithms. Commit format: `feat|fix|test|docs(scope): message`.
- C1-C11 compatibility must stay GREEN. Run before every PR.

## Key env vars
`DEPTHFUSION_MODE` (local|vps), `DEPTHFUSION_GRAPH_ENABLED`, `DEPTHFUSION_HAIKU_ENABLED`, `DEPTHFUSION_API_KEY` (NOT `ANTHROPIC_API_KEY`).

## Before starting a build sprint
Read `docs/depthfusion-mega-prompt.md` for full architecture, constraints, and integration contracts. Read `docs/honest-assessment-2026-03-28.md` for current bottlenecks.

## Current phase
v0.3.1 (scoring fixes + data gap closure) → v0.4.0 (knowledge graph). See `docs/depthfusion-build-plan.md`.
