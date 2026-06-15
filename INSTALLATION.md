# DepthFusion V2 — Installation Guide

> **Branch:** `v2-enterprise`
> **Status:** v2.0.0-dev
> **Audience:** System administrators and enterprise deployers.

This guide covers the complete installation of DepthFusion V2: the Python API server (Linux VPS), the Tauri desktop app (macOS and Windows), and the required Microsoft Entra ID configuration.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Server Installation (Linux VPS)](#2-server-installation-linux-vps)
3. [Desktop App — macOS](#3-desktop-app--macos)
4. [Desktop App — Windows](#4-desktop-app--windows)
5. [Microsoft Entra ID Configuration](#5-microsoft-entra-id-configuration)
6. [Environment Variable Reference](#6-environment-variable-reference)
7. [SharePoint Integration (Optional)](#7-sharepoint-integration-optional)
8. [Upgrading from V1](#8-upgrading-from-v1)
9. [Verification](#9-verification)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

### Server (Linux VPS)

| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| RAM | 4 GB | 8 GB |
| CPU | 2 vCPUs | 4 vCPUs |
| Disk | 20 GB free | 50 GB free |
| Python | 3.11 | 3.12 |
| Network | Tailscale or TLS-terminated reverse proxy | |

GPU-accelerated mode (`vps-gpu`) additionally requires:
- CUDA 12.x
- NVIDIA GPU with 20 GB+ VRAM (RTX 3090 / A4000 or equivalent)
- `nvidia-smi` on PATH

```bash
# Verify Python version
python3 --version   # must be 3.11, 3.12, or 3.13

# Verify disk space
df -h ~

# Verify CUDA (GPU mode only)
nvidia-smi
```

### Desktop App Build Machine

| Tool | Required Version | Install |
|---|---|---|
| Rust + Cargo | 1.77.2+ | https://rustup.rs |
| Node.js | 18+ | https://nodejs.org |
| npm | bundled with Node.js | |

Linux additional system libraries (required for Tauri 2 on Ubuntu/Debian):

```bash
sudo apt update
sudo apt install -y \
  libwebkit2gtk-4.1-dev \
  libgdk-pixbuf2.0-dev \
  libgtk-3-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  patchelf \
  libssl-dev \
  pkg-config \
  build-essential \
  curl \
  wget \
  file \
  libxdo-dev \
  libsoup-3.0-dev \
  libjavascriptcoregtk-4.1-dev
```

### Microsoft Entra ID

You need:
- A Microsoft Azure account with permission to create App Registrations in your tenant
- A test tenant (do not use your production tenant for pilots)
- Global Admin or Application Administrator role in the target tenant

---

## 2. Server Installation (Linux VPS)

### 2.1 Clone the Repository

```bash
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion
git checkout v2-enterprise
```

### 2.2 Create a Python Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

### 2.3 Install DepthFusion V2 Server Dependencies

Choose the install mode that matches your server:

**CPU-only VPS (recommended for most deployments):**

```bash
pip install -e '.[vps-cpu]'
```

This installs: `anthropic>=0.40`, `chromadb>=1.0`, `fastapi>=0.100`, `uvicorn>=0.23`, and security-pinned transitive dependencies.

**GPU-accelerated VPS (requires CUDA 12.x and 20 GB+ VRAM):**

```bash
pip install -e '.[vps-gpu]'
# vLLM is installed separately — see the vps-gpu quickstart
pip install vllm
```

### 2.4 Configure Environment Variables

Copy the example env file and fill in all required values:

```bash
cp .env.example ~/.depthfusion.env
```

Open `~/.depthfusion.env` in your editor and set at minimum these required variables (see [Section 6](#6-environment-variable-reference) for the full reference):

```bash
# Identity (required — V2 will not start without these)
DEPTHFUSION_OIDC_CLIENT_ID=        # Application (client) ID from Entra — see Section 5
DEPTHFUSION_OIDC_TENANT_ID=        # Directory (tenant) ID from Entra — see Section 5
DEPTHFUSION_OIDC_AUDIENCE=         # api://<client-id>  OR  the client ID itself

# API tokens (required when binding publicly)
DEPTHFUSION_API_KEY=               # Your Anthropic API key (NOT ANTHROPIC_API_KEY — see note below)
DEPTHFUSION_API_TOKEN=             # Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
DEPTHFUSION_QUERY_API_KEY=         # Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Server binding
DEPTHFUSION_API_PORT=7300
DEPTHFUSION_MODE=vps-cpu           # or vps-gpu
```

> **Billing safety:** Always use `DEPTHFUSION_API_KEY`, never `ANTHROPIC_API_KEY`. Claude Code reads `ANTHROPIC_API_KEY` as its own credential and will switch your entire Claude Code billing to pay-per-token for everything — not just DepthFusion. The separate `DEPTHFUSION_API_KEY` prevents this.

Export the env file in your shell:

```bash
export $(grep -v '^#' ~/.depthfusion.env | xargs)
```

Or configure the systemd service to load it (recommended — see step 2.6).

### 2.5 Run the V2 Installer

The installer creates the database files, generates the device CA keypair, and writes `~/.depthfusion/config.json`:

```bash
python3 -m depthfusion.install.install --mode vps-cpu --v2
```

For GPU mode:

```bash
python3 -m depthfusion.install.install --mode vps-gpu --v2
```

The installer will:
1. Create `~/.depthfusion/` with `memory_store.db`, `event_log.db`, and `audit.db`
2. Generate the device CA keypair for device enrollment certificates
3. Prompt for OIDC provider configuration
4. Write `~/.depthfusion/config.json`
5. Install and enable the systemd service

### 2.6 Configure TLS (Required for Production)

**Using Let's Encrypt (recommended):**

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d depthfusion.yourdomain.com
```

Certificates are written to `/etc/letsencrypt/live/depthfusion.yourdomain.com/`.

Configure DepthFusion to use them:

```bash
cat > ~/.depthfusion/tls.json <<EOF
{
  "cert_file": "/etc/letsencrypt/live/depthfusion.yourdomain.com/fullchain.pem",
  "key_file": "/etc/letsencrypt/live/depthfusion.yourdomain.com/privkey.pem"
}
EOF
```

Add a post-renewal hook to restart the API server after certificate renewal:

```bash
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-depthfusion.sh <<'EOF'
#!/bin/bash
systemctl restart depthfusion-api
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-depthfusion.sh
```

Verify the certbot renewal timer is active:

```bash
sudo systemctl list-timers | grep certbot
```

### 2.7 Enable and Start the API Server

```bash
# Install the systemd service (the installer does this automatically)
# If you need to do it manually:
cp ~/projects/depthfusion/infra/systemd/depthfusion-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now depthfusion-api

# Verify the server is running
systemctl --user status depthfusion-api

# Check the health endpoint
curl -s https://depthfusion.yourdomain.com/v2/health | python3 -m json.tool
```

Expected response:

```json
{
  "status": "healthy",
  "version": "2.0.0",
  "databases": "ok",
  "oidc_provider": "reachable",
  "device_ca": "ok"
}
```

If you are testing locally before TLS is configured:

```bash
curl -s http://localhost:7300/v2/health
```

### 2.8 Bootstrap the First Admin User

The first user to enroll is automatically granted `admin` role (bootstrap mode). Bootstrap mode is active until the first admin user exists.

1. Install the desktop app on your machine (see [Section 3](#3-desktop-app--macos) or [Section 4](#4-desktop-app--windows))
2. Launch the app and sign in with your Entra ID account
3. The desktop app will prompt for the server URL — enter `https://depthfusion.yourdomain.com`
4. Complete the device enrollment flow
5. Verify enrollment on the server:

```bash
python3 -m depthfusion.admin.query_audit --last 5
```

You should see a `device_enrollment` event with your email address. After the first admin exists, bootstrap mode is disabled and all subsequent enrollments receive the default `contributor` role.

### 2.9 View and Approve Pending Device Enrollments

```bash
# List pending enrollments
python3 -m depthfusion.admin.devices --status pending

# Approve a device (replace abc-123 with the actual device_id)
python3 -m depthfusion.admin.devices --approve abc-123

# Approve with a specific role
python3 -m depthfusion.admin.devices --approve abc-123 --role operator
```

---

## 3. Desktop App — macOS

### 3.1 Install Rust

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# Verify
rustc --version   # must be 1.77.2+
cargo --version
```

### 3.2 Install Node.js Dependencies

```bash
cd /path/to/depthfusion/.claude/worktrees/v2-lane-c-ui/app
npm install
```

The frontend uses: React 19, Tauri API v2, Tailwind CSS v4, TypeScript 6, and Vite 8.

### 3.3 Build the Desktop App

```bash
# From inside app/
npm run build    # compiles TypeScript and bundles frontend assets

# Build the native binary
npx @tauri-apps/cli build
```

For a universal binary (Intel + Apple Silicon):

```bash
npx @tauri-apps/cli build --target universal-apple-darwin
```

The build output is in `src-tauri/target/release/bundle/`:

- `.dmg` installer: `src-tauri/target/release/bundle/dmg/DepthFusion_*.dmg`
- `.app` bundle: `src-tauri/target/release/bundle/macos/DepthFusion.app`

### 3.4 Install the App

Double-click the `.dmg` file and drag `DepthFusion.app` to your `/Applications` folder.

> **macOS Gatekeeper:** If you see "Apple could not verify", right-click the app and select Open, then confirm. For production deployments, sign and notarize the app with your Apple Developer certificate.

### 3.5 First Launch and Sign In

1. Open DepthFusion from `/Applications`
2. You will be prompted to enter your server URL (e.g., `https://depthfusion.yourdomain.com`)
3. Click **Sign In** — your browser will open to the Microsoft Entra ID sign-in page
4. Authenticate with your organizational account
5. The app completes the PKCE authorization code flow and stores your tokens in the macOS Keychain
6. Device enrollment completes automatically; an admin must approve your device before access is granted (unless you are the first user triggering bootstrap mode)

---

## 4. Desktop App — Windows

### 4.1 Install Rust

Download and run `rustup-init.exe` from https://rustup.rs.

In PowerShell after installation:

```powershell
rustc --version   # must be 1.77.2+
cargo --version
```

You also need the MSVC build tools. Install **Visual Studio Build Tools** from https://visualstudio.microsoft.com/visual-cpp-build-tools/ and select the "Desktop development with C++" workload.

### 4.2 Install Node.js

Download Node.js 18+ from https://nodejs.org. Choose the LTS release.

### 4.3 Install Node.js Dependencies

In PowerShell:

```powershell
cd path\to\depthfusion\.claude\worktrees\v2-lane-c-ui\app
npm install
```

### 4.4 Build the Desktop App

```powershell
npm run build
npx @tauri-apps/cli build
```

The build output is in `src-tauri\target\release\bundle\`:

- `.msi` installer: `src-tauri\target\release\bundle\msi\DepthFusion_*.msi`
- `.exe` NSIS installer: `src-tauri\target\release\bundle\nsis\DepthFusion_*-setup.exe`

### 4.5 Install the App

Run the `.msi` or `.exe` installer. Accept the UAC prompt.

### 4.6 First Launch and Sign In

1. Open DepthFusion from the Start menu
2. Enter your server URL when prompted
3. Click **Sign In** — your default browser opens to the Microsoft Entra ID sign-in page
4. Authenticate with your organizational account
5. The app completes the PKCE flow and stores tokens in the Windows Credential Manager
6. Wait for an admin to approve your device enrollment (unless bootstrap mode is active)

---

## 5. Microsoft Entra ID Configuration

### 5.1 Create the App Registration

1. Go to the [Azure Portal](https://portal.azure.com) and sign in as a Global Admin or Application Administrator
2. Navigate to **Azure Active Directory → App registrations → New registration**
3. Fill in the form:
   - **Name:** `DepthFusion`
   - **Supported account types:** `Accounts in this organizational directory only`
   - **Redirect URI:** Leave blank for now (configured in step 5.2)
4. Click **Register**
5. Record the **Application (client) ID** — this becomes `DEPTHFUSION_OIDC_CLIENT_ID`
6. Record the **Directory (tenant) ID** — this becomes `DEPTHFUSION_OIDC_TENANT_ID`

### 5.2 Configure Authentication

1. In the app registration, go to **Authentication → Add a platform → Mobile and desktop applications**
2. Add the following redirect URI:
   ```
   http://localhost:8400/callback
   ```
   This is the loopback URI used by the desktop app during the PKCE authorization code flow.
3. Also add (for device-code flow on the VPS):
   ```
   http://127.0.0.1
   ```
4. Under **Advanced settings**, enable **Allow public client flows** (required for the device-code and PKCE flows)
5. Click **Save**

### 5.3 Configure API Permissions

1. Go to **API permissions → Add a permission → Microsoft Graph**
2. Add these **delegated** permissions:
   - `openid`
   - `profile`
   - `email`
   - `offline_access`
   - `User.Read`
3. For SharePoint access, add this **application** permission:
   - `Sites.Selected` (Microsoft Graph)
4. Click **Grant admin consent for [your tenant]** and confirm

### 5.4 Set the Application ID URI (Token Audience)

1. Go to **Expose an API → Set** next to Application ID URI
2. Set it to `api://<your-application-client-id>`
   - Example: `api://a1b2c3d4-e5f6-7890-abcd-ef1234567890`
3. Click **Save**

This URI becomes the value for `DEPTHFUSION_OIDC_AUDIENCE`.

### 5.5 Set Environment Variables on the Server

Edit `~/.depthfusion.env` and fill in the values from the previous steps:

```bash
DEPTHFUSION_OIDC_CLIENT_ID=a1b2c3d4-e5f6-7890-abcd-ef1234567890
DEPTHFUSION_OIDC_TENANT_ID=f9e8d7c6-b5a4-3210-9876-fedcba987654
DEPTHFUSION_OIDC_AUDIENCE=api://a1b2c3d4-e5f6-7890-abcd-ef1234567890
DEPTHFUSION_OIDC_SCOPE=https://graph.microsoft.com/.default
DEPTHFUSION_OIDC_REDIRECT_URI=http://localhost:8400/callback
```

The following are computed automatically from `DEPTHFUSION_OIDC_TENANT_ID` and do not need to be set unless you are using a non-Entra OIDC provider:

```bash
# Auto-computed: https://login.microsoftonline.com/<TENANT_ID>/v2.0
# DEPTHFUSION_OIDC_ISSUER=

# Auto-computed: https://login.microsoftonline.com/<TENANT_ID>/discovery/v2.0/keys
# DEPTHFUSION_JWKS_URI=
```

Restart the API server after updating the env file:

```bash
systemctl --user restart depthfusion-api
curl -s http://localhost:7300/v2/health | python3 -m json.tool
# "oidc_provider" should be "reachable"
```

---

## 6. Environment Variable Reference

All variables are defined in `.env.example`. Below is a summary of the variables you are most likely to need.

### Required for V2

| Variable | Description |
|---|---|
| `DEPTHFUSION_OIDC_CLIENT_ID` | Azure AD Application (client) ID |
| `DEPTHFUSION_OIDC_TENANT_ID` | Azure AD Directory (tenant) ID |
| `DEPTHFUSION_OIDC_AUDIENCE` | Token audience: `api://<client-id>` or the client ID itself |
| `DEPTHFUSION_OIDC_SCOPE` | OAuth2 scope. Default: `https://graph.microsoft.com/.default` |
| `DEPTHFUSION_OIDC_REDIRECT_URI` | Must match the Redirect URI in your Entra app. Default: `http://localhost:8400/callback` |

### Auth / API Tokens

| Variable | Description |
|---|---|
| `DEPTHFUSION_API_KEY` | Anthropic API key for Haiku reranker/extraction (use instead of `ANTHROPIC_API_KEY`) |
| `DEPTHFUSION_API_TOKEN` | Bearer token for the REST API (required when `DEPTHFUSION_API_PUBLIC=1`) |
| `DEPTHFUSION_QUERY_API_KEY` | Read-only API key for `/query/*` endpoints |
| `DEPTHFUSION_MCP_TOKEN` | Bearer token for the MCP HTTP server (required when `DEPTHFUSION_MCP_PUBLIC=1`) |
| `DEPTHFUSION_V2_LEGACY_AUTH` | Set to `1` to accept V1 API tokens during migration. Remove after migration. |

### Server Binding

| Variable | Default | Description |
|---|---|---|
| `DEPTHFUSION_API_PORT` | `7300` | FastAPI server port |
| `DEPTHFUSION_MCP_PORT` | `7301` | MCP HTTP server port |
| `DEPTHFUSION_RLM_PORT` | `7302` | RLM service port |
| `DEPTHFUSION_API_PUBLIC` | `0` | Set to `1` to bind to `0.0.0.0`. Requires `DEPTHFUSION_API_TOKEN`. |
| `DEPTHFUSION_MCP_PUBLIC` | `0` | Set to `1` to bind MCP server publicly. Requires `DEPTHFUSION_MCP_TOKEN`. |

> **Security:** Never set `DEPTHFUSION_API_PUBLIC=1` or `DEPTHFUSION_MCP_PUBLIC=1` without authentication tokens and TLS termination. All ports default to loopback (`127.0.0.1`) only.

### Mode and Tier

| Variable | Default | Description |
|---|---|---|
| `DEPTHFUSION_MODE` | `local` | Install mode: `local`, `vps-cpu`, `vps-gpu`, or `mac-mlx` |
| `DEPTHFUSION_TIER_THRESHOLD` | `500` | Session count at which Tier 2 (ChromaDB) promotion is triggered |
| `DEPTHFUSION_TIER_AUTOPROMOTE` | `0` | Set to `1` to enable automatic tier promotion |

### LLM Backends

| Variable | Default | Description |
|---|---|---|
| `DEPTHFUSION_EMBEDDING_BACKEND` | `null` | `null` (BM25 only), `local` (sentence-transformers), or `haiku` |
| `DEPTHFUSION_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model name (used when `EMBEDDING_BACKEND=local`) |
| `DEPTHFUSION_RERANKER_BACKEND` | `null` | `null`, `haiku`, or `gemma` |
| `DEPTHFUSION_EXTRACTOR_BACKEND` | `null` | `null`, `haiku`, or `gemma` |
| `DEPTHFUSION_SUMMARISER_BACKEND` | `null` | `null`, `haiku`, or `gemma` |
| `DEPTHFUSION_GEMMA_URL` | `http://127.0.0.1:8000` | vLLM endpoint URL (GPU mode only) |
| `OPENROUTER_API_KEY` | | OpenRouter API key (for bridge tools and Codex fallback) |

### Key Feature Flags

| Variable | Default | Description |
|---|---|---|
| `DEPTHFUSION_GRAPH_ENABLED` | `true` | Enable knowledge graph entity extraction |
| `DEPTHFUSION_HNSW_ENABLED` | `false` | Enable HNSW approximate-nearest-neighbour search |
| `DEPTHFUSION_VECTOR_SEARCH_ENABLED` | `false` | Enable vector search in recall pipeline |
| `DEPTHFUSION_FUSION_GATES_ENABLED` | `false` | Enable BM25 + vector RRF fusion (Tier 2+) |
| `DEPTHFUSION_FTS_ENABLED` | `false` | Enable SQLite FTS5 full-text search |
| `DEPTHFUSION_METRICS_ENABLED` | `0` | Set to `1` to collect JSONL performance metrics |
| `DEPTHFUSION_COGNITIVE_SCORING` | `false` | Enable 8-component CognitiveScorer |
| `DEPTHFUSION_CONTRADICTION_ENGINE` | `false` | Enable ContradictionEngine on new captures |
| `DEPTHFUSION_DECISION_EXTRACTOR_ENABLED` | `false` | Enable decision extraction |
| `DEPTHFUSION_REST_API` | — | Set `DEPTHFUSION_REST_API=true` to start the FastAPI REST server |

### Storage and Data

| Variable | Default | Description |
|---|---|---|
| `DEPTHFUSION_BUS_BACKEND` | `memory` | Context bus backend: `memory` or `file` |
| `DEPTHFUSION_BUS_FILE_DIR` | `~/.claude/depthfusion/bus` | File bus directory (when `BUS_BACKEND=file`) |
| `DEPTHFUSION_PRUNE_AGE_DAYS` | `90` | Days before discovery files are pruned |
| `DEPTHFUSION_REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis URL for pub/sub. Must remain loopback-only. |
| `DEPTHFUSION_PROJECT` | | Project slug for this instance |
| `DEPTHFUSION_VENV_PATH` | | Path to venv (used by hooks to locate Python) |

---

## 7. SharePoint Integration (Optional)

SharePoint integration enables DepthFusion to ingest and recall from your organization's SharePoint sites.

### 7.1 Grant Sites.Selected Permission

The app registration created in Section 5 already has `Sites.Selected` in its API permissions. You must now grant it access to a specific SharePoint site.

Using the Microsoft Graph API (requires Global Admin or SharePoint Admin):

```bash
# Get a token for the Graph API
az login
TOKEN=$(az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv)

# Find the site ID for your SharePoint site
curl -H "Authorization: Bearer $TOKEN" \
  "https://graph.microsoft.com/v1.0/sites?search=your-site-name"

# Grant read access to the app (replace SITE_ID and APP_OBJECT_ID)
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "roles": ["read"],
    "grantedToIdentities": [{
      "application": {
        "id": "YOUR_APP_OBJECT_ID",
        "displayName": "DepthFusion"
      }
    }]
  }' \
  "https://graph.microsoft.com/v1.0/sites/SITE_ID/permissions"
```

For write access (required to publish discoveries back to SharePoint), change `"roles": ["read"]` to `"roles": ["write"]`.

### 7.2 Configure SharePoint Environment Variables

Add these to `~/.depthfusion.env`:

```bash
DEPTHFUSION_OIDC_CLIENT_ID=<same as Section 5>
DEPTHFUSION_OIDC_TENANT_ID=<same as Section 5>
DEPTHFUSION_OIDC_SCOPE=https://graph.microsoft.com/.default
```

No separate SharePoint-specific client ID or secret is required — DepthFusion uses the same Entra app registration and acquires tokens via the PKCE flow for delegated access, or via client credentials for service-to-service access.

For unattended/service-account access, create a client secret in the app registration:

1. **Certificates & secrets → New client secret**
2. Set a description and expiry
3. Record the **Value** (shown once)
4. Add to env:

```bash
DEPTHFUSION_OIDC_CLIENT_SECRET=<secret value>
```

### 7.3 Verify SharePoint Access

```bash
source .venv/bin/activate
python3 -c "
from depthfusion.connectors.sharepoint import SharePointClient
c = SharePointClient.from_env()
print(c.list_sites())
"
```

A successful response lists the SharePoint sites the app can access.

---

## 8. Upgrading from V1

If you have an existing V1 installation, follow this migration path.

### 8.1 Back Up V1 Data

```bash
BACKUP_DIR=~/.claude/depthfusion-v1-backup-$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"
cp -r ~/.claude/shared/discoveries/ "${BACKUP_DIR}/discoveries"
cp -r ~/.claude/sessions/ "${BACKUP_DIR}/sessions"
cp ~/.claude/depthfusion.env "${BACKUP_DIR}/depthfusion.env.bak" 2>/dev/null || true
echo "Backup written to $BACKUP_DIR"
```

Stop any running V1 processes:

```bash
systemctl stop depthfusion 2>/dev/null || pkill -f depthfusion
```

### 8.2 Run the ACL Backfill

V2 adds `acl_allow` and `classification` columns to all six data stores. Run the backfill to stamp legacy records:

```bash
# Dry run first — verify no data loss
python3 scripts/backfill_acl.py --dry-run --data-dir ~/.claude/depthfusion/

# Review output: all stores should show records_to_migrate > 0 and records_at_risk == 0

# Live run
python3 scripts/backfill_acl.py --data-dir ~/.claude/depthfusion/

# Verify all records are ACL-stamped
python3 scripts/backfill_acl.py --verify --data-dir ~/.claude/depthfusion/
```

### 8.3 Migrate the Config File

```bash
python3 -m depthfusion migrate v2 --config ~/.claude/depthfusion.env
```

This translator:
- Adds V2 OIDC fields (left blank for you to fill in)
- Translates `DEPTHFUSION_HAIKU_ENABLED=true` to `DEPTHFUSION_RERANKER_BACKEND=haiku`
- Sets `DEPTHFUSION_V2_LEGACY_AUTH=1` temporarily so V1 clients keep working

Fill in the OIDC fields from Section 5, then restart the server.

Once all clients have migrated to the desktop app, remove the legacy auth flag:

```bash
# In ~/.depthfusion.env, delete or set to 0:
DEPTHFUSION_V2_LEGACY_AUTH=0
```

### 8.4 Confirm V1 sync.sh is Retired

```bash
bash sync.sh 2>&1 | grep -q "ERROR: sync.sh is retired" && echo "Frozen OK" || echo "NOT FROZEN — check the file"
```

The V1 `sync.sh` wholesale rsync is retired in V2. If you need emergency read-only access to old sync behavior, set `DEPTHFUSION_SYNC_OVERRIDE=1` (not recommended in production).

---

## 9. Verification

### 9.1 Server Health

```bash
# Local (before TLS)
curl http://localhost:7300/v2/health

# With TLS
curl https://depthfusion.yourdomain.com/v2/health
```

Expected response:

```json
{
  "status": "healthy",
  "version": "2.0.0",
  "databases": "ok",
  "oidc_provider": "reachable",
  "device_ca": "ok"
}
```

### 9.2 List Enrolled Devices

```bash
python3 -m depthfusion.admin.devices --status active
```

You should see your device listed after completing the desktop app enrollment.

### 9.3 Run the Integration Smoke Test

```bash
bash scripts/integration_smoke_test.sh
```

This script verifies:
- OIDC discovery endpoint is reachable
- JWKS endpoint returns valid keys
- Database integrity checks pass
- Device CA is healthy
- API token authentication works

### 9.4 Database Integrity Check

```bash
python3 -m depthfusion.admin.verify
```

Checks:
- `PRAGMA integrity_check` on all three SQLite databases
- Event log sequence integrity (no gaps in sequence numbers)
- Config file is valid
- API server health endpoint responds

### 9.5 Verify Audit Logging

```bash
python3 -m depthfusion.admin.query_audit --last 10
```

After any enrollment, sign-in, or device approval, you should see the corresponding audit events.

---

## 10. Troubleshooting

### Server fails to start: OIDC configuration error

```
Error: DEPTHFUSION_OIDC_CLIENT_ID is required but not set
```

Ensure `~/.depthfusion.env` is loaded before starting the server. If using systemd, add an `EnvironmentFile` directive to the service unit:

```ini
[Service]
EnvironmentFile=%h/.depthfusion.env
```

Then reload: `systemctl --user daemon-reload && systemctl --user restart depthfusion-api`

### Desktop app: "Server URL not reachable"

- Verify the server is running: `systemctl --user status depthfusion-api`
- Verify the port is open: `ss -tlnp | grep 7300`
- If using Tailscale: verify both client and server are connected to the same tailnet
- If using a public URL: verify TLS certificate is valid and DNS resolves correctly

### Desktop app: Sign-in completes but device is not enrolled

After signing in, check the server for a pending enrollment:

```bash
python3 -m depthfusion.admin.devices --status pending
```

If your device appears, approve it:

```bash
python3 -m depthfusion.admin.devices --approve <device_id>
```

The first enrolled user is auto-approved (bootstrap mode). Subsequent users require admin approval.

### macOS: "cannot be opened because the developer cannot be verified"

Right-click the app in Finder → Open → Open. For enterprise deployments, sign the app with your Apple Developer certificate and submit it for notarization.

### Windows: missing MSVC build tools (during build)

Install Visual Studio Build Tools from https://visualstudio.microsoft.com/visual-cpp-build-tools/ and select the **Desktop development with C++** workload. Restart PowerShell after installation.

### ChromaDB security advisory

ChromaDB versions ≤1.5.9 contain a known pre-authentication code injection vulnerability (Dependabot #41). There is no upstream patch at the time of writing. Mitigation: ensure ChromaDB is never exposed on a public interface — it operates only within the `vps-cpu`/`vps-gpu` server process and is accessed exclusively via the DepthFusion API layer, which enforces OIDC authentication. The server binds to loopback only by default.

A daily cron job on the VPS monitors for a patched chromadb version:

```bash
# Check manually
pip index versions chromadb 2>/dev/null | head -1
```

Upgrade ChromaDB as soon as a version ≥1.6.0 is available.

### Port conflict on server

```bash
# Find what is using port 7300
ss -tlnp | grep 7300

# Change the port in ~/.depthfusion.env and restart
DEPTHFUSION_API_PORT=7400
systemctl --user restart depthfusion-api
```

### Disk full — SQLite will not start

```bash
df -h ~/.depthfusion/
# Free up space, then restart
systemctl --user restart depthfusion-api
```

---

## Reference

| Document | Location |
|---|---|
| Admin runbooks (devices, roles, backup, audit) | `docs/v2/admin-runbooks.md` |
| Pilot checklist | `docs/v2/pilot-checklist.md` |
| Security model (RBAC, ACL, data classification) | `docs/v2/security-model.md` |
| User guide (desktop app usage) | `docs/v2/user-guide.md` |
| Entra app registration runbook | `docs/runbooks/entra-app-registration.md` |
| VPS CPU quickstart | `docs/install/vps-cpu-quickstart.md` |
| VPS GPU quickstart | `docs/install/vps-gpu-quickstart.md` |
| Environment variable reference | `.env.example` |
| Updater signing key setup | `app/docs/updater-signing-key-setup.md` |

---

*Last updated: 2026-06-11 — DepthFusion V2 initial installation guide.*
