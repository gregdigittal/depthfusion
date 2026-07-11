#!/usr/bin/env bash
# DepthFusion v0.4.0 — Project Scaffolding Script
# Run this from the repo root to create all new directories, files, and configs
# for the v0.3.1 → v0.4.0 build plan.
#
# Usage: bash depthfusion-scaffold.sh
# Idempotent: safe to run multiple times (mkdir -p, no overwrites)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "=== DepthFusion Scaffolding ==="
echo "Repo root: ${REPO_ROOT}"
echo ""

# ── v0.3.1 Infrastructure ────────────────────────────────────────────────

echo "[1/6] Creating benchmark directory..."
mkdir -p "${REPO_ROOT}/docs/benchmarks"
mkdir -p "${REPO_ROOT}/docs/sessions"

# ── v0.4.0 Graph Module ──────────────────────────────────────────────────

echo "[2/6] Creating graph module structure..."
mkdir -p "${REPO_ROOT}/src/depthfusion/graph"
mkdir -p "${REPO_ROOT}/tests/test_graph"

# Create __init__.py files if they don't exist
for dir in src/depthfusion/graph tests/test_graph; do
    init="${REPO_ROOT}/${dir}/__init__.py"
    if [ ! -f "$init" ]; then
        touch "$init"
        echo "  Created ${dir}/__init__.py"
    fi
done

# Create stub files for graph module (won't overwrite existing)
declare -A GRAPH_STUBS=(
    ["src/depthfusion/graph/types.py"]='"""Graph types: Entity, Edge, GraphScope, TraversalResult."""'
    ["src/depthfusion/graph/extractor.py"]='"""Entity extraction: regex (confidence 1.0) + haiku enrichment (0.70-0.95)."""'
    ["src/depthfusion/graph/linker.py"]='"""Edge linking: co-occurrence, haiku-inferred, temporal proximity."""'
    ["src/depthfusion/graph/store.py"]='"""Graph store backends: JSON (local), SQLite (Tier 1), ChromaDB (Tier 2)."""'
    ["src/depthfusion/graph/traverser.py"]='"""Graph traversal, query expansion, and score boosting."""'
    ["src/depthfusion/graph/scope.py"]='"""Session scope management: per-project, cross-project, custom."""'
)

for filepath in "${!GRAPH_STUBS[@]}"; do
    full="${REPO_ROOT}/${filepath}"
    if [ ! -f "$full" ]; then
        echo "${GRAPH_STUBS[$filepath]}" > "$full"
        echo "  Created ${filepath}"
    else
        echo "  Exists  ${filepath} (skipped)"
    fi
done

# Create stub test files
declare -A TEST_STUBS=(
    ["tests/test_graph/test_types.py"]='"""Tests for graph types."""
import pytest
'
    ["tests/test_graph/test_extractor.py"]='"""Tests for entity extraction."""
import pytest
'
    ["tests/test_graph/test_linker.py"]='"""Tests for edge linking."""
import pytest
'
    ["tests/test_graph/test_store.py"]='"""Tests for graph store backends."""
import pytest
'
    ["tests/test_graph/test_traverser.py"]='"""Tests for graph traversal and query expansion."""
import pytest
'
    ["tests/test_graph/test_scope.py"]='"""Tests for session scope management."""
import pytest
'
    ["tests/test_graph/conftest.py"]='"""Shared fixtures for graph tests."""
import pytest
'
)

for filepath in "${!TEST_STUBS[@]}"; do
    full="${REPO_ROOT}/${filepath}"
    if [ ! -f "$full" ]; then
        echo "${TEST_STUBS[$filepath]}" > "$full"
        echo "  Created ${filepath}"
    else
        echo "  Exists  ${filepath} (skipped)"
    fi
done

# ── CI/CD ─────────────────────────────────────────────────────────────────

echo "[3/6] Creating CI/CD directory..."
mkdir -p "${REPO_ROOT}/.github/workflows"

# ── Hooks ─────────────────────────────────────────────────────────────────

echo "[4/6] Creating hooks directory..."
mkdir -p "${REPO_ROOT}/hooks"

# SessionStart hook template
HOOK_FILE="${REPO_ROOT}/hooks/session-start-depthfusion.sh"
if [ ! -f "$HOOK_FILE" ]; then
    cat > "$HOOK_FILE" << 'HOOKEOF'
#!/usr/bin/env bash
# DepthFusion SessionStart Hook
# Injects git context + DepthFusion recall into new Claude Code sessions.
# Install: Add to ~/.claude/hooks/session-start/
#
# Timeout: 2 seconds max. Degrades gracefully on failure.

set -euo pipefail
TIMEOUT=2

# Gather git context (fast, local operations)
GIT_BRANCH=$(timeout $TIMEOUT git branch --show-current 2>/dev/null || echo "unknown")
GIT_LOG=$(timeout $TIMEOUT git log --oneline -5 2>/dev/null || echo "no git history")
BACKLOG=$(timeout $TIMEOUT head -20 BACKLOG.md 2>/dev/null || echo "no backlog")

# Build auto-query for DepthFusion recall
AUTO_QUERY="Current branch: ${GIT_BRANCH}. Recent commits: ${GIT_LOG}. Active work: ${BACKLOG}"

echo "--- DepthFusion Context ---"
echo "Branch: ${GIT_BRANCH}"
echo "Recent: ${GIT_LOG}"
echo ""

# Call DepthFusion MCP recall (if available)
# This will be wired to depthfusion_recall_relevant via MCP
# Placeholder for MCP integration:
# claude mcp call depthfusion depthfusion_recall_relevant --query "${AUTO_QUERY}" --top_k 5
HOOKEOF
    chmod +x "$HOOK_FILE"
    echo "  Created hooks/session-start-depthfusion.sh"
fi

# PostCompact hook template
HOOK_FILE2="${REPO_ROOT}/hooks/post-compact-depthfusion.sh"
if [ ! -f "$HOOK_FILE2" ]; then
    cat > "$HOOK_FILE2" << 'HOOKEOF'
#!/usr/bin/env bash
# DepthFusion PostCompact Hook
# Triggers auto-learning and entity extraction after Claude Code compaction.
# Install: Add to ~/.claude/hooks/post-compact/

set -euo pipefail

# Find most recent .tmp compaction file
RECENT_TMP=$(ls -t ~/.claude/.tmp-* 2>/dev/null | head -1)

if [ -z "$RECENT_TMP" ]; then
    echo "[DepthFusion] No compaction file found, skipping."
    exit 0
fi

echo "[DepthFusion] Processing compacted session: ${RECENT_TMP}"

# Trigger auto-learn (heuristic extraction — works without API key)
# claude mcp call depthfusion depthfusion_auto_learn

# Trigger session compression (haiku summarization if HAIKU_ENABLED)
# claude mcp call depthfusion depthfusion_compress_session --tmp_file "${RECENT_TMP}"

echo "[DepthFusion] PostCompact processing complete."
HOOKEOF
    chmod +x "$HOOK_FILE2"
    echo "  Created hooks/post-compact-depthfusion.sh"
fi

# ── Config Templates ──────────────────────────────────────────────────────

echo "[5/6] Creating config templates..."

ENV_TEMPLATE="${REPO_ROOT}/.env.example"
if [ ! -f "$ENV_TEMPLATE" ]; then
    cat > "$ENV_TEMPLATE" << 'ENVEOF'
# DepthFusion Environment Configuration
# Copy to ~/.claude/depthfusion.env and adjust as needed.
# WARNING: Do NOT set ANTHROPIC_API_KEY — use DEPTHFUSION_API_KEY instead.

# Install mode: local (zero-dep) or vps (haiku + chromadb)
DEPTHFUSION_MODE=local

# VPS Tier 2 promotion threshold
DEPTHFUSION_TIER_THRESHOLD=500
DEPTHFUSION_TIER_AUTOPROMOTE=true

# Feature flags
DEPTHFUSION_FUSION_ENABLED=true
DEPTHFUSION_SESSION_ENABLED=true
DEPTHFUSION_RLM_ENABLED=true
DEPTHFUSION_ROUTER_ENABLED=true
DEPTHFUSION_METRICS_ENABLED=true

# Opt-in: Haiku API for summarization/reranking (VPS only)
DEPTHFUSION_HAIKU_ENABLED=false
# DEPTHFUSION_API_KEY=sk-ant-your-key-here

# v0.4.0: Knowledge graph (default off until validated)
DEPTHFUSION_GRAPH_ENABLED=false
ENVEOF
    echo "  Created .env.example"
fi

# ── CLAUDE.md ─────────────────────────────────────────────────────────────

echo "[6/6] Creating CLAUDE.md (project context for Claude Code)..."

CLAUDE_MD="${REPO_ROOT}/CLAUDE.md"
if [ ! -f "$CLAUDE_MD" ]; then
    cat > "$CLAUDE_MD" << 'CLAUDEEOF'
# DepthFusion — Claude Code Project Context

## Quick Reference
- **Language**: Python 3.10+ | **Framework**: None (pure Python + numpy/pyyaml/structlog)
- **Tests**: `pytest` (328+ tests) | **Lint**: `ruff check src/ tests/` | **Types**: `mypy src/`
- **Install**: `pip install -e .` (local) or `pip install -e ".[vps-tier2]"` (full)

## Architecture
Cross-session memory for Claude Code. Tiered retrieval: BM25 → Haiku reranker → ChromaDB vectors.
Knowledge graph entity linking (v0.4.0). GEPA self-improvement loops (future).

## Commands
```bash
pytest                                      # run all tests
pytest --cov=depthfusion                   # with coverage
mypy src/                                   # type check
ruff check src/ tests/                      # lint
python -m depthfusion.analyzer.compatibility # C1-C11 check
python -m depthfusion.install.install --mode local  # install
python -m depthfusion.install.migrate       # Tier 1 → Tier 2
```

## Conventions
- Type hints on all public functions
- Docstrings on all public classes/functions
- Structured logging via structlog (no print())
- Feature flags for all new functionality
- Tests co-located in tests/test_<package>/
- Commits: conventional format (feat/fix/test/docs)

## Active Build Plan
See `docs/` for specs and plans. Current phase: v0.3.1 (scoring fixes + data gap) → v0.4.0 (knowledge graph).
CLAUDEEOF
    echo "  Created CLAUDE.md"
fi

echo ""
echo "=== Scaffolding Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy .github/workflows/depthfusion-ci.yml from the build kit"
echo "  2. Review and customize hooks/ templates"
echo "  3. Copy .env.example to ~/.claude/depthfusion.env"
echo "  4. Begin Sprint 1 (BM25 norm + snippets + RRF wiring)"
echo ""
