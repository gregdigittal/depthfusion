#!/usr/bin/env bash
# =============================================================================
# DepthFusion — VPS Tailscale Setup
# =============================================================================
# Run this ONCE on the VPS as root (or with sudo).
# What it does:
#   1. Installs Tailscale
#   2. Connects this VPS to your Tailscale network
#   3. Locks down port 7301 so it's only reachable via Tailscale
#   4. Prints the Tailscale IP to share with your team
#
# Usage:
#   sudo bash setup-tailscale-vps.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'; BLU='\033[0;34m'; RST='\033[0m'
info()    { printf "${BLU}→${RST} %s\n" "$*"; }
success() { printf "${GRN}✓${RST} %s\n" "$*"; }
warn()    { printf "${YLW}⚠${RST}  %s\n" "$*"; }
die()     { printf "${RED}✗${RST} %s\n" "$*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die "Run as root: sudo bash $0"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║    DepthFusion — VPS Tailscale Setup             ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# 1. Install Tailscale
# =============================================================================
if command -v tailscale &>/dev/null; then
    info "Tailscale already installed ($(tailscale version | head -1))"
else
    info "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    success "Tailscale installed"
fi

# =============================================================================
# 2. Connect to your Tailscale network
# =============================================================================
TAILSCALE_STATUS=$(tailscale status --json 2>/dev/null | python3 -c \
    "import json,sys; print(json.load(sys.stdin).get('BackendState',''))" 2>/dev/null || echo "")

if [[ "$TAILSCALE_STATUS" == "Running" ]]; then
    success "Tailscale already connected"
else
    info "Connecting to Tailscale..."
    echo ""
    echo "  A URL will appear below. Open it in your browser to approve this VPS."
    echo ""
    tailscale up --accept-routes
    echo ""
fi

# =============================================================================
# 3. Get the Tailscale IP
# =============================================================================
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || "")
[[ -n "$TAILSCALE_IP" ]] || die "Could not get Tailscale IP — is the VPS connected? Run: tailscale status"
success "VPS Tailscale IP: $TAILSCALE_IP"

# =============================================================================
# 4. Lock down port 7301 to Tailscale only
# =============================================================================
info "Configuring firewall (ufw)..."

if ! command -v ufw &>/dev/null; then
    apt-get install -y ufw 2>/dev/null || warn "ufw not found — install it manually and run: ufw allow from 100.64.0.0/10 to any port 7301 && ufw deny 7301"
else
    # Allow access from the entire Tailscale IP range (100.64.0.0/10)
    ufw allow from 100.64.0.0/10 to any port 7301 comment "DepthFusion MCP — Tailscale only"

    # Block public access to port 7301
    # (ufw evaluates rules top-to-bottom; the allow above fires first for Tailscale IPs)
    ufw deny 7301 comment "DepthFusion MCP — block public"

    ufw --force enable
    ufw reload
    success "Port 7301 is now Tailscale-only (public access blocked)"
fi

# =============================================================================
# Done — print the IP for the team
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  VPS setup complete!                                                 ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
printf "║  Tailscale IP to share with your team:                               ║\n"
printf "║                                                                      ║\n"
printf "║    %-66s ║\n" "$TAILSCALE_IP"
printf "║                                                                      ║\n"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  Next steps:                                                         ║"
echo "║  1. Copy the Tailscale IP above.                                     ║"
echo "║  2. Paste it into scripts/connect-vps.sh and connect-vps.ps1        ║"
echo "║     (replace VPS_TAILSCALE_IP_HERE with the real IP).               ║"
echo "║  3. Invite your team via admin.tailscale.com → DNS → Invite users.  ║"
echo "║  4. Send each team member their connect-vps script.                  ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
