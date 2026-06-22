#!/usr/bin/env bash
# =============================================================================
# DepthFusion — Self-hosted VPS Installer
# =============================================================================
# Installs DepthFusion on a Linux server the user controls. Detects whether a
# CUDA GPU is present and chooses the inference mode accordingly:
#   • GPU present (nvidia-smi) → vps-gpu mode, on-box vLLM inference
#   • No GPU                   → vps-cpu mode, Anthropic API for LLM calls
#
# Public access is served ONLY through a Caddy TLS vhost (automatic HTTPS via
# Let's Encrypt). Every internal service — the DepthFusion REST API, Keycloak,
# and any datastore — binds to 127.0.0.1 (loopback) and is reachable from the
# internet exclusively through Caddy. This satisfies the infra-exposure rule:
# loopback-by-default, public only behind TLS + auth.
#
# Security invariants enforced by this script:
#   • Keycloak admin credentials are PROMPTED, never product defaults.
#   • The DepthFusion realm admin user is PROMPTED (email + password).
#   • Keycloak binds to 127.0.0.1:8080 only — no 0.0.0.0, no published Docker
#     port on a public interface.
#   • No secret, token, or password is hardcoded — everything is prompted or
#     read from the environment and written to a root-only (chmod 600) env file.
#
# Usage (run as root on a fresh Ubuntu 22.04+ host):
#   sudo bash install-vps.sh
#   sudo DEPTHFUSION_DOMAIN=df.example.com bash install-vps.sh   (non-interactive domain)
# =============================================================================
set -euo pipefail

# ── Colour output ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'; BLU='\033[0;34m'; RST='\033[0m'
info()    { printf "${BLU}→${RST} %s\n" "$*"; }
success() { printf "${GRN}✓${RST} %s\n" "$*"; }
warn()    { printf "${YLW}⚠${RST}  %s\n" "$*"; }
die()     { printf "${RED}✗${RST} %s\n" "$*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die "Run as root: sudo bash $0"

# ── Configuration (loopback-only binds; never 0.0.0.0) ───────────────────────
REPO_URL="${DEPTHFUSION_REPO_URL:-https://github.com/gregdigittal/depthfusion.git}"
REPO_DIR="${DEPTHFUSION_REPO:-/opt/depthfusion}"
VENV_DIR="${DEPTHFUSION_VENV_PATH:-/opt/depthfusion/.venv}"
ENV_FILE="${DEPTHFUSION_ENV_FILE:-/etc/depthfusion/depthfusion.env}"
SERVICE_USER="${DEPTHFUSION_USER:-depthfusion}"

REST_HOST="127.0.0.1"
REST_PORT="${DEPTHFUSION_REST_PORT:-7300}"
KEYCLOAK_HOST="127.0.0.1"
KEYCLOAK_PORT="${KEYCLOAK_PORT:-8080}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-depthfusion}"
KEYCLOAK_CLIENT_ID="${KEYCLOAK_CLIENT_ID:-depthfusion-app}"
KEYCLOAK_CONTAINER="depthfusion-keycloak"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║     DepthFusion — Self-hosted VPS Installer      ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# STEP 0 — Prompts (domain + credentials). No defaults for any secret.
# =============================================================================
DOMAIN="${DEPTHFUSION_DOMAIN:-}"
while [[ -z "$DOMAIN" ]]; do
    read -r -p "Public domain for this server (e.g. df.example.com): " DOMAIN
    [[ -n "$DOMAIN" ]] || warn "A domain is required for the Caddy TLS vhost."
done

# Keycloak bootstrap admin (console / master realm). Prompted — no defaults.
KEYCLOAK_ADMIN_USER="${KEYCLOAK_ADMIN_USER:-}"
while [[ -z "$KEYCLOAK_ADMIN_USER" ]]; do
    read -r -p "Keycloak admin username (NOT 'admin'): " KEYCLOAK_ADMIN_USER
    if [[ "$KEYCLOAK_ADMIN_USER" == "admin" ]]; then
        warn "Refusing the default username 'admin'. Choose a non-default username."
        KEYCLOAK_ADMIN_USER=""
    fi
done

prompt_password() {
    # prompt_password <var_name> <label>
    local __var="$1" label="$2" pw1 pw2
    while :; do
        read -r -s -p "$label: " pw1; echo ""
        [[ ${#pw1} -ge 12 ]] || { warn "Password must be at least 12 characters."; continue; }
        [[ "$pw1" != "admin" && "$pw1" != "password" ]] || { warn "Refusing a default/weak password."; continue; }
        read -r -s -p "$label (confirm): " pw2; echo ""
        [[ "$pw1" == "$pw2" ]] || { warn "Passwords did not match — try again."; continue; }
        printf -v "$__var" '%s' "$pw1"
        break
    done
}

KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-}"
[[ -n "$KEYCLOAK_ADMIN_PASSWORD" ]] || prompt_password KEYCLOAK_ADMIN_PASSWORD "Keycloak admin password (min 12 chars)"

# DepthFusion realm admin user (the human who signs into the app).
ADMIN_EMAIL="${DEPTHFUSION_ADMIN_EMAIL:-}"
while [[ -z "$ADMIN_EMAIL" ]]; do
    read -r -p "Your admin email (becomes the first DepthFusion user): " ADMIN_EMAIL
    [[ "$ADMIN_EMAIL" == *"@"*"."* ]] || { warn "Enter a valid email address."; ADMIN_EMAIL=""; }
done

ADMIN_PASSWORD="${DEPTHFUSION_ADMIN_PASSWORD:-}"
[[ -n "$ADMIN_PASSWORD" ]] || prompt_password ADMIN_PASSWORD "Your admin password (min 12 chars)"

# =============================================================================
# STEP 1 — Detect CUDA → choose mode
# =============================================================================
info "Detecting GPU …"
if command -v nvidia-smi &>/dev/null && nvidia-smi -L &>/dev/null; then
    DF_MODE="vps-gpu"
    success "CUDA GPU detected — using on-box vLLM inference (vps-gpu)."
else
    DF_MODE="vps-cpu"
    warn "No CUDA GPU detected — using Anthropic API for LLM calls (vps-cpu)."
fi

# vps-cpu requires an Anthropic API key. Prompt (or env) — never hardcoded.
DF_ANTHROPIC_KEY="${DEPTHFUSION_ANTHROPIC_API_KEY:-}"
if [[ "$DF_MODE" == "vps-cpu" && -z "$DF_ANTHROPIC_KEY" ]]; then
    while [[ -z "$DF_ANTHROPIC_KEY" ]]; do
        read -r -s -p "Anthropic API key (sk-ant-…), used for LLM calls in CPU mode: " DF_ANTHROPIC_KEY; echo ""
        [[ "$DF_ANTHROPIC_KEY" == sk-ant-* ]] || { warn "Key must start with 'sk-ant-'."; DF_ANTHROPIC_KEY=""; }
    done
fi

# =============================================================================
# STEP 2 — System packages
# =============================================================================
info "Installing system packages (python3.12, docker, caddy) …"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y

# Python 3.12 (deadsnakes if not in the base repo)
if ! command -v python3.12 &>/dev/null; then
    apt-get install -y software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -y
fi
apt-get install -y python3.12 python3.12-venv git curl ca-certificates docker.io

# uv
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

# Caddy (official apt repo)
if ! command -v caddy &>/dev/null; then
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -y
    apt-get install -y caddy
fi
systemctl enable --now docker
success "System packages installed"

# =============================================================================
# STEP 3 — Service user + repo + venv
# =============================================================================
if ! id -u "$SERVICE_USER" &>/dev/null; then
    info "Creating service user '$SERVICE_USER' …"
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [[ -d "$REPO_DIR/.git" ]]; then
    info "Updating repo at $REPO_DIR …"
    git -C "$REPO_DIR" pull --ff-only || warn "git pull failed — using existing checkout."
else
    info "Cloning DepthFusion into $REPO_DIR …"
    git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

info "Creating venv and installing DepthFusion ($DF_MODE extras) …"
uv venv --python 3.12 "$VENV_DIR"
uv pip install --python "$VENV_DIR/bin/python" -e "$REPO_DIR[$DF_MODE]"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$REPO_DIR"
success "DepthFusion installed in $DF_MODE mode"

# =============================================================================
# STEP 4 — Env file (root-only, chmod 600). Secrets sourced here, never inline.
# =============================================================================
info "Writing environment file at $ENV_FILE …"
mkdir -p "$(dirname "$ENV_FILE")"
DF_API_TOKEN="$(python3.12 -c 'import secrets; print(secrets.token_urlsafe(32))')"
OIDC_ISSUER="https://${DOMAIN}/auth/realms/${KEYCLOAK_REALM}"

umask 077
cat > "$ENV_FILE" <<ENV
# DepthFusion VPS environment — generated by install-vps.sh
# This file contains secrets. Keep mode 600, owned by root.
DEPTHFUSION_MODE=${DF_MODE}
DEPTHFUSION_API_PORT=${REST_PORT}
# Bind the REST API to loopback only; Caddy terminates TLS and proxies in.
DEPTHFUSION_API_PUBLIC=0
DEPTHFUSION_API_TOKEN=${DF_API_TOKEN}

# OIDC (Keycloak) — issued via the Caddy TLS vhost, validated against the realm.
DEPTHFUSION_OIDC_ISSUER=${OIDC_ISSUER}
DEPTHFUSION_OIDC_CLIENT_ID=${KEYCLOAK_CLIENT_ID}
DEPTHFUSION_OIDC_AUDIENCE=${KEYCLOAK_CLIENT_ID}
DEPTHFUSION_OIDC_REDIRECT_URI=https://${DOMAIN}/callback
ENV

if [[ "$DF_MODE" == "vps-cpu" ]]; then
    printf 'DEPTHFUSION_ANTHROPIC_API_KEY=%s\n' "$DF_ANTHROPIC_KEY" >> "$ENV_FILE"
    printf 'DEPTHFUSION_RERANKER_BACKEND=%s\n' "haiku" >> "$ENV_FILE"
else
    printf 'DEPTHFUSION_GEMMA_URL=%s\n' "http://127.0.0.1:8000" >> "$ENV_FILE"
fi
umask 022

chmod 600 "$ENV_FILE"
chown root:root "$ENV_FILE"
success "Env file written (chmod 600, root-owned)"

# =============================================================================
# STEP 5 — Keycloak (Docker), bound to 127.0.0.1 ONLY (never 0.0.0.0)
# =============================================================================
info "Starting Keycloak (loopback-only: 127.0.0.1:${KEYCLOAK_PORT}) …"
docker rm -f "$KEYCLOAK_CONTAINER" 2>/dev/null || true

# -p 127.0.0.1:HOST:CONTAINER binds the published port to loopback only.
# Public access to Keycloak is exclusively through the Caddy /auth vhost.
# KC_BOOTSTRAP_ADMIN_* come from prompted values; they are not defaults.
docker run -d --name "$KEYCLOAK_CONTAINER" --restart unless-stopped \
    -p "127.0.0.1:${KEYCLOAK_PORT}:8080" \
    -e KC_BOOTSTRAP_ADMIN_USERNAME="$KEYCLOAK_ADMIN_USER" \
    -e KC_BOOTSTRAP_ADMIN_PASSWORD="$KEYCLOAK_ADMIN_PASSWORD" \
    -e KC_PROXY_HEADERS=xforwarded \
    -e KC_HTTP_ENABLED=true \
    -e KC_HOSTNAME="https://${DOMAIN}/auth" \
    quay.io/keycloak/keycloak:latest \
    start --http-port=8080

info "Waiting for Keycloak to become ready …"
KC_READY=0
for _ in $(seq 1 60); do
    if curl -sf "http://${KEYCLOAK_HOST}:${KEYCLOAK_PORT}/health/ready" &>/dev/null \
        || curl -sf "http://${KEYCLOAK_HOST}:${KEYCLOAK_PORT}/realms/master" &>/dev/null; then
        KC_READY=1; break
    fi
    sleep 3; printf "."
done
echo ""
[[ "$KC_READY" -eq 1 ]] || die "Keycloak did not become ready. Check: docker logs $KEYCLOAK_CONTAINER"
success "Keycloak running on 127.0.0.1:${KEYCLOAK_PORT}"

# =============================================================================
# STEP 6 — Realm + client + admin user via kcadm (inside the container)
# =============================================================================
info "Configuring realm '${KEYCLOAK_REALM}', client '${KEYCLOAK_CLIENT_ID}', and admin user …"
kc() { docker exec "$KEYCLOAK_CONTAINER" /opt/keycloak/bin/kcadm.sh "$@"; }

# Authenticate kcadm against the local (in-container) admin endpoint using the
# prompted bootstrap credentials. Credentials are passed via exec, not stored.
kc config credentials --server http://localhost:8080 \
    --realm master --user "$KEYCLOAK_ADMIN_USER" --password "$KEYCLOAK_ADMIN_PASSWORD"

# Create realm (idempotent).
kc create realms -s realm="$KEYCLOAK_REALM" -s enabled=true 2>/dev/null \
    || info "Realm '${KEYCLOAK_REALM}' already exists."

# Create the public PKCE client used by the desktop app + web callback.
kc create clients -r "$KEYCLOAK_REALM" \
    -s clientId="$KEYCLOAK_CLIENT_ID" \
    -s enabled=true \
    -s publicClient=true \
    -s standardFlowEnabled=true \
    -s 'redirectUris=["https://'"${DOMAIN}"'/callback","http://localhost:8400/callback"]' \
    -s 'attributes."pkce.code.challenge.method"=S256' 2>/dev/null \
    || info "Client '${KEYCLOAK_CLIENT_ID}' already exists."

# Create the admin user with the prompted email/password.
kc create users -r "$KEYCLOAK_REALM" \
    -s username="$ADMIN_EMAIL" \
    -s email="$ADMIN_EMAIL" \
    -s enabled=true \
    -s emailVerified=true 2>/dev/null \
    || info "User '${ADMIN_EMAIL}' already exists."

kc set-password -r "$KEYCLOAK_REALM" \
    --username "$ADMIN_EMAIL" --new-password "$ADMIN_PASSWORD"
success "Realm, client, and admin user configured"

# =============================================================================
# STEP 7 — Caddy vhost: https://{DOMAIN} → loopback services (TLS terminator)
# =============================================================================
info "Writing Caddyfile (TLS vhost → loopback REST + Keycloak) …"
cat > /etc/caddy/Caddyfile <<CADDY
# DepthFusion — automatic HTTPS. Caddy is the ONLY public-facing service.
# It terminates TLS and reverse-proxies to loopback-bound backends.
${DOMAIN} {
    encode gzip

    # Keycloak (OIDC) — reachable only via this TLS vhost under /auth.
    handle_path /auth/* {
        reverse_proxy 127.0.0.1:${KEYCLOAK_PORT}
    }

    # Everything else → DepthFusion REST API (loopback).
    handle {
        reverse_proxy 127.0.0.1:${REST_PORT}
    }
}
CADDY

caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl enable --now caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy
success "Caddy TLS vhost active for https://${DOMAIN}"

# =============================================================================
# STEP 8 — systemd unit for the DepthFusion REST API (loopback bind)
# =============================================================================
info "Installing systemd unit for the DepthFusion REST API …"
cat > /etc/systemd/system/depthfusion-rest.service <<UNIT
[Unit]
Description=DepthFusion REST API
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m uvicorn depthfusion.api.rest:app --host ${REST_HOST} --port ${REST_PORT} --log-level warning
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

# GPU mode: install the vLLM systemd unit shipped in the repo (loopback bind).
if [[ "$DF_MODE" == "vps-gpu" && -f "$REPO_DIR/scripts/vllm-qwen.service" ]]; then
    info "Installing vLLM systemd unit (vps-gpu) …"
    cp "$REPO_DIR/scripts/vllm-qwen.service" /etc/systemd/system/vllm-qwen.service
fi

systemctl daemon-reload
systemctl enable --now depthfusion-rest
if [[ "$DF_MODE" == "vps-gpu" && -f /etc/systemd/system/vllm-qwen.service ]]; then
    systemctl enable --now vllm-qwen || warn "vllm-qwen failed to start — check 'journalctl -u vllm-qwen'."
fi
success "DepthFusion REST API service enabled"

# =============================================================================
# STEP 9 — Completion box
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓ DepthFusion VPS install complete                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  Mode          : %-43s║\n" "$DF_MODE"
printf "║  Server URL    : %-43s║\n" "https://${DOMAIN}"
printf "║  Sign-in user  : %-43s║\n" "$ADMIN_EMAIL"
echo "║  (Your password is the one you entered — not shown here.)   ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Bindings (all internal services are loopback-only):        ║"
printf "║    REST API    : %-43s║\n" "127.0.0.1:${REST_PORT}"
printf "║    Keycloak    : %-43s║\n" "127.0.0.1:${KEYCLOAK_PORT}"
echo "║  Public access is ONLY through the Caddy TLS vhost above.    ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Next: open the DepthFusion app, choose \"Connect to server\",║"
echo "║  enter the Server URL above, and sign in.                   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
