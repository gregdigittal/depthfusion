# DepthFusion V2 — Pilot Checklist

> **Purpose:** Structured checklist for running a V2 pilot on real (scoped) SharePoint data.  
> **Audience:** Pilot operator and participating team members.  
> **Prerequisite:** G1 gate must be declared PASS before starting the pilot.  
> **Related:** `docs/v2/merge-plan.md`, `docs/plans/G1-gate.md`, `docs/decisions/sync-v2-design.md`

---

## 1. Infrastructure Requirements

### Compute

- [ ] **VPS / server:** Linux (Ubuntu 22.04 LTS or Debian 12 recommended), ≥ 8 GB RAM, ≥ 4 vCPUs
  - For GPU-accelerated embedding (optional): RTX 3090 / A4000 or equivalent; 24 GB VRAM for Gemma 7B
- [ ] **Mac client:** macOS 13 (Ventura) or later; Apple Silicon (M1/M2/M3) or Intel
- [ ] **Windows client:** Windows 10 22H2 or later (x64)
- [ ] **Network:** Clients reach VPS over Tailscale (recommended) or a TLS-terminated reverse proxy
- [ ] **Disk:** ≥ 50 GB free on VPS for corpus storage + ChromaDB indices

### Software stack (VPS)

- [ ] Python 3.11 or 3.12
- [ ] `pip install -e ".[vps-cpu]"` (or `[vps-gpu]` for GPU mode)
- [ ] `uvicorn` installed (pulled by `[vps-cpu]`)
- [ ] `sqlite3` ≥ 3.35 (ships with Python 3.11+)
- [ ] Redis 7.x (optional; required only for pub/sub bus backend)
  - If used: must bind `127.0.0.1` only per infra-exposure policy
- [ ] Tailscale installed and authenticated on VPS and all client machines

### Software stack (Desktop clients — Lane C)

- [ ] DepthFusion desktop app built from `app/` (Tauri 2 + React)
  - Mac: universal binary from `tauri-build.yml` CI artifact
  - Windows: x64 binary from same workflow
- [ ] App signed and notarized (required on macOS Gatekeeper systems)
- [ ] App updater configured (see `app/docs/updater-signing-key-setup.md`)

---

## 2. Entra ID / OIDC App Registration

### 2.1 Create the app registration (test tenant)

- [ ] Log in to [Azure Portal](https://portal.azure.com) with a global-admin account in the **test tenant** (never the production tenant for pilots)
- [ ] Navigate to **Azure Active Directory → App registrations → New registration**
- [ ] Set **Name:** `DepthFusion V2 Pilot`
- [ ] Set **Supported account types:** `Accounts in this organizational directory only`
- [ ] Set **Redirect URI:** `http://localhost:8400/callback` (for desktop app PKCE flow)
- [ ] Click **Register**; record the **Application (client) ID** and **Directory (tenant) ID**

### 2.2 Configure the app

- [ ] **API permissions:** Add `Microsoft Graph → Sites.Selected` (application permission) for SharePoint access
- [ ] **API permissions:** Add `Microsoft Graph → User.Read` (delegated)
- [ ] **API permissions:** Grant admin consent for all permissions
- [ ] **Authentication:** Enable **Allow public client flows** (for device-code flow on VPS)
- [ ] **Expose an API:** Set Application ID URI (optional; used as token audience)
  - Example: `api://<client-id>`

### 2.3 Set DepthFusion environment variables

Copy and fill `.env.example` into `~/.claude/depthfusion.env` on the VPS:

```bash
DEPTHFUSION_OIDC_CLIENT_ID=<Application (client) ID from step 2.1>
DEPTHFUSION_OIDC_TENANT_ID=<Directory (tenant) ID from step 2.1>
DEPTHFUSION_OIDC_AUDIENCE=api://<client-id>   # or the client ID itself
DEPTHFUSION_OIDC_SCOPE=https://graph.microsoft.com/.default
```

- [ ] Verify the installer writes the correct values: `python3 -m depthfusion.install.install --mode vps-cpu`
- [ ] Run the V2 integration smoke test: `bash scripts/integration_smoke_test.sh`

### 2.4 Sites.Selected consent (SharePoint pilot site)

- [ ] In Azure Portal: navigate to the **SharePoint site** used for the pilot
- [ ] Grant `Sites.Selected` read permission to the DepthFusion app registration
  - Microsoft Graph API: `POST /v1.0/sites/{site-id}/permissions`
  - See `docs/runbooks/entra-app-registration.md` for the full command
- [ ] Verify access: `python3 -c "from depthfusion.connectors.sharepoint import SharePointClient; c = SharePointClient.from_env(); print(c.list_sites())"`

---

## 3. First-Time Data Migration from V1

### 3.1 Pre-migration snapshot

- [ ] Stop any running V1 DepthFusion processes: `pkill -f depthfusion` (or use systemd: `systemctl stop depthfusion`)
- [ ] Create a full backup of V1 data:
  ```bash
  BACKUP_DIR=~/.claude/depthfusion-v1-backup-$(date +%Y%m%d)
  cp -r ~/.claude/shared/discoveries/ "${BACKUP_DIR}/discoveries"
  cp -r ~/.claude/sessions/ "${BACKUP_DIR}/sessions"
  cp ~/.claude/depthfusion.env "${BACKUP_DIR}/depthfusion.env.bak" 2>/dev/null || true
  ```
- [ ] Record the backup path in `docs/decisions/` as evidence

### 3.2 Schema migration (ACL backfill)

V2 adds `acl_allow` and `classification` columns to all six data stores. The backfill script stamps legacy records with `acl_allow=[owner]` and `classification=internal`, so they are accessible only by the owner principal.

- [ ] Dry-run the backfill to verify no data loss:
  ```bash
  python3 scripts/backfill_acl.py --dry-run --data-dir ~/.claude/depthfusion/
  ```
- [ ] Review the dry-run output: all six stores should show `records_to_migrate` > 0 and `records_at_risk` == 0
- [ ] Run the live backfill:
  ```bash
  python3 scripts/backfill_acl.py --data-dir ~/.claude/depthfusion/
  ```
- [ ] Verify: `python3 scripts/backfill_acl.py --verify --data-dir ~/.claude/depthfusion/` shows all records ACL-stamped
- [ ] Confirm count reconciliation: pre-migration baseline == post-migration total for all six stores

### 3.3 Config migration

- [ ] Run the config translator: `python3 -m depthfusion migrate v2 --config ~/.claude/depthfusion.env`
  - Adds V2 OIDC fields (left blank for manual entry)
  - Translates V1 `DEPTHFUSION_HAIKU_ENABLED=true` → `DEPTHFUSION_RERANKER_BACKEND=haiku`
  - Sets `DEPTHFUSION_V2_LEGACY_AUTH=1` temporarily so existing integrations keep working
- [ ] Fill in the OIDC fields (step 2.3) in the migrated env file
- [ ] Remove `DEPTHFUSION_V2_LEGACY_AUTH=1` once all clients use the desktop app

### 3.4 V1 sync.sh retirement

- [ ] Confirm `sync.sh` is frozen (exits non-zero):
  ```bash
  bash sync.sh 2>&1 | grep -q "ERROR: sync.sh is retired" && echo "Frozen OK" || echo "NOT FROZEN"
  ```
- [ ] Record the result as evidence for G1 C6

---

## 4. Rollback Procedure

If a critical issue is found during the pilot that cannot be fixed within the pilot window, roll back to V1:

### 4.1 Restore V1 data

```bash
# Assumes backup created in step 3.1
BACKUP_DIR=~/.claude/depthfusion-v1-backup-YYYYMMDD  # fill in date
cp -r "${BACKUP_DIR}/discoveries/" ~/.claude/shared/
cp "${BACKUP_DIR}/depthfusion.env.bak" ~/.claude/depthfusion.env
```

### 4.2 Restore V1 binary / process

```bash
git checkout main -- src/depthfusion/   # if running from source
# OR
pip install depthfusion==1.2.2          # pin to last V1 release
```

### 4.3 Verify V1 is healthy

```bash
python3 -m depthfusion.mcp.server &
python3 -m depthfusion.install.install --mode local
bash scripts/install-git-hook.sh --verify
```

### 4.4 Record the rollback

- [ ] File a post-mortem in `docs/decisions/V2-PILOT-ROLLBACK-<date>.md`
- [ ] List the specific failure mode, affected data (if any), and fix required before re-running the pilot
- [ ] Update the BACKLOG.md story that was blocking the pilot (add `[x]` only after confirmed fix)

---

## 5. Pilot Success Metrics

The 2-week pilot is considered successful when all of the following are met:

| Metric | Target | Measurement |
|--------|--------|-------------|
| Search relevance rating | ≥ 4.0/5.0 average across ≥ 20 queries | Pilot feedback form |
| Offline hit rate | ≥ 70% of queries answered from local cache | `GET /metrics` on each device |
| Authorization incidents | 0 (zero) | Audit log `GET /v2/admin/audit` |
| App crash rate | < 1 per device per day | Sentry / app telemetry |
| Pilot participant count | ≥ 3 team members | Attendance log |
| Platform coverage | Mac + Windows both tested | Sign-off from both platform users |

---

## 6. Feedback Triage

After the pilot, review all feedback and categorize:

- **Fix before merge:** Security issues, data-access bugs, sign-in failures, crash-on-launch
- **Fix before GA:** UX polish, performance regressions, missing error messages
- **Post-merge backlog:** Nice-to-have features, future integrations

File each "fix before merge" item as a backlog story (use `backlog-intake.md` protocol) and block the merge gate (S-205 T-697) until all are resolved.
