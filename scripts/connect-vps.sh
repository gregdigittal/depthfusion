#!/usr/bin/env bash
# =============================================================================
# DepthFusion — Connect to Team VPS (Mac)
# =============================================================================
# Configures Claude Desktop (and Claude Code CLI if installed) to use the
# team's shared DepthFusion memory hub on the VPS via Tailscale.
#
# Prerequisites:
#   - Tailscale installed and connected to Greg's tailnet (see instructions)
#   - Greg has approved your device in admin.tailscale.com
#
# Distribute privately — do NOT commit to GitHub.
#
# Usage:
#   bash connect-vps.sh
# =============================================================================
set -euo pipefail

# ─── FILL IN AFTER RUNNING setup-tailscale-vps.sh ON THE VPS ───────────────
VPS_TAILSCALE_IP="100.112.109.51"
# ────────────────────────────────────────────────────────────────────────────
VPS_PORT="7301"
MCP_TOKEN="3cea56481975dc53587e8d99cfa989c3ab8b1c3e5e44792443832f4cf8c1f317"
MCP_URL="http://${VPS_TAILSCALE_IP}:${VPS_PORT}/sse"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'; BLU='\033[0;34m'; CYN='\033[0;36m'; RST='\033[0m'
info()    { printf "${BLU}→${RST} %s\n" "$*"; }
success() { printf "${GRN}✓${RST} %s\n" "$*"; }
warn()    { printf "${YLW}⚠${RST}  %s\n" "$*"; }
die()     { printf "${RED}✗${RST} %s\n" "$*" >&2; exit 1; }
step()    { printf "\n${CYN}── %s ──${RST}\n" "$*"; }

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║    DepthFusion — Connect to Team VPS             ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# Sanity check: placeholder not filled in
if [[ "$VPS_TAILSCALE_IP" == "VPS_TAILSCALE_IP_HERE" ]]; then
    die "VPS Tailscale IP not set. Open this script in a text editor and replace VPS_TAILSCALE_IP_HERE with the actual IP Greg gave you."
fi

# =============================================================================
# 1. Tailscale — install if missing
# =============================================================================
step "Checking Tailscale"

if ! command -v tailscale &>/dev/null; then
    warn "Tailscale not found. Installing via Homebrew..."
    if ! command -v brew &>/dev/null; then
        echo ""
        echo "  Homebrew is also missing. To install Tailscale manually:"
        echo "  1. Open your browser and go to: https://tailscale.com/download"
        echo "  2. Download and install the Mac version."
        echo "  3. Sign in with Google or create a free account."
        echo "  4. Message Greg with your email address to get approved."
        echo "  5. Re-run this script once the Tailscale icon shows connected."
        echo ""
        die "Please install Tailscale first, then re-run this script."
    fi
    brew install --cask tailscale
    success "Tailscale installed"
    echo ""
    echo "  Tailscale is now installed. To connect:"
    echo "  1. Click the Tailscale icon in your menu bar (top-right of screen)."
    echo "  2. Click 'Log in' and sign in or create a free account."
    echo "  3. Message Greg with your Tailscale email to get approved."
    echo "  4. Wait until the icon turns solid (not grey)."
    echo "  5. Re-run this script."
    echo ""
    exit 0
fi

success "Tailscale is installed"

# =============================================================================
# 2. Tailscale — check it's running and connected
# =============================================================================
TAILSCALE_STATUS=$(tailscale status --json 2>/dev/null | python3 -c \
    "import json,sys; print(json.load(sys.stdin).get('BackendState',''))" 2>/dev/null || echo "")

if [[ "$TAILSCALE_STATUS" != "Running" ]]; then
    warn "Tailscale is installed but not connected."
    echo ""
    echo "  To connect:"
    echo "  1. Click the Tailscale icon in your menu bar (top-right of screen)."
    echo "  2. Click 'Log in' and sign in."
    echo "  3. Message Greg with your email to get approved (if you haven't already)."
    echo "  4. Wait until the icon shows a solid colour (not grey)."
    echo "  5. Re-run this script."
    echo ""
    exit 1
fi

MY_TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
if [[ -n "$MY_TAILSCALE_IP" ]]; then
    success "Tailscale running — your IP is $MY_TAILSCALE_IP"
else
    success "Tailscale running"
fi

# =============================================================================
# 3. Verify VPS reachability over Tailscale
# =============================================================================
step "Checking VPS connectivity ($VPS_TAILSCALE_IP:$VPS_PORT)"
if curl -sf --max-time 8 \
    -H "Authorization: Bearer $MCP_TOKEN" \
    "http://${VPS_TAILSCALE_IP}:${VPS_PORT}/health" &>/dev/null; then
    success "VPS is reachable over Tailscale"
else
    warn "Cannot reach $VPS_TAILSCALE_IP:$VPS_PORT"
    echo ""
    echo "  This usually means one of:"
    echo "  a) Greg hasn't approved your device yet (message him your Tailscale email)"
    echo "  b) The VPS is temporarily offline (ask Greg)"
    echo "  c) The IP in this script is wrong"
    echo ""
    echo "  Try: tailscale ping $VPS_TAILSCALE_IP"
    echo "  If that also times out, your device isn't approved yet."
    echo ""
    exit 1
fi

# =============================================================================
# 4. Claude Desktop
# =============================================================================
step "Registering with Claude Desktop"

DESKTOP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
mkdir -p "$(dirname "$DESKTOP_CONFIG")"

if [[ -f "$DESKTOP_CONFIG" ]]; then
    cp "$DESKTOP_CONFIG" "${DESKTOP_CONFIG}.bak-$(date +%Y%m%d-%H%M%S)"
fi

python3 - "$DESKTOP_CONFIG" "$MCP_URL" <<'PYEOF'
import json, os, sys, tempfile
config_path, mcp_url = sys.argv[1], sys.argv[2]
config = {}
if os.path.exists(config_path):
    try:
        with open(config_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        sys.exit(f"Error: {config_path} is not valid JSON ({exc}). Fix or remove it, then re-run.")
config.setdefault("mcpServers", {})
config["mcpServers"]["depthfusion"] = {"url": mcp_url}
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

success "Claude Desktop configured → $MCP_URL"

# =============================================================================
# 5. Claude Code CLI (optional)
# =============================================================================
if command -v claude &>/dev/null; then
    step "Registering with Claude Code CLI"
    claude mcp remove depthfusion -s user 2>/dev/null || true
    claude mcp add depthfusion --scope user "$MCP_URL"
    success "Claude Code CLI registered (user-scoped)"
else
    warn "Claude Code CLI not found — only Claude Desktop configured."
fi

# =============================================================================
# Done
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  Connected!                                                          ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  1. Quit and restart Claude Desktop.                                 ║"
echo "║  2. Open a new chat and type:  depthfusion_status                   ║"
echo "║  3. You should see the team memory hub respond.                      ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  ⚠  Keep Tailscale running. If you quit Tailscale, the connection   ║"
echo "║     will stop working until you restart it.                          ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
