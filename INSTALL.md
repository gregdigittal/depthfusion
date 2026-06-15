# DepthFusion V2 — Installation Guide

DepthFusion V2 is an enterprise knowledge-retrieval platform with three deployable components:

| Component | Who installs it | Platform |
|-----------|-----------------|----------|
| **MCP Server** (Python API + vector store) | IT / DevOps admin | Linux VPS, macOS server |
| **Desktop App** (Tauri 2, React) | End users | macOS 13+, Windows 10/11 |
| **SharePoint Connector** | IT / DevOps admin | Same host as MCP server |

> **Quick links:** [Server setup](#1-mcp-server) · [Desktop app](#2-desktop-app) · [SharePoint connector](#3-sharepoint-connector) · [Environment variables reference](#4-environment-variables) · [Upgrading from V1](#5-upgrading-from-v1) · [Troubleshooting](#6-troubleshooting)

---

## Prerequisites

### Server host (for MCP server + SharePoint connector)

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| CPU | 2 cores | 4 cores |
| RAM | 4 GB | 8 GB |
| Disk | 20 GB SSD | 100 GB SSD |
| Python | 3.11 | 3.12 |
| Network | Reachable by desktop clients | Tailscale or TLS-terminated HTTPS |

macOS 13+ is supported for single-user or development installs.

### Desktop app clients

| Requirement | macOS | Windows |
|-------------|-------|---------|
| OS | macOS 13 (Ventura)+ | Windows 10/11 (64-bit) |
| RAM | 4 GB | 4 GB |
| WebView2 | N/A | Ships with Win 11; auto-installed on Win 10 |

### Azure Entra ID (for OIDC sign-in + optional SharePoint connector)

You need an **App Registration** in your Azure tenant. See [§1.3 Configure OIDC](#13-configure-oidc-sign-in).

---

## 1. MCP Server

### 1.1 Install the server

```bash
# Clone the repository
git clone https://github.com/gregdigittal/depthfusion.git ~/depthfusion
cd ~/depthfusion
git checkout v2.0.0  # replace with the latest release tag

# Create a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows PowerShell

# Install with the VPS extras (chromadb, sentence-transformers, etc.)
pip install -e ".[vps-cpu]"
# For GPU hosts with CUDA 12+: pip install -e ".[vps-gpu]"
```

### 1.2 Run first-time setup

```bash
python3 -m depthfusion.install.install --mode vps-cpu --v2
```

This will:
1. Create `~/.depthfusion/` with database files (`memory_store.db`, `event_log.db`, `audit.db`)
2. Generate the device CA keypair for user certificate enrollment
3. Prompt for OIDC configuration (Entra ID recommended — see §1.3)
4. Write `~/.depthfusion/config.json`
5. Install and enable the systemd service

**Manual configuration** — if you prefer to configure without the interactive installer, create `~/.depthfusion/config.json`:

```json
{
  "mode": "vps-cpu",
  "api_host": "0.0.0.0",
  "api_port": 7300,
  "memory_store_path": "~/.depthfusion/memory_store.db",
  "audit_log_path": "~/.depthfusion/audit.db",
  "oidc": {
    "provider": "azure",
    "tenant_id": "YOUR_TENANT_ID",
    "client_id": "YOUR_APP_CLIENT_ID",
    "discovery_url": "https://login.microsoftonline.com/YOUR_TENANT_ID/v2.0/.well-known/openid-configuration",
    "scopes": ["openid", "email", "profile", "offline_access"]
  }
}
```

> **Security note:** Never use `ANTHROPIC_API_KEY` for DepthFusion — always set `DEPTHFUSION_API_KEY` separately to avoid billing interference with other Claude Code sessions.

### 1.3 Configure OIDC sign-in

**Create an Azure Entra ID App Registration:**

1. Azure Portal → **Entra ID → App registrations → New registration**
2. Name: `DepthFusion`
3. Supported account types: *Accounts in this organizational directory only*
4. Redirect URI: `depthfusion://auth/callback` (Custom URI scheme — select "Mobile and desktop applications")
5. After creating, note the **Application (client) ID** and **Directory (tenant) ID**

**Configure API permissions (for sign-in only):**

| Permission | Type | Purpose |
|------------|------|---------|
| `openid` | Delegated | ID token |
| `email` | Delegated | Email claim in token |
| `profile` | Delegated | Name claims |
| `offline_access` | Delegated | Refresh token |

Grant admin consent for the organization.

**Set the OIDC config** in `~/.depthfusion/config.json` (see template above) or via env vars:

```bash
export DEPTHFUSION_OIDC_TENANT_ID="your-tenant-id"
export DEPTHFUSION_OIDC_CLIENT_ID="your-app-client-id"
```

### 1.4 Start the MCP server

**As a systemd service (recommended for Linux):**

```bash
# The installer creates the service; enable and start it:
sudo systemctl enable depthfusion-mcp
sudo systemctl start depthfusion-mcp
sudo systemctl status depthfusion-mcp
```

**Manually (development or macOS):**

```bash
cd ~/depthfusion
source .venv/bin/activate
DEPTHFUSION_MODE=vps-cpu python3 -m depthfusion.mcp.server
```

**As a Claude Code MCP server** (local development):

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "depthfusion": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "depthfusion.mcp.server"],
      "env": {
        "DEPTHFUSION_MODE": "vps-cpu",
        "DEPTHFUSION_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

Then restart Claude Code and run `/mcp` to confirm the server appears.

### 1.5 TLS and reverse proxy (production)

For production, terminate TLS in front of the MCP server with nginx or Caddy:

**Caddy (recommended — automatic Let's Encrypt):**

```
depthfusion.yourcompany.com {
    reverse_proxy localhost:7300
}
```

**nginx:**

```nginx
server {
    listen 443 ssl;
    server_name depthfusion.yourcompany.com;

    ssl_certificate /etc/letsencrypt/live/depthfusion.yourcompany.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/depthfusion.yourcompany.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:7300;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

> **Network binding:** The MCP server binds to `127.0.0.1:7300` by default. The reverse proxy is the only listener on a public interface. Never expose port 7300 directly to the internet.

### 1.6 Enroll the first admin user

```bash
python3 -m depthfusion.install.enroll --role owner --email admin@yourcompany.com
```

This creates an `owner`-role principal and prints an enrollment token. The first user who signs in with a matching email is auto-granted the owner role.

**Role hierarchy:**

| Role | Can do |
|------|--------|
| `owner` | All operations; manage all users; rotate keys |
| `admin` | Read all records; manage members/viewers; run ingestion |
| `member` | Read records where their principal_id is in `acl_allow` |
| `viewer` | Read own records; no write |

### 1.7 Verify the server is running

```bash
python3 -m depthfusion.mcp.tools.system status
# Expected output: {"status": "ok", "mode": "vps-cpu", "tools_enabled": N}
```

Or from the desktop app: the status indicator in the bottom-right corner turns green when the server is reachable.

---

## 2. Desktop App

### 2.1 Install on macOS

**System requirements:** macOS 13 (Ventura) or later · 4 GB RAM · 500 MB disk

1. Download `DepthFusion-<version>-macos-universal.dmg` from the [GitHub releases page](https://github.com/gregdigittal/depthfusion/releases).
2. Open the `.dmg` and drag **DepthFusion** to Applications.
3. First launch: if macOS shows *"DepthFusion cannot be opened because it is from an unidentified developer"*:
   - Go to **System Settings → Privacy & Security** → click **Open Anyway**
   - Or right-click → **Open** in Finder

**Auto-updates:** The app checks for updates on launch and shows a notification when one is available. Updates are signed with the same key as the release.

### 2.2 Install on Windows

**System requirements:** Windows 10/11 (64-bit) · 4 GB RAM · 500 MB disk

1. Download `DepthFusion-<version>-windows-x64-setup.exe`.
2. Run the installer. SmartScreen may prompt — click **More info → Run anyway**.
3. The app installs to `%LOCALAPPDATA%\DepthFusion\`.

**Silent install for IT deployment:**

```powershell
DepthFusion-<version>-windows-x64-setup.exe /S /ServerUrl=https://depthfusion.yourcompany.com
```

**MSI distribution (Intune / SCCM):** A `.msi` build is available in CI artifacts under `depthfusion-win-x64-msi`.

### 2.3 First-time setup

1. Launch DepthFusion.
2. Enter the **server address** your admin provided (e.g., `https://depthfusion.yourcompany.com` or the Tailscale hostname).
3. Click **Sign in with Microsoft** (or your configured identity provider).
4. Complete the browser-based OIDC flow — DepthFusion opens a system browser tab, authenticates, and returns a deep-link callback to the app.
5. Your session is stored in the OS keychain (macOS Keychain / Windows DPAPI). It persists across relaunches until you explicitly sign out.

### 2.4 Build from source (developers)

**Prerequisites:**
- Rust 1.77+ (`rustup show`)
- Node.js 20+ (`node --version`)
- Tauri CLI v2: `npm install -g @tauri-apps/cli@^2`
- macOS: Xcode Command Line Tools (`xcode-select --install`)
- Windows: MSVC Build Tools (Visual Studio 2019+)

```bash
cd depthfusion/app
npm install
npm run dev          # Hot-reload dev build (opens Vite DevTools)
npm run build        # Production TypeScript build + Vite bundle
npx tauri build      # Compile Rust + bundle installer
```

**macOS universal binary:**
```bash
rustup target add aarch64-apple-darwin x86_64-apple-darwin
npx tauri build --target universal-apple-darwin
```

**Signing for distribution:**

Before any public release, rotate the updater signing key:

```bash
npx tauri signer generate -w ~/.tauri/depthfusion-signing.key
# Output: public key string
```

Set the public key in `app/src-tauri/tauri.conf.json` under `plugins.updater.pubkey`, and store the private key as the `TAURI_SIGNING_PRIVATE_KEY` GitHub Actions secret.

> **Critical:** The bootstrap signing key in the repository is for CI pipeline bootstrapping only. It MUST be replaced before any public or production release.

---

## 3. SharePoint Connector

### 3.1 Azure App Registration for Graph API

The SharePoint connector uses a service-principal credential (client ID + certificate) for Microsoft Graph. You need a **separate App Registration** from the OIDC one (or extend the same one with the extra permissions).

**In Azure Portal → Entra ID → App registrations → your DepthFusion app:**

1. Go to **Certificates & secrets → Certificates** → upload or generate a certificate.
   - To generate: `openssl req -x509 -newkey rsa:4096 -keyout graph-client.key -out graph-client.crt -days 730 -nodes`
   - Upload `graph-client.crt` to the Azure app registration.
2. Store `graph-client.key` on the server at a path referenced by `DEPTHFUSION_SHAREPOINT_CERT_PATH`.

**API permissions required:**

| Permission | Type | Purpose |
|------------|------|---------|
| `Sites.Selected` | Application | Read only the sites you explicitly select |
| `Files.Read.All` | Application | Read file content and metadata |
| `User.Read.All` | Application | Resolve user IDs for ACL mapping |

> **Prefer `Sites.Selected` over `Sites.ReadWrite.All`.** It limits blast radius to only the sites you register — a SharePoint admin must grant per-site consent in the SharePoint admin center.

**Grant admin consent** for all three permissions (requires Azure Global Admin or a SharePoint Admin + an Entra Admin).

**Grant per-site access** (for `Sites.Selected`):

```powershell
# Run in Azure Cloud Shell or local PowerShell with PnP Online module
Connect-PnPOnline -Url https://yourcompany.sharepoint.com/sites/MySite -Interactive
Grant-PnPAzureADAppSitePermission `
    -AppId <your-app-client-id> `
    -DisplayName "DepthFusion" `
    -Permissions Read
```

### 3.2 Configure the connector

Set these environment variables on the server (or in `~/.depthfusion/.env`):

```bash
DEPTHFUSION_SHAREPOINT_TENANT_ID="your-tenant-id"
DEPTHFUSION_SHAREPOINT_CLIENT_ID="your-graph-app-client-id"
DEPTHFUSION_SHAREPOINT_CERT_PATH="/home/depthfusion/.depthfusion/graph-client.key"
DEPTHFUSION_SHAREPOINT_CERT_THUMBPRINT="ABCDEF0123456789..."  # SHA-1 thumbprint from Azure
```

### 3.3 Register sites for ingestion

```bash
source .venv/bin/activate
python3 -m depthfusion.connectors.sharepoint_scope add \
    --site-url "https://yourcompany.sharepoint.com/sites/MySite" \
    --drive-id "b!ABC..." \
    --label "MySite Documents"
```

List registered sites:
```bash
python3 -m depthfusion.connectors.sharepoint_scope list
```

Remove a site:
```bash
python3 -m depthfusion.connectors.sharepoint_scope remove \
    --site-url "https://yourcompany.sharepoint.com/sites/MySite"
```

### 3.4 Run the initial crawl

```bash
python3 -m depthfusion.connectors.sharepoint sync --mode initial
```

This walks all registered drives and downloads every file. Progress is printed to stdout; a journal file at `~/.depthfusion/sharepoint_sync.lock` prevents concurrent runs.

Estimated time: ~5–10 minutes per 1,000 documents (depends on file sizes and Graph API throttle rate). The connector automatically backs off on 429/503 responses using the `Retry-After` header.

### 3.5 Schedule incremental sync

The connector uses Microsoft Graph delta queries — after the initial crawl, only changed items are fetched. Delta tokens are persisted per (tenant, site, drive) in `~/.depthfusion/sharepoint_cursors.json`.

**Add a cron job for incremental sync:**

```bash
# Sync every hour (edit with: crontab -e)
0 * * * * /home/depthfusion/depthfusion/.venv/bin/python \
    -m depthfusion.connectors.sharepoint sync --mode incremental \
    >> /var/log/depthfusion-sharepoint.log 2>&1
```

Or use the scheduler module (handles the file lock automatically):

```bash
python3 -m depthfusion.connectors.sharepoint_scheduler --interval-minutes 60
```

**Check sync status:**

```bash
python3 -m depthfusion.connectors.sharepoint status
```

Output shows last sync time, delta cursor health, document count, and recent errors.

---

## 4. Environment Variables

All variables can be set as shell exports or placed in `~/.depthfusion/.env`. The `.env` file is loaded at startup and must not be committed to git.

### Core server

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEPTHFUSION_MODE` | Yes | `local` | `local`, `vps-cpu`, or `vps-gpu` |
| `DEPTHFUSION_API_KEY` | Yes | — | API key for the Claude model used by DepthFusion tools |
| `DEPTHFUSION_API_HOST` | No | `127.0.0.1` | Bind address for the HTTP API |
| `DEPTHFUSION_API_PORT` | No | `7300` | HTTP API port |
| `DEPTHFUSION_MEMORY_STORE_PATH` | No | `~/.depthfusion/memory_store.db` | ChromaDB persistent store path |
| `DEPTHFUSION_AUDIT_LOG_PATH` | No | `~/.depthfusion/audit.db` | Audit log SQLite path |
| `DEPTHFUSION_AUTONOMIC` | No | `0` | Set to `1` to enable background memory consolidation |
| `DEPTHFUSION_CONSOLIDATION_INTERVAL_MINUTES` | No | `30` | Consolidation daemon interval |

### OIDC / identity

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEPTHFUSION_OIDC_TENANT_ID` | Yes (OIDC) | — | Azure Entra ID tenant ID |
| `DEPTHFUSION_OIDC_CLIENT_ID` | Yes (OIDC) | — | App registration client ID |
| `DEPTHFUSION_OIDC_DISCOVERY_URL` | No | Auto-derived from tenant ID | Full `.well-known/openid-configuration` URL |

### SharePoint connector

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEPTHFUSION_SHAREPOINT_TENANT_ID` | Yes (SP) | — | Azure tenant ID |
| `DEPTHFUSION_SHAREPOINT_CLIENT_ID` | Yes (SP) | — | App registration client ID for Graph API |
| `DEPTHFUSION_SHAREPOINT_CERT_PATH` | Yes (SP) | — | Path to the PEM private key for Graph auth |
| `DEPTHFUSION_SHAREPOINT_CERT_THUMBPRINT` | Yes (SP) | — | SHA-1 thumbprint of the certificate |

> **Never set `ANTHROPIC_API_KEY` for DepthFusion.** The Claude model used by DepthFusion tools should be authenticated via `DEPTHFUSION_API_KEY` only. Setting `ANTHROPIC_API_KEY` will route charges to your Claude Code session's billing account.

---

## 5. Upgrading from V1

See `docs/v2/sync-migration-runbook.md` for the full migration procedure. Summary:

1. **Back up** `~/.depthfusion/` before upgrading.
2. Upgrade the package: `pip install -e . --upgrade`
3. Run schema migration: `python3 -m depthfusion.install.migrate --v1-to-v2`
4. The V2 schema adds ACL columns (`acl_allow`, `acl_deny`), `classification`, and the SharePoint delta-cursor tables.
5. Existing records receive `acl_allow: [owner_principal_id]` as the backfill default.
6. Restart the MCP server.

**Rollback:** Restore the `~/.depthfusion/` backup and reinstall the V1 package. The migration is not reversible in-place.

---

## 6. Troubleshooting

### Server won't start: "module not found"

```
ModuleNotFoundError: No module named 'depthfusion'
```

You are running Python outside the virtualenv. Activate it:

```bash
source ~/depthfusion/.venv/bin/activate
python3 -m depthfusion.mcp.server
```

Or ensure PYTHONPATH includes `src/`:

```bash
PYTHONPATH=~/depthfusion/src python3 -m depthfusion.mcp.server
```

### SharePoint: delta token expired

If incremental sync fails with `410 Gone` or "delta token expired":

```bash
python3 -m depthfusion.connectors.sharepoint sync --mode initial --reset-cursors
```

This clears all stored delta tokens and re-runs the full crawl. Delta tokens expire after 30 days of no activity.

### SharePoint: lock file exists

```
SyncLock: another sync process is running (PID 12345)
```

If the previous run crashed without releasing the lock:

```bash
python3 -m depthfusion.connectors.sharepoint sync --break-lock
```

The lock is PID-aware: if PID 12345 is not running, the lock is automatically cleared on the next sync attempt.

### Desktop app: "server unreachable"

1. Verify the MCP server is running: `sudo systemctl status depthfusion-mcp`
2. Check the server address in app Settings matches the configured host/port.
3. If using Tailscale, confirm the device is connected: `tailscale status`
4. Check the TLS certificate is valid: `curl -v https://depthfusion.yourcompany.com/health`

### Desktop app: sign-in loop (OIDC redirect fails)

The deep-link URI scheme `depthfusion://` must be registered at the OS level. This is handled automatically by the installer. If it fails:

**macOS:** `open depthfusion://auth/test` — if the app opens, the scheme is registered.

**Windows:** Check `HKCU\Software\Classes\depthfusion` in the registry. Re-run the installer if the key is missing.

### "Authorization denied" when calling tools

Your principal is not enrolled or lacks the required role. An admin must run:

```bash
python3 -m depthfusion.install.enroll \
    --email your@company.com \
    --role member
```

### Tests failing after install

```bash
cd ~/depthfusion
PYTHONPATH=./src python3 -m pytest tests/ -x -q \
    --ignore=tests/test_benchmark \
    --ignore=tests/test_benchmarks \
    --ignore=tests/test_integration
```

All tests should pass. If `test_pin.py` fails with an ImportError for `_TOOL_FLAGS`, ensure you are on a commit after `8537142` (v2.0.0+).

---

## 7. Health Checks and Monitoring

### MCP server health endpoint

```bash
curl http://localhost:7300/health
# {"status": "ok", "version": "0.4.0", "mode": "vps-cpu"}
```

### systemd journal

```bash
journalctl -u depthfusion-mcp -f       # follow live
journalctl -u depthfusion-mcp --since "1 hour ago"
```

### SharePoint sync observability

```bash
python3 -m depthfusion.connectors.sharepoint status
# Prints: last sync time, documents indexed, delta cursor state, error count
```

### Key log locations

| Log | Path |
|-----|------|
| MCP server | `journalctl -u depthfusion-mcp` (systemd) or stdout |
| SharePoint sync | `/var/log/depthfusion-sharepoint.log` (if using cron) |
| Audit log | `~/.depthfusion/audit.db` (SQLite; query with `sqlite3`) |

---

## 8. Security Hardening Checklist

Before going to production:

- [ ] TLS certificate is valid and auto-renewing (Caddy or certbot)
- [ ] Port 7300 is NOT exposed directly — only the reverse proxy is public-facing
- [ ] `~/.depthfusion/.env` permissions are `600` (`chmod 600 ~/.depthfusion/.env`)
- [ ] Tauri updater signing key has been rotated from the bootstrap key (`npx tauri signer generate`)
- [ ] `TAURI_SIGNING_PRIVATE_KEY` GitHub secret has been set to the new private key
- [ ] Full-disk encryption is enabled on the server host
- [ ] OIDC redirect URI uses `depthfusion://auth/callback` (not a wildcard)
- [ ] `Sites.Selected` is used (not `Sites.ReadWrite.All`) for SharePoint
- [ ] Graph API certificate thumbprint matches the cert in Azure portal
- [ ] First admin user enrolled and bootstrap credentials changed
- [ ] `DEPTHFUSION_API_KEY` is set and NOT the same as `ANTHROPIC_API_KEY`

---

*For advanced configuration, deployment automation, and migration from V1, see `docs/v2/admin-runbooks.md` and `docs/v2/sync-migration-runbook.md`.*
