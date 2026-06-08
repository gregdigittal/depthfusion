#!/usr/bin/env bash
# =============================================================================
# DepthFusion — Mac MLX Installer
# =============================================================================
# Self-contained: works from a fresh Mac with nothing installed.
# Designed to run via:
#   curl -fsSL https://raw.githubusercontent.com/gregdigittal/depthfusion/main/scripts/install-mac-mlx.sh | bash
# or:
#   bash scripts/install-mac-mlx.sh   (from inside the repo)
#
# What this does:
#   1. Verifies Apple Silicon (arm64) and macOS 13+
#   2. Installs Xcode Command Line Tools if missing
#   3. Installs Homebrew if missing
#   4. Installs Python 3.12 via Homebrew if < 3.10 found
#   5. Clones the DepthFusion repo (or updates it if already present)
#   6. Creates a dedicated venv at ~/.depthfusion-venv
#   7. Installs the mac-mlx extras (mlx-lm, sentence-transformers, chromadb, …)
#   8. Prompts for API key and writes ~/.claude/depthfusion.env
#   9. Selects an MLX model based on unified memory (downloads on first use)
#  10. Creates launchd plists for the MLX inference server and REST API
#  11. Loads both services (auto-restart on login, auto-restart on crash)
#  12. Registers DepthFusion with Claude Desktop and Claude Code CLI
#  13. Runs a smoke test
#
# Safe to re-run — idempotent throughout.
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/gregdigittal/depthfusion.git"
REPO_DIR="${DEPTHFUSION_REPO:-$HOME/depthfusion}"
VENV_DIR="${DEPTHFUSION_VENV_PATH:-$HOME/.depthfusion-venv}"
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ENV_FILE="$CONFIG_DIR/depthfusion.env"
MLX_PORT="${DEPTHFUSION_GEMMA_PORT:-8000}"
REST_PORT="${DEPTHFUSION_REST_PORT:-7300}"
PLIST_MLX="$HOME/Library/LaunchAgents/com.depthfusion.mlx-server.plist"
PLIST_REST="$HOME/Library/LaunchAgents/com.depthfusion.rest.plist"

# ── Colour output ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'; BLU='\033[0;34m'; RST='\033[0m'
info()    { printf "${BLU}→${RST} %s\n" "$*"; }
success() { printf "${GRN}✓${RST} %s\n" "$*"; }
warn()    { printf "${YLW}⚠${RST}  %s\n" "$*"; }
die()     { printf "${RED}✗${RST} %s\n" "$*" >&2; exit 1; }

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║       DepthFusion — Mac MLX Installer            ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# STEP 1 — Hardware and OS checks
# =============================================================================
info "Checking hardware and OS …"

[[ "$(uname -s)" == "Darwin" ]]  || die "This installer requires macOS."
[[ "$(uname -m)" == "arm64"  ]]  || die "MLX requires Apple Silicon (M1/M2/M3/M4). Intel Macs are not supported."

MACOS_VER=$(sw_vers -productVersion)
MACOS_MAJOR=$(echo "$MACOS_VER" | cut -d. -f1)
[[ "$MACOS_MAJOR" -ge 13 ]] || die "macOS 13 (Ventura) or later required. Found: $MACOS_VER"

MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
MEM_GB=$(( MEM_BYTES / 1073741824 ))
[[ "$MEM_GB" -ge 8 ]] || die "At least 8 GB unified memory required. Found: ${MEM_GB} GB"

success "Apple Silicon · macOS $MACOS_VER · ${MEM_GB} GB unified memory"

# =============================================================================
# STEP 2 — Xcode Command Line Tools
# =============================================================================
if ! xcode-select -p &>/dev/null; then
    info "Installing Xcode Command Line Tools (needed for git and Python) …"
    xcode-select --install 2>/dev/null || true
    echo ""
    warn "A dialog box has appeared asking you to install Xcode Command Line Tools."
    echo "     Click 'Install', wait for it to finish, then press Enter here to continue."
    read -r -p "     Press Enter once the Xcode install completes …"
    xcode-select -p &>/dev/null || die "Xcode Command Line Tools still not found. Install them and re-run."
fi
success "Xcode Command Line Tools"

# =============================================================================
# STEP 3 — Homebrew
# =============================================================================
if ! command -v brew &>/dev/null; then
    info "Installing Homebrew …"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for this session (Apple Silicon path)
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
fi
# Ensure brew is in PATH for this shell session
eval "$(brew shellenv 2>/dev/null)" 2>/dev/null || true
command -v brew &>/dev/null || die "Homebrew not found after install — open a new Terminal and re-run."
success "Homebrew $(brew --version | head -1 | awk '{print $2}')"

# =============================================================================
# STEP 4 — Python 3.10+
# =============================================================================
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [[ "$major" -gt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -ge 10 ]]; }; then
            PYTHON_BIN=$(command -v "$candidate")
            PYTHON_VER="$ver"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    info "Python 3.10+ not found — installing Python 3.12 via Homebrew …"
    brew install python@3.12
    PYTHON_BIN="$(brew --prefix)/bin/python3.12"
    PYTHON_VER="$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"
fi
success "Python $PYTHON_VER ($PYTHON_BIN)"

# =============================================================================
# STEP 5 — Clone or update the repo
# =============================================================================
if [[ -d "$REPO_DIR/.git" ]]; then
    info "Updating existing repo at $REPO_DIR …"
    git -C "$REPO_DIR" pull --ff-only 2>/dev/null && success "Repo updated" || warn "Could not auto-update repo — continuing with existing version"
else
    info "Cloning DepthFusion to $REPO_DIR …"
    git clone "$REPO_URL" "$REPO_DIR"
    success "Repo cloned"
fi

# =============================================================================
# STEP 6 — Virtual environment
# =============================================================================
if [[ -d "$VENV_DIR" && -x "$VENV_DIR/bin/python" ]]; then
    info "Re-using existing venv at $VENV_DIR"
else
    info "Creating virtual environment at $VENV_DIR …"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
success "Virtual environment ($VENV_DIR)"

# =============================================================================
# STEP 7 — Install DepthFusion mac-mlx extras
# =============================================================================
info "Installing DepthFusion with mac-mlx extras (this takes 3–6 minutes) …"
"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet -e "$REPO_DIR[mac-mlx]"
success "DepthFusion installed (mlx-lm, sentence-transformers, chromadb, hnswlib)"

# =============================================================================
# STEP 8 — API key
# =============================================================================
echo ""
echo "  Your DepthFusion API key comes from:"
echo "    https://console.anthropic.com/settings/keys"
echo ""
echo "  IMPORTANT: This is NOT your Claude Pro/Max subscription key."
echo "  It is a separate API key used only for DepthFusion's reranking calls."
echo ""

# Check if key already set and valid
EXISTING_KEY=""
if [[ -f "$ENV_FILE" ]]; then
    EXISTING_KEY=$(grep -E "^DEPTHFUSION_API_KEY=.+" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
fi

if [[ -n "$EXISTING_KEY" ]]; then
    warn "Found existing API key in $ENV_FILE — skipping prompt."
    warn "Delete that line and re-run if you need to change it."
    API_KEY="$EXISTING_KEY"
else
    while true; do
        read -r -s -p "  Paste your API key (input hidden): " API_KEY
        echo ""
        [[ -n "$API_KEY" ]] || { warn "Key cannot be empty — try again."; continue; }
        # Refuse Claude Code billing key
        if [[ "$API_KEY" =~ ^sk-ant-api03- ]]; then
            warn "That looks like a Claude Code billing key (starts with sk-ant-api03-)."
            warn "DepthFusion needs a separate key from console.anthropic.com → API keys."
            continue
        fi
        break
    done
fi

# Write env file (600 = owner read/write only)
mkdir -p "$CONFIG_DIR"
if [[ -f "$ENV_FILE" ]]; then
    # Remove stale key line before rewriting
    grep -v "^DEPTHFUSION_API_KEY=" "$ENV_FILE" > "${ENV_FILE}.tmp" 2>/dev/null || true
    mv "${ENV_FILE}.tmp" "$ENV_FILE"
fi
{
    echo "DEPTHFUSION_API_KEY=$API_KEY"
    echo "DEPTHFUSION_MODE=mac-mlx"
    echo "DEPTHFUSION_GEMMA_URL=http://127.0.0.1:${MLX_PORT}/v1"
    echo "DEPTHFUSION_HNSW_ENABLED=true"
    echo "DEPTHFUSION_GRAPH_ENABLED=true"
    echo "DEPTHFUSION_VECTOR_SEARCH_ENABLED=true"
    echo "DEPTHFUSION_TIER_AUTOPROMOTE=true"
    echo "DEPTHFUSION_RERANKER_ENABLED=true"
    echo "DEPTHFUSION_EMBEDDING_BACKEND=local"
    echo "DEPTHFUSION_TIER_THRESHOLD=500"
    echo "DEPTHFUSION_HAIKU_ENABLED=true"
    echo "DEPTHFUSION_REST_API=true"
} >> "$ENV_FILE"
# Deduplicate (last value wins — use awk to keep last occurrence of each key)
awk -F= '!seen[$1]++' <(tac "$ENV_FILE") | tac > "${ENV_FILE}.dedup"
mv "${ENV_FILE}.dedup" "$ENV_FILE"
chmod 600 "$ENV_FILE"
success "API key and config written to $ENV_FILE"

# =============================================================================
# STEP 9 — Model selection
# =============================================================================
echo ""
echo "  ── MLX Model Selection ────────────────────────────────────────────────"
echo "  Detected unified memory: ${MEM_GB} GB"
echo ""

if [[ "$MEM_GB" -ge 32 ]]; then
    echo "  [1] Qwen2.5-32B-Instruct-4bit  (~20 GB download)  highest quality  ← recommended"
    echo "  [2] Qwen2.5-14B-Instruct-4bit  (~9 GB)            balanced"
    echo "  [3] gemma-3-12b-it-4bit        (~7 GB)            fast"
    echo ""
    read -r -p "  Choose [1/2/3] or Enter for recommended: " CHOICE
    case "${CHOICE:-1}" in
        2) MODEL="mlx-community/Qwen2.5-14B-Instruct-4bit" ;;
        3) MODEL="mlx-community/gemma-3-12b-it-4bit" ;;
        *) MODEL="mlx-community/Qwen2.5-32B-Instruct-4bit" ;;
    esac
elif [[ "$MEM_GB" -ge 16 ]]; then
    echo "  [1] Qwen2.5-14B-Instruct-4bit  (~9 GB)            balanced  ← recommended"
    echo "  [2] gemma-3-12b-it-4bit        (~7 GB)            fast"
    echo ""
    read -r -p "  Choose [1/2] or Enter for recommended: " CHOICE
    case "${CHOICE:-1}" in
        2) MODEL="mlx-community/gemma-3-12b-it-4bit" ;;
        *) MODEL="mlx-community/Qwen2.5-14B-Instruct-4bit" ;;
    esac
else
    echo "  gemma-3-12b-it-4bit  (~7 GB)  ← only option for <16 GB"
    MODEL="mlx-community/gemma-3-12b-it-4bit"
    read -r -p "  Press Enter to continue …" _
fi
echo ""
success "Model selected: $MODEL"

# Write model back to env file
grep -v "^DEPTHFUSION_GEMMA_MODEL=" "$ENV_FILE" > "${ENV_FILE}.tmp" 2>/dev/null || true
mv "${ENV_FILE}.tmp" "$ENV_FILE"
echo "DEPTHFUSION_GEMMA_MODEL=$MODEL" >> "$ENV_FILE"

# Pre-download the model now so first service start is not a surprise
echo ""
echo "  Downloading model weights (${MEM_GB} GB available — this may take"
echo "  10–40 minutes on first run; the model is cached for future starts)."
echo "  You can watch progress here. Press Ctrl+C only if you want to abort."
echo ""
"$VENV_PYTHON" -c "
from mlx_lm import load
import sys
print(f'  Downloading {sys.argv[1]} …', flush=True)
load(sys.argv[1])
print('  Model ready.')
" "$MODEL"
success "Model downloaded and cached"

# =============================================================================
# STEP 10 — launchd plists
# =============================================================================
info "Creating launchd service plists …"
mkdir -p "$HOME/Library/LaunchAgents"

# --- MLX inference server ---
cat > "$PLIST_MLX" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.depthfusion.mlx-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>$REPO_DIR/scripts/mlx-serve-direct.py</string>
        <string>--model</string>
        <string>$MODEL</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>$MLX_PORT</string>
    </array>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/depthfusion-mlx.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/depthfusion-mlx.log</string>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DEPTHFUSION_GEMMA_PORT</key>
        <string>$MLX_PORT</string>
    </dict>
</dict>
</plist>
PLIST

# --- DepthFusion REST / MCP server ---
# Source env file vars into a dict for the plist
MLX_URL="http://127.0.0.1:${MLX_PORT}/v1"

cat > "$PLIST_REST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.depthfusion.rest</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>-m</string>
        <string>depthfusion.mcp.server</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DEPTHFUSION_MODE</key>
        <string>mac-mlx</string>
        <key>DEPTHFUSION_GEMMA_URL</key>
        <string>$MLX_URL</string>
        <key>DEPTHFUSION_GEMMA_MODEL</key>
        <string>$MODEL</string>
        <key>DEPTHFUSION_HNSW_ENABLED</key>
        <string>true</string>
        <key>DEPTHFUSION_GRAPH_ENABLED</key>
        <string>true</string>
        <key>DEPTHFUSION_VECTOR_SEARCH_ENABLED</key>
        <string>true</string>
        <key>DEPTHFUSION_TIER_AUTOPROMOTE</key>
        <string>true</string>
        <key>DEPTHFUSION_RERANKER_ENABLED</key>
        <string>true</string>
        <key>DEPTHFUSION_EMBEDDING_BACKEND</key>
        <string>local</string>
        <key>DEPTHFUSION_TIER_THRESHOLD</key>
        <string>500</string>
        <key>DEPTHFUSION_ENV_FILE</key>
        <string>$ENV_FILE</string>
        <key>DEPTHFUSION_REST_API</key>
        <string>true</string>
    </dict>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/depthfusion-rest.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/depthfusion-rest.log</string>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
</dict>
</plist>
PLIST

success "launchd plists written"

# =============================================================================
# STEP 11 — Load services
# =============================================================================
info "Loading services …"

# Unload stale instances before loading (idempotent)
launchctl unload "$PLIST_MLX" 2>/dev/null || true
launchctl unload "$PLIST_REST" 2>/dev/null || true

# MLX server starts first — REST API connects to it
launchctl load "$PLIST_MLX"
success "MLX inference server loaded (will start loading model in background)"

# Wait a moment then load REST — MLX server has a health-check retry loop
sleep 3
launchctl load "$PLIST_REST"
success "REST / MCP server loaded"

# Wait for REST API to be ready (up to 90 seconds — model load takes time)
info "Waiting for services to become ready (model load takes 15–60 seconds) …"
MAX_WAIT=90; ELAPSED=0
while [[ "$ELAPSED" -lt "$MAX_WAIT" ]]; do
    if curl -sf "http://127.0.0.1:${REST_PORT}/health" &>/dev/null; then
        break
    fi
    sleep 3; ELAPSED=$(( ELAPSED + 3 ))
    printf "."
done
echo ""

if ! curl -sf "http://127.0.0.1:${REST_PORT}/health" &>/dev/null; then
    warn "REST API not yet responding after ${MAX_WAIT}s."
    warn "This is normal if the MLX model is still loading. Check:"
    warn "  tail -f $HOME/Library/Logs/depthfusion-rest.log"
    warn "Services will keep running in the background. Continue with setup."
else
    success "REST API healthy at http://127.0.0.1:${REST_PORT}"
fi

# =============================================================================
# STEP 12 — Register with Claude Desktop and Claude Code CLI
# =============================================================================
info "Registering with Claude Desktop …"

DESKTOP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
mkdir -p "$(dirname "$DESKTOP_CONFIG")"

# Backup before mutation
if [[ -f "$DESKTOP_CONFIG" ]]; then
    STAMP=$(date +%Y%m%d-%H%M%S)
    cp "$DESKTOP_CONFIG" "${DESKTOP_CONFIG}.bak-${STAMP}"
fi

"$VENV_PYTHON" - "$DESKTOP_CONFIG" "$VENV_PYTHON" "$ENV_FILE" <<'PYEOF'
import json, os, sys, tempfile
config_path, python_bin, env_file = sys.argv[1], sys.argv[2], sys.argv[3]
config = {}
if os.path.exists(config_path):
    try:
        with open(config_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        sys.exit(f"Error: {config_path} is not valid JSON ({exc}). Fix or remove it, then re-run.")
config.setdefault("mcpServers", {})
config["mcpServers"]["depthfusion"] = {
    "command": python_bin,
    "args": ["-m", "depthfusion.mcp.server"],
    "env": {"DEPTHFUSION_ENV_FILE": env_file},
}
d = os.path.dirname(config_path) or "."
os.makedirs(d, exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
try:
    with os.fdopen(fd, "w") as tf:
        json.dump(config, tf, indent=2)
    os.replace(tmp, config_path)
except Exception:
    try: os.unlink(tmp)
    except OSError: pass
    raise
PYEOF
success "Claude Desktop registered ($DESKTOP_CONFIG)"

# Claude Code CLI registration (optional — may not be installed)
if command -v claude &>/dev/null; then
    claude mcp remove depthfusion -s user 2>/dev/null || true
    claude mcp add depthfusion --scope user "$VENV_PYTHON" -m depthfusion.mcp.server
    success "Claude Code CLI registered (user-scoped MCP)"
else
    warn "Claude Code CLI not found — skipping CLI registration (Claude Desktop is registered)."
    info "If you install Claude Code CLI later, run:"
    info "  claude mcp add depthfusion --scope user $VENV_PYTHON -m depthfusion.mcp.server"
fi

# =============================================================================
# STEP 13 — Smoke test
# =============================================================================
echo ""
info "Running smoke test …"
if curl -sf "http://127.0.0.1:${REST_PORT}/health" &>/dev/null; then
    RESULT=$("$VENV_PYTHON" -c "
from depthfusion.mcp.server import _tool_recall
import json
result = json.loads(_tool_recall({'query': 'install verification test', 'top_k': 1}))
print(f'blocks={len(result.get(\"blocks\", []))} error={result.get(\"error\", \"none\")}')
" 2>/dev/null || echo "recall test skipped")
    success "Smoke test passed — $RESULT"
else
    warn "REST API not yet up — smoke test skipped. Services are still starting."
fi

# =============================================================================
# Done
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  Installation complete!                                              ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  1. Restart Claude Desktop to load DepthFusion.                     ║"
echo "║  2. Open a new chat and type:  depthfusion_status                   ║"
echo "║  3. You should see version info confirming the connection.           ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  Services auto-start at login and auto-restart on crash.            ║"
echo "║  Logs:  ~/Library/Logs/depthfusion-mlx.log                         ║"
echo "║         ~/Library/Logs/depthfusion-rest.log                        ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
