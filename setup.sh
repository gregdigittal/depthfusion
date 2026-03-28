#!/usr/bin/env bash
# DepthFusion setup script — run locally or on VPS
#
# Usage:
#   ./setup.sh --mode local --api-key sk-ant-...
#   ./setup.sh --mode vps   --api-key sk-ant-...
#
# Or without --api-key to be prompted interactively.

set -euo pipefail

# ── helpers ──────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }
step() { echo -e "\n${YELLOW}▶${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── argument parsing ──────────────────────────────────────────────────────────

MODE=""
API_KEY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)    MODE="$2";    shift 2 ;;
        --api-key) API_KEY="$2"; shift 2 ;;
        *)         fail "Unknown argument: $1. Use --mode local|vps [--api-key sk-ant-...]" ;;
    esac
done

[[ -z "$MODE" ]] && fail "Required: --mode local|vps"
[[ "$MODE" != "local" && "$MODE" != "vps" ]] && fail "--mode must be 'local' or 'vps'"

if [[ -z "$API_KEY" && "$MODE" == "vps" ]]; then
    echo -n "Enter ANTHROPIC_API_KEY (sk-ant-...): "
    read -rs API_KEY
    echo
fi

if [[ -n "$API_KEY" && "$MODE" == "local" && -z "${FORCE_API_KEY:-}" ]]; then
    echo -n "Enter ANTHROPIC_API_KEY for local mode (or press Enter to skip): "
    read -rs INPUT_KEY
    echo
    [[ -n "$INPUT_KEY" ]] && API_KEY="$INPUT_KEY"
fi

# ── python check ─────────────────────────────────────────────────────────────

step "Checking Python version"
PYTHON=$(command -v python3 || command -v python || fail "Python 3.10+ required")
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 || ("$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10) ]]; then
    fail "Python 3.10+ required (found $PY_VERSION)"
fi
ok "Python $PY_VERSION"

# ── virtualenv ────────────────────────────────────────────────────────────────

step "Setting up virtualenv"
VENV_DIR="$SCRIPT_DIR/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Created .venv"
else
    ok ".venv already exists"
fi

PY="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# ── install dependencies ──────────────────────────────────────────────────────

step "Installing depthfusion ($MODE mode)"

if [[ "$MODE" == "vps" ]]; then
    "$PIP" install --quiet -e "$SCRIPT_DIR/.[vps-tier2]"
    ok "Installed with [vps-tier2] extras (chromadb)"
else
    "$PIP" install --quiet -e "$SCRIPT_DIR/."
    ok "Installed (local mode — zero optional deps)"
fi

# ── run install script ────────────────────────────────────────────────────────

step "Running DepthFusion installer (--mode $MODE)"
"$PY" -m depthfusion.install.install --mode "$MODE"
ok "DepthFusion installer complete"

# ── MCP server registration ───────────────────────────────────────────────────

step "Registering MCP server with Claude Code"
CLAUDE_BIN=$(command -v claude || true)

if [[ -z "$CLAUDE_BIN" ]]; then
    warn "claude CLI not found — skipping MCP registration"
    warn "Run manually: claude mcp add depthfusion --scope user -- $PY -m depthfusion.mcp.server"
else
    # Remove existing registration if present (idempotent)
    "$CLAUDE_BIN" mcp remove depthfusion --scope user 2>/dev/null || true
    "$CLAUDE_BIN" mcp add depthfusion --scope user -- "$PY" -m depthfusion.mcp.server
    ok "MCP server registered: depthfusion"
fi

# ── environment variables ─────────────────────────────────────────────────────

step "Writing environment variables"

# Detect which shell profile to update
PROFILE=""
if [[ -f "$HOME/.zshrc" ]]; then
    PROFILE="$HOME/.zshrc"
elif [[ -f "$HOME/.bashrc" ]]; then
    PROFILE="$HOME/.bashrc"
elif [[ -f "$HOME/.bash_profile" ]]; then
    PROFILE="$HOME/.bash_profile"
fi

if [[ -z "$PROFILE" ]]; then
    warn "No shell profile found — set env vars manually:"
    echo "  export DEPTHFUSION_MODE=$MODE"
    [[ -n "$API_KEY" ]] && echo "  export ANTHROPIC_API_KEY=$API_KEY"
else
    # Remove any previous depthfusion entries (macOS needs sed -i '', Linux needs sed -i)
    SED_INPLACE=(-i)
    [[ "$(uname)" == "Darwin" ]] && SED_INPLACE=(-i '')
    sed "${SED_INPLACE[@]}" '/# depthfusion/d' "$PROFILE"
    sed "${SED_INPLACE[@]}" '/DEPTHFUSION_MODE/d' "$PROFILE"

    {
        echo "# depthfusion"
        echo "export DEPTHFUSION_MODE=$MODE"
    } >> "$PROFILE"

    ok "DEPTHFUSION_MODE=$MODE written to $PROFILE"

    if [[ -n "$API_KEY" ]]; then
        sed "${SED_INPLACE[@]}" '/ANTHROPIC_API_KEY/d' "$PROFILE"
        echo "export ANTHROPIC_API_KEY=$API_KEY" >> "$PROFILE"
        ok "ANTHROPIC_API_KEY written to $PROFILE"
    else
        warn "No API key set. Reranker will be disabled (local-mode fallback)."
        warn "Add manually: echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> $PROFILE"
    fi
fi

# ── verify ────────────────────────────────────────────────────────────────────

step "Verifying installation"
RESULT=$("$PY" -m depthfusion.mcp.server <<< \
    '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 2>/dev/null)

TOOL_COUNT=$(echo "$RESULT" | "$PY" -c \
    "import sys,json; d=json.load(sys.stdin); print(len(d.get('result',{}).get('tools',[])))" \
    2>/dev/null || echo "0")

if [[ "$TOOL_COUNT" -ge 8 ]]; then
    ok "MCP server responds: $TOOL_COUNT tools available"
else
    warn "MCP server response unexpected (got $TOOL_COUNT tools). Check logs."
fi

# ── summary ───────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}DepthFusion v0.3.0 setup complete${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Mode:    $MODE"
echo "  Python:  $PY"
echo "  API key: $([ -n "$API_KEY" ] && echo "set" || echo "not set (local fallback)")"
[[ -n "$PROFILE" ]] && echo "  Profile: $PROFILE (restart shell or: source $PROFILE)"
echo ""

if [[ "$MODE" == "vps" && -n "$API_KEY" ]]; then
    echo "Tier status:"
    "$PY" -m depthfusion.mcp.server <<< \
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"depthfusion_tier_status","arguments":{}}}' \
        2>/dev/null | "$PY" -c \
        "import sys,json; r=json.load(sys.stdin); t=json.loads(r['result']['content'][0]['text']); print(f\"  Tier:    {t.get('tier')}\"); print(f\"  Corpus:  {t.get('corpus_size')} files\")" \
        2>/dev/null || true
    echo ""
fi

if [[ "$MODE" == "vps" && "$TOOL_COUNT" -ge 8 ]]; then
    echo "Next step: run the migration if you have 500+ session files:"
    echo "  $PY -m depthfusion.install.migrate"
fi
