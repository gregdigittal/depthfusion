# DepthFusion V2 — Admin Runbooks

Operational runbooks for DepthFusion V2 administrators. These procedures assume you have `admin` role access and shell access to the host running the API server.

---

## 1. Initial Server Setup + First-User Enrollment

### Prerequisites

Before starting:
- [ ] Ubuntu 22.04 or 24.04 LTS (or macOS 13+ for single-user installs)
- [ ] Python 3.11–3.13 installed (`python3 --version`)
- [ ] Full-disk encryption enabled (FileVault / LUKS / BitLocker)
- [ ] TLS certificate provisioned (Let's Encrypt recommended; see §1.3)
- [ ] DNS A record pointing to the server's public IP (for TLS + Tailscale)
- [ ] Ports 443 (HTTPS) and 7300 (API, if not proxied) open in firewall

### 1.1 Install DepthFusion V2

```bash
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion
git checkout v2-enterprise  # or the released v2.x.x tag when available

python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[vps-cpu]'  # or .[vps-gpu] for GPU hosts

# Run the V2 installer
python3 -m depthfusion.install.install --mode vps-cpu --v2
```

The installer will:
1. Create the database files (`memory_store.db`, `event_log.db`, `audit.db`) in `~/.depthfusion/`
2. Generate the device CA keypair for device enrollment certificates
3. Prompt for OIDC provider configuration (see §1.2)
4. Write `~/.depthfusion/config.json` with all settings
5. Install and enable the systemd service (see §1.4)

### 1.2 Configure OIDC Provider

During install, or by editing `~/.depthfusion/config.json`:

```json
{
  "oidc": {
    "provider": "azure",
    "tenant_id": "YOUR_TENANT_ID",
    "client_id": "YOUR_APP_CLIENT_ID",
    "discovery_url": "https://login.microsoftonline.com/YOUR_TENANT_ID/.well-known/openid-configuration",
    "scopes": ["openid", "email", "profile", "offline_access"]
  }
}
```

**For Azure Entra ID — create the app registration:**

1. Azure Portal → Entra ID → App registrations → New registration
2. Name: `DepthFusion`
3. Supported account types: `Accounts in this organizational directory only`
4. Redirect URI: `http://127.0.0.1` (Mobile and desktop applications platform)
5. After creation:
   - Go to Authentication → Add platform → Mobile and desktop → add `http://127.0.0.1`
   - Enable "Allow public client flows"
   - API permissions: `openid`, `email`, `profile`, `offline_access` (all delegated, Microsoft Graph)
6. Copy the Application (client) ID and paste into `config.json` `client_id`
7. Copy the Directory (tenant) ID and paste into `config.json` `tenant_id`

### 1.3 TLS Certificate Setup

**Let's Encrypt (recommended):**

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d depthfusion.yourdomain.com
# Certificates written to /etc/letsencrypt/live/depthfusion.yourdomain.com/

# Configure DepthFusion to use them
cat > ~/.depthfusion/tls.json <<EOF
{
  "cert_file": "/etc/letsencrypt/live/depthfusion.yourdomain.com/fullchain.pem",
  "key_file": "/etc/letsencrypt/live/depthfusion.yourdomain.com/privkey.pem"
}
EOF

# Auto-renewal (certbot installs a systemd timer automatically; verify it)
sudo systemctl list-timers | grep certbot
```

**Add a DepthFusion post-renewal hook** to restart the API server after renewal:

```bash
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-depthfusion.sh <<'EOF'
#!/bin/bash
systemctl restart depthfusion-api
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-depthfusion.sh
```

### 1.4 Start the API Server

```bash
# Install the systemd service (done by installer; manual steps if needed)
cp ~/projects/depthfusion/infra/systemd/depthfusion-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now depthfusion-api

# Verify
systemctl --user status depthfusion-api
curl -s https://depthfusion.yourdomain.com/v2/health | python3 -m json.tool
```

Expected health response:
```json
{
  "status": "healthy",
  "version": "2.0.0",
  "databases": "ok",
  "oidc_provider": "reachable",
  "device_ca": "ok"
}
```

### 1.5 Bootstrap First Admin User

The first user to enroll is automatically granted `admin` role (bootstrap mode). Bootstrap mode is active until the first admin user exists.

```bash
# Install the desktop app on your machine (see user-guide.md)
# Sign in — you will be auto-approved and granted admin role
# Verify in the server audit log:
python3 -m depthfusion.admin.query_audit --last 5
```

After the first admin exists, bootstrap mode is disabled and all subsequent enrollments receive the default role (`contributor` unless changed via `DEPTHFUSION_DEFAULT_ROLE`).

---

## 2. Adding / Revoking Device Access

### 2.1 Review Pending Enrollments

```bash
python3 -m depthfusion.admin.devices --status pending
```

Output:
```
PENDING DEVICE ENROLLMENTS
──────────────────────────
device_id  principal         device_name       platform   enrolled_at
abc-123    alice@example.com Alice's MacBook   macOS      2026-06-11T09:00Z
def-456    bob@example.com   Bob's Laptop      Windows    2026-06-11T09:05Z
```

### 2.2 Approve a Device

```bash
python3 -m depthfusion.admin.devices --approve abc-123
# Optional: set role at approval time
python3 -m depthfusion.admin.devices --approve abc-123 --role operator
```

Or via the REST API:
```bash
curl -X POST https://depthfusion.yourdomain.com/v2/devices/abc-123/approve \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "contributor"}'
```

### 2.3 Revoke a Device

Revoking a device immediately blocks all API access from that device. The user's account is not affected — they can re-enroll from the same or a different device.

```bash
python3 -m depthfusion.admin.devices --revoke abc-123 --reason "Device reported lost"
```

The revocation is written to the audit log and takes effect immediately. The device's refresh token is invalidated server-side within one token refresh cycle (max 1 hour).

**To force immediate invalidation** (e.g. after a security incident):
```bash
python3 -m depthfusion.admin.devices --revoke abc-123 --force-invalidate
```

This sends a push notification to the device instructing it to clear its token cache immediately.

### 2.4 List All Devices for a User

```bash
python3 -m depthfusion.admin.devices --user alice@example.com
```

### 2.5 Emergency: Revoke All Devices for a User

```bash
python3 -m depthfusion.admin.devices --revoke-all-for alice@example.com --reason "Account compromise"
```

---

## 3. Role Assignment

### 3.1 View Current Role

```bash
python3 -m depthfusion.admin.users --show alice@example.com
```

Output:
```
User: alice@example.com
Principal ID: usr-abc-123
Current role: contributor
Devices: 2 active, 0 pending, 1 revoked
ACLs: 3 explicit records
```

### 3.2 Change Role

```bash
# Promote
python3 -m depthfusion.admin.users --set-role alice@example.com --role operator

# Demote
python3 -m depthfusion.admin.users --set-role alice@example.com --role contributor
```

Role changes take effect within one token refresh cycle (max 15 minutes). To force immediate effect, also revoke and re-approve the user's devices.

### 3.3 Valid Roles

`viewer` | `contributor` | `operator` | `admin`

See `docs/v2/security-model.md` §2.1 for the full capability matrix.

### 3.4 Grant Project-Scoped Operator Access

To give a user operator-level access to one project without promoting them globally:

```bash
python3 -m depthfusion.admin.acl --grant \
  --subject alice@example.com \
  --resource-type project \
  --resource-id my-project-slug \
  --permission write
```

---

## 4. Backup and Restore

### 4.1 What to Back Up

| Path | Contents | Frequency |
|---|---|---|
| `~/.depthfusion/memory_store.db` | All memory entries, tags, scores | Daily |
| `~/.depthfusion/event_log.db` | Append-only event log (source of truth) | Daily |
| `~/.depthfusion/audit.db` | Audit log | Daily |
| `~/.depthfusion/projects.json` | Project registry | Daily |
| `~/.depthfusion/config.json` | Server configuration (no secrets) | On change |
| `~/.depthfusion/certs/` | Device CA certificate (public cert only) | On change |
| `~/.claude/shared/discoveries/` | Discovery files | Daily |

Do **not** back up `~/.depthfusion/.keystore` (the encrypted key material) — it is machine-specific and the keys are derived from device identity. Restoring it to a different machine would not work.

### 4.2 Create a Backup

```bash
#!/bin/bash
# backup-depthfusion.sh
BACKUP_DIR="/backups/depthfusion/$(date -u +%Y-%m-%d)"
mkdir -p "$BACKUP_DIR"

# Checkpoint WAL before backup (required for SQLite WAL mode)
python3 -m depthfusion.admin.checkpoint

# Copy databases (SQLite is safe to copy after checkpoint)
cp ~/.depthfusion/memory_store.db   "$BACKUP_DIR/"
cp ~/.depthfusion/event_log.db      "$BACKUP_DIR/"
cp ~/.depthfusion/audit.db          "$BACKUP_DIR/"
cp ~/.depthfusion/projects.json     "$BACKUP_DIR/"
cp ~/.depthfusion/config.json       "$BACKUP_DIR/"
cp -r ~/.depthfusion/certs/         "$BACKUP_DIR/certs/"
rsync -a ~/.claude/shared/discoveries/ "$BACKUP_DIR/discoveries/"

# Compress
tar -czf "$BACKUP_DIR.tar.gz" "$BACKUP_DIR"
rm -rf "$BACKUP_DIR"

echo "Backup written to $BACKUP_DIR.tar.gz"
```

Add to cron (`crontab -e`):
```
0 2 * * * /home/gregmorris/scripts/backup-depthfusion.sh >> /var/log/depthfusion-backup.log 2>&1
```

### 4.3 Restore from Backup

```bash
#!/bin/bash
# restore-depthfusion.sh <backup_file.tar.gz>
BACKUP_TAR="$1"
RESTORE_DIR="/tmp/df-restore-$(date +%s)"

# Stop the API server
systemctl --user stop depthfusion-api

# Extract backup
mkdir -p "$RESTORE_DIR"
tar -xzf "$BACKUP_TAR" -C "$RESTORE_DIR"
BACKUP_CONTENTS=$(ls "$RESTORE_DIR")

# Restore databases
cp "$RESTORE_DIR/$BACKUP_CONTENTS/memory_store.db"  ~/.depthfusion/memory_store.db
cp "$RESTORE_DIR/$BACKUP_CONTENTS/event_log.db"     ~/.depthfusion/event_log.db
cp "$RESTORE_DIR/$BACKUP_CONTENTS/audit.db"         ~/.depthfusion/audit.db
cp "$RESTORE_DIR/$BACKUP_CONTENTS/projects.json"    ~/.depthfusion/projects.json

# Restore discoveries
rsync -a "$RESTORE_DIR/$BACKUP_CONTENTS/discoveries/" ~/.claude/shared/discoveries/

# Clear recall cache (derived from restored data)
rm -rf ~/.depthfusion/cache/

# Restart
systemctl --user start depthfusion-api
echo "Restore complete. Verify with: python3 -m depthfusion.admin.verify"
```

### 4.4 Verify After Restore

```bash
python3 -m depthfusion.admin.verify
```

Checks:
- Database integrity (`PRAGMA integrity_check`)
- Entry count matches pre-backup count (if `--expected-count N` provided)
- Event log is internally consistent (no gaps in sequence numbers)
- Config is valid
- API server health endpoint responds

---

## 5. Log Analysis and Audit Queries

### 5.1 Tail the Server Log

```bash
journalctl --user -u depthfusion-api -f
```

For structured JSON log output:
```bash
journalctl --user -u depthfusion-api -f -o cat | python3 -m json.tool
```

### 5.2 Audit Query Reference

```bash
# Last N audit events
python3 -m depthfusion.admin.query_audit --last 50

# All events for a specific user
python3 -m depthfusion.admin.query_audit --principal alice@example.com

# All device events in a time range
python3 -m depthfusion.admin.query_audit \
  --event-type device_enrollment,device_revocation \
  --since 2026-06-01 --until 2026-06-11

# All classification changes
python3 -m depthfusion.admin.query_audit --event-type classification_change

# All RESTRICTED exports in the last 30 days
python3 -m depthfusion.admin.query_audit \
  --event-type export \
  --classification RESTRICTED \
  --since "$(date -u -d '30 days ago' +%Y-%m-%d)"

# Failed sign-in attempts
python3 -m depthfusion.admin.query_audit \
  --event-type signin_failure \
  --since 2026-06-01
```

### 5.3 Direct SQLite Queries (Advanced)

```bash
# All audit events for a memory entry
sqlite3 ~/.depthfusion/audit.db \
  "SELECT timestamp, event_type, principal_id, details
   FROM audit_events
   WHERE json_extract(details, '$.memory_id') = 'mem-abc-123'
   ORDER BY timestamp DESC"

# Active devices by user
sqlite3 ~/.depthfusion/audit.db \
  "SELECT principal_id, device_id, device_name, platform, enrolled_at
   FROM device_audit
   WHERE current_status = 'active'
   ORDER BY principal_id, enrolled_at"

# Memory entries modified in the last 7 days
sqlite3 ~/.depthfusion/memory_store.db \
  "SELECT id, created_at, updated_at, classification, tags
   FROM memories
   WHERE updated_at > datetime('now', '-7 days')
   ORDER BY updated_at DESC
   LIMIT 100"

# Event log — sequence integrity check
sqlite3 ~/.depthfusion/event_log.db \
  "SELECT MIN(seq), MAX(seq), COUNT(*) as total,
          MAX(seq) - MIN(seq) + 1 as expected_count,
          CASE WHEN COUNT(*) = MAX(seq) - MIN(seq) + 1 THEN 'OK' ELSE 'GAPS FOUND' END as status
   FROM events"
```

### 5.4 Metrics Query

```bash
# Per-backend recall latency summary (last 7 days)
python3 -m depthfusion.metrics.aggregator \
  --mode backend_summary \
  --days 7

# Capture mechanism write rates
python3 -m depthfusion.metrics.aggregator \
  --mode capture_summary \
  --days 7

# Telemetry via MCP tool
# (from Claude Code session with DepthFusion MCP connected)
# depthfusion_query_telemetry({ "period": "week", "group_by": "backend" })
```

---

## 6. Incident Response Checklist

### 6.1 Suspected Device Compromise

1. `python3 -m depthfusion.admin.devices --revoke <device_id> --force-invalidate --reason "Suspected compromise"`
2. Query audit log for recent activity from the device: `python3 -m depthfusion.admin.query_audit --device <device_id> --last 100`
3. Review any CONFIDENTIAL or RESTRICTED exports: `python3 -m depthfusion.admin.query_audit --event-type export --device <device_id>`
4. Notify the user to re-enroll from a clean device
5. If RESTRICTED data was accessed: initiate your organization's data breach notification procedure

### 6.2 Suspected Account Compromise

1. `python3 -m depthfusion.admin.devices --revoke-all-for <email> --force-invalidate --reason "Account compromise"`
2. Revoke the user's role: `python3 -m depthfusion.admin.users --set-role <email> --role viewer`
3. Query full audit history for the account
4. Coordinate with your IdP admin to reset or suspend the OIDC account
5. After the incident is resolved, restore role and re-enroll devices

### 6.3 API Server Unavailable

```bash
# Check service status
systemctl --user status depthfusion-api

# Check for port conflict
ss -tlnp | grep 7300

# Check disk space (SQLite will fail if disk is full)
df -h ~/.depthfusion/

# Restart
systemctl --user restart depthfusion-api

# Check for database corruption
python3 -m depthfusion.admin.verify --strict
```

---

*Last updated: 2026-06-11 — V2 admin runbooks, initial release.*
