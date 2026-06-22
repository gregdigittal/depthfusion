#!/usr/bin/env bash
# =============================================================================
# DepthFusion — Solo (Mac) Installer
# =============================================================================
# Solo mode: runs entirely on the user's Mac. MLX serves an OpenAI-compatible
# inference endpoint on 127.0.0.1:8000, and the DepthFusion REST API serves on
# 127.0.0.1:7300. No remote server, no Keycloak — the desktop app supplies the
# Anthropic API key after this script reports the server is up.
#
# Invoked by the Setup Wizard's "Solo" path, or directly:
#   curl -fsSL https://raw.githubusercontent.com/gregdigittal/depthfusion/main/scripts/install-mac-solo.sh | bash
#   bash scripts/install-mac-solo.sh   (from inside the repo)
#
# What this does:
#   1. Verifies Apple Silicon + macOS 13+
#   2. Creates a dedicated venv (uv) and installs mlx-lm + the mac-mlx extras
#   3. Clones / updates the repo and installs DepthFusion (editable)
#   4. Writes two launchd plists — both bound to 127.0.0.1 (loopback only):
#        • com.depthfusion.mlx-serve  → mlx_lm.server   :8000
#        • com.depthfusion.solo       → depthfusion serve :7300
#   5. Loads both services (RunAtLoad + KeepAlive)
#   6. Waits for the REST API health endpoint, then prints a completion box
#
# No secrets are written by this script. The Anthropic API key is supplied by
# the desktop app (stored in the OS keychain via setup_solo_auth), never here.
#
# Safe to re-run — idempotent throughout.
# =============================================================================
set -euo pipefail

REPO_URL="${DEPTHFUSION_REPO_URL:-https://github.com/gregdigittal/depthfusion.git}"
REPO_DIR="${DEPTHFUSION_REPO:-$HOME/depthfusion}"
VENV_DIR="${DEPTHFUSION_VENV_PATH:-$HOME/.depthfusion-venv}"
MLX_HOST="127.0.0.1"
MLX_PORT="${DEPTHFUSION_GEMMA_PORT:-8000}"
REST_HOST="127.0.0.1"
REST_PORT="${DEPTHFUSION_REST_PORT:-7300}"
MLX_MODEL="${DEPTHFUSION_GEMMA_MODEL:-mlx-community/Qwen2.5-14B-Instruct-4bit}"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_MLX="$LAUNCH_AGENTS/com.depthfusion.mlx-serve.plist"
PLIST_SOLO="$LAUNCH_AGENTS/com.depthfusion.solo.plist"
LOG_DIR="$HOME/Library/Logs"

# ── Colour output ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'; BLU='\033[0;34m'; RST='\033[0m'
info()    { printf "${BLU}→${RST} %s\n" "$*"; }
success() { printf "${GRN}✓${RST} %s\n" "$*"; }
warn()    { printf "${YLW}⚠${RST}  %s\n" "$*"; }
die()     { printf "${RED}✗${RST} %s\n" "$*" >&2; exit 1; }

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║        DepthFusion — Solo (Mac) Installer        ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# STEP 1 — Hardware and OS checks
# =============================================================================
info "Checking hardware and OS …"
[[ "$(uname -s)" == "Darwin" ]] || die "Solo mode requires macOS."
[[ "$(uname -m)" == "arm64"  ]] || die "MLX requires Apple Silicon (M1/M2/M3/M4)."

MACOS_VER="$(sw_vers -productVersion 2>/dev/null || echo "0")"
MACOS_MAJOR="$(echo "$MACOS_VER" | cut -d. -f1)"
[[ "$MACOS_MAJOR" -ge 13 ]] || die "macOS 13 (Ventura) or later required. Found: $MACOS_VER"
success "Apple Silicon · macOS $MACOS_VER"

# =============================================================================
# STEP 2 — uv (fast Python package manager)
# =============================================================================
if ! command -v uv &>/dev/null; then
    info "Installing uv …"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv installs to ~/.local/bin or ~/.cargo/bin depending on version
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv &>/dev/null || die "uv not found after install — open a new Terminal and re-run."
success "uv $(uv --version 2>/dev/null | awk '{print $2}')"

# =============================================================================
# STEP 3 — Clone / update the repo
# =============================================================================
if [[ -d "$REPO_DIR/.git" ]]; then
    info "Updating existing repo at $REPO_DIR …"
    git -C "$REPO_DIR" pull --ff-only || warn "git pull failed — continuing with existing checkout."
else
    info "Cloning DepthFusion into $REPO_DIR …"
    git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi
success "Repo ready at $REPO_DIR"

# =============================================================================
# STEP 4 — Virtualenv + mlx-lm + DepthFusion (mac-mlx extras)
# =============================================================================
info "Creating venv at $VENV_DIR …"
uv venv --python 3.12 "$VENV_DIR"
VENV_PYTHON="$VENV_DIR/bin/python"

info "Installing mlx-lm …"
uv pip install --python "$VENV_PYTHON" mlx-lm

info "Installing DepthFusion (mac-mlx extras, editable) …"
uv pip install --python "$VENV_PYTHON" -e "$REPO_DIR[mac-mlx]"
success "Python environment ready"

# =============================================================================
# STEP 5 — launchd plists (both loopback-only: 127.0.0.1)
# =============================================================================
info "Writing launchd service plists …"
mkdir -p "$LAUNCH_AGENTS" "$LOG_DIR"

# --- MLX inference server → 127.0.0.1:8000 ---
cat > "$PLIST_MLX" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.depthfusion.mlx-serve</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/mlx_lm.server</string>
        <string>--model</string>
        <string>$MLX_MODEL</string>
        <string>--host</string>
        <string>$MLX_HOST</string>
        <string>--port</string>
        <string>$MLX_PORT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/depthfusion-mlx.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/depthfusion-mlx.log</string>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
</dict>
</plist>
PLIST

# --- DepthFusion REST / solo server → 127.0.0.1:7300 ---
cat > "$PLIST_SOLO" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.depthfusion.solo</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>depthfusion.api.rest:app</string>
        <string>--host</string>
        <string>$REST_HOST</string>
        <string>--port</string>
        <string>$REST_PORT</string>
        <string>--log-level</string>
        <string>warning</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DEPTHFUSION_MODE</key>
        <string>mac-mlx</string>
        <key>DEPTHFUSION_GEMMA_URL</key>
        <string>http://$MLX_HOST:$MLX_PORT</string>
        <key>DEPTHFUSION_GEMMA_MODEL</key>
        <string>$MLX_MODEL</string>
        <key>DEPTHFUSION_API_PORT</key>
        <string>$REST_PORT</string>
        <key>DEPTHFUSION_EMBEDDING_BACKEND</key>
        <string>local</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/depthfusion-solo.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/depthfusion-solo.log</string>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
</dict>
</plist>
PLIST
success "launchd plists written (both bound to 127.0.0.1)"

# =============================================================================
# STEP 6 — Load services
# =============================================================================
info "Loading services …"
launchctl unload "$PLIST_MLX"  2>/dev/null || true
launchctl unload "$PLIST_SOLO" 2>/dev/null || true
launchctl load "$PLIST_MLX"
success "MLX inference server loaded (model downloads on first start)"
sleep 3
launchctl load "$PLIST_SOLO"
success "DepthFusion solo server loaded"

# =============================================================================
# STEP 7 — Wait for health, then completion message
# =============================================================================
info "Waiting for the server to become ready (first model load can take 15–60 s) …"
MAX_WAIT=90; ELAPSED=0; READY=0
while [[ "$ELAPSED" -lt "$MAX_WAIT" ]]; do
    if curl -sf "http://$REST_HOST:$REST_PORT/health" &>/dev/null; then
        READY=1; break
    fi
    sleep 3; ELAPSED=$(( ELAPSED + 3 )); printf "."
done
echo ""

if [[ "$READY" -eq 1 ]]; then
    echo ""
    echo "╔══════════════════════════════════════════════════╗"
    echo "║  ✓ DepthFusion is running.                       ║"
    echo "║                                                  ║"
    echo "║  MLX inference : http://127.0.0.1:$MLX_PORT          ║"
    echo "║  REST API      : http://127.0.0.1:$REST_PORT          ║"
    echo "║                                                  ║"
    echo "║  Return to the app and enter your Anthropic      ║"
    echo "║  API key to finish setup.                        ║"
    echo "╚══════════════════════════════════════════════════╝"
    echo ""
else
    warn "Server did not report healthy within ${MAX_WAIT}s."
    warn "It may still be downloading the model. Check logs:"
    echo "     $LOG_DIR/depthfusion-mlx.log"
    echo "     $LOG_DIR/depthfusion-solo.log"
    echo "  The app will detect the server automatically once it is up."
fi
