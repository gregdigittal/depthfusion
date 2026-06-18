# DepthFusion V2 Sync Migration Runbook

> **Audience:** DepthFusion administrators and power users migrating from V1 (rsync-based sync) to V2 (delta-query connectors, transactional ingest batches, SharePoint Graph integration).
>
> **Scope:** This runbook covers the full migration path — backup, install, schema migration, SharePoint connector setup, and rollback. For ongoing admin procedures post-migration, see `docs/v2/admin-runbooks.md`.

---

## Overview

V2 replaces V1's rsync-based file sync with a purpose-built connector architecture:

| Capability | V1 | V2 |
|---|---|---|
| Sync mechanism | `rsync` over SSH (`sync.sh`) | Graph API delta-query via `SharePointConnector` |
| Incremental sync | Not supported (full copy each run) | Delta tokens — only changed items fetched |
| Ingest batches | Single-threaded, unbounded | Transactional batches with rollback on failure |
| SharePoint support | Not available | Full (docx, pdf, txt, md; sensitivity-label mapping) |
| Credential management | SSH keys / manual | `SHAREPOINT_CLIENT_ID` / `SHAREPOINT_CLIENT_SECRET` / `SHAREPOINT_TENANT_ID` env vars |
| Storage backend | File-based (`~/.depthfusion/`) | ChromaDB `PersistentClient` (same directory, updated collection metadata) |

**Key benefit of delta queries:** After the first full sync, subsequent runs only download items that changed since the last run. For large SharePoint drives this reduces sync time from minutes to seconds.

---

## Prerequisites

Before starting the migration:

- Python 3.11 or later (`python3 --version`)
- `pip` or `uv` available on the PATH
- DepthFusion V1 data directory exists (`~/.depthfusion/` or a custom path set via `DEPTHFUSION_DATA_DIR`)
- Git access to the `v2-enterprise` branch (or a V2 release tag)
- If migrating the SharePoint connector: an Azure Entra ID app registration with `Files.Read.All` and `Sites.Read.All` application permissions (see §4.1)
- Minimum 2× the current size of `~/.depthfusion/` free disk space (for the backup)

---

## Phase 1: Pre-migration backup

**Do this before any other step. A failed migration is fully recoverable only if the backup exists.**

```bash
# Default data path
cp -r ~/.depthfusion ~/.depthfusion.v1.backup

# If you use a custom data directory
export V1_DATA="${DEPTHFUSION_DATA_DIR:-$HOME/.depthfusion}"
cp -r "$V1_DATA" "${V1_DATA}.v1.backup"
echo "Backup written to ${V1_DATA}.v1.backup"
ls -lh "${V1_DATA}.v1.backup"
```

Verify the backup is readable:

```bash
# Should list chroma.sqlite3, event_log.db, and any memory files
ls -la ~/.depthfusion.v1.backup/
```

If the data directory is large (> 5 GB), use rsync for a faster copy:

```bash
rsync -a --progress ~/.depthfusion/ ~/.depthfusion.v1.backup/
```

---

## Phase 2: Install V2

```bash
# Navigate to the DepthFusion repo
cd /path/to/depthfusion

# Switch to the V2 branch (or a v2.x.x release tag)
git checkout v2-enterprise

# Install with all optional dependencies (recommended)
pip install -e ".[all]"

# Alternatively, with uv (faster)
uv pip install -e ".[all]"

# Verify installation
python -c "import depthfusion; print(depthfusion.__version__)"
# Expected: 2.x.x (or the version printed by the v2 branch)
```

If you only need SharePoint sync (no GPU/vLLM extras):

```bash
pip install -e ".[connectors]"
```

---

## Phase 3: Schema migration

V2 uses the same ChromaDB `PersistentClient` and the same `~/.depthfusion/` directory as V1. The storage format is backward-compatible at the SQLite level. However, V2 adds new fields to collection metadata (classification level, source connector, delta cursor) that V1 left absent.

The migration runs automatically on first startup via the install module. You can also trigger it explicitly:

```bash
python3 -m depthfusion.install.install --migrate-schema
```

The migrator:
1. Opens each existing ChromaDB collection.
2. Adds missing metadata fields with safe defaults (`classification: "internal"`, `connector: null`, `delta_cursor: null`).
3. Writes a `schema_version` marker to `~/.depthfusion/config.json`.
4. Does **not** delete or rewrite any existing embeddings.

Verify the migration completed:

```bash
python3 - <<'EOF'
import json, pathlib
cfg = json.loads(pathlib.Path("~/.depthfusion/config.json").expanduser().read_text())
print("schema_version:", cfg.get("schema_version", "not set"))
EOF
# Expected: schema_version: 2
```

If `schema_version` is missing, re-run the installer:

```bash
python3 -m depthfusion.install.install --migrate-schema --force
```

---

## Phase 4: SharePoint connector migration (if used)

Skip this phase if you are not using SharePoint. V2 is fully functional without it.

### 4.1 App registration prerequisites

The SharePoint connector uses Microsoft Graph API with client-credentials flow. You need an Entra ID (Azure AD) app registration:

1. Azure Portal → **Entra ID** → **App registrations** → **New registration**
2. Name: `DepthFusion SharePoint Connector` (or any name)
3. Supported account types: `Accounts in this organizational directory only`
4. No redirect URI needed (daemon/service flow)
5. After creation, go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**:
   - `Files.Read.All`
   - `Sites.Read.All`
6. Click **Grant admin consent** (requires Global Admin or Application Admin role)
7. Go to **Certificates & secrets** → **New client secret** — copy the value immediately

### 4.2 Set environment variables

Add to your shell profile (`.bashrc`, `.zshrc`) or to the systemd service environment file:

```bash
export SHAREPOINT_CLIENT_ID="your-application-client-id"
export SHAREPOINT_CLIENT_SECRET="your-client-secret-value"
export SHAREPOINT_TENANT_ID="your-directory-tenant-id"
```

Verify all three are set:

```bash
python3 - <<'EOF'
import os
for var in ("SHAREPOINT_CLIENT_ID", "SHAREPOINT_CLIENT_SECRET", "SHAREPOINT_TENANT_ID"):
    val = os.environ.get(var, "")
    print(f"{var}: {'SET' if val else 'MISSING'}")
EOF
```

### 4.3 Add site scope

Before running sync, register the SharePoint sites you want to ingest:

```bash
python3 -m depthfusion.connectors.sharepoint_scope add \
  --site-url "https://contoso.sharepoint.com/sites/Engineering" \
  --drive-id "b!abc123xyz"
```

To list registered scopes:

```bash
python3 -m depthfusion.connectors.sharepoint_scope list
```

To remove a scope:

```bash
python3 -m depthfusion.connectors.sharepoint_scope remove \
  --site-url "https://contoso.sharepoint.com/sites/Engineering"
```

The `drive-id` for a SharePoint library can be found via:

```bash
# Using the Microsoft Graph CLI (mg) or curl:
curl -H "Authorization: Bearer $TOKEN" \
  "https://graph.microsoft.com/v1.0/sites/{site-id}/drives" \
  | python3 -m json.tool | grep '"id"\|"name"'
```

### 4.4 Initial full sync

The first sync has no delta token — it ingests all items in the registered drives:

```bash
PYTHONPATH=./src python3 -m depthfusion.connectors.sharepoint sync
```

Expected output:

```
[SharePoint] Authenticating with tenant <tenant-id>...
[SharePoint] Starting full sync for site: https://contoso.sharepoint.com/sites/Engineering
[SharePoint] Drive b!abc123xyz: fetching item list...
[SharePoint] Ingested 142 items, skipped 3 (unsupported MIME), 0 errors
[SharePoint] Delta token saved. Next run will be incremental.
```

The delta token is persisted to `~/.depthfusion/sharepoint_cursors.json`. **Do not delete this file** between runs — it is the basis for incremental sync.

### 4.5 Delta sync (second run onwards)

Every subsequent invocation automatically uses the saved delta token:

```bash
PYTHONPATH=./src python3 -m depthfusion.connectors.sharepoint sync
```

Expected output:

```
[SharePoint] Authenticating with tenant <tenant-id>...
[SharePoint] Delta sync for drive b!abc123xyz: 7 changes since last run
[SharePoint] Ingested 7 items (4 new, 2 modified, 1 deleted)
[SharePoint] Delta token updated.
```

To schedule incremental sync via cron (example: every 4 hours):

```cron
0 */4 * * * cd /path/to/depthfusion && PYTHONPATH=./src \
  SHAREPOINT_CLIENT_ID=... SHAREPOINT_CLIENT_SECRET=... SHAREPOINT_TENANT_ID=... \
  python3 -m depthfusion.connectors.sharepoint sync >> ~/.depthfusion/sync.log 2>&1
```

---

## Phase 5: Verify sync

After completing the sync, verify the connector status and document count:

```bash
PYTHONPATH=./src python3 -m depthfusion.connectors.sharepoint status
```

Expected output:

```
SharePoint connector status:
  Tenant ID:        xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  Registered sites: 1
  Drive b!abc123xyz:
    Last sync:      2026-06-12T14:30:00Z
    Documents:      142
    Delta token:    present (incremental sync enabled)
    Errors since last sync: 0
```

Verify documents are retrievable via the MCP interface:

```bash
python3 - <<'EOF'
from depthfusion.mcp.server import DepthFusionServer
srv = DepthFusionServer()
results = srv.recall(query="engineering design documents", top_k=5)
for r in results:
    print(r.source_id, r.score, r.metadata.get("title", "(no title)"))
EOF
```

Verify ChromaDB collection health:

```bash
python3 - <<'EOF'
import chromadb
client = chromadb.PersistentClient(path="~/.depthfusion")
for col in client.list_collections():
    print(f"{col.name}: {col.count()} items")
EOF
```

---

## Phase 6: Rollback procedure

If the migration fails or produces unexpected behaviour, restore from the V1 backup:

```bash
# Stop any running DepthFusion service
systemctl --user stop depthfusion 2>/dev/null || true

# Restore the data directory
rm -rf ~/.depthfusion
cp -r ~/.depthfusion.v1.backup ~/.depthfusion

# Switch back to the V1 branch
cd /path/to/depthfusion
git checkout main   # or the V1 tag you were on

# Reinstall V1 dependencies
pip install -e ".[all]"

# Verify V1 is running
python -c "import depthfusion; print(depthfusion.__version__)"
```

The rsync-based `sync.sh` script is preserved on the `main` branch and will continue to work as before.

**Important:** The V2 migration does not modify the V1 backup directory. Restoring from backup is always safe.

---

## Troubleshooting

### "Delta token expired" (HTTP 410 Gone)

The Graph API invalidates delta tokens after ~30 days of inactivity or following a major drive restructure.

```bash
# Clear the stale cursor for the affected drive
python3 -m depthfusion.connectors.sharepoint clear-cursor \
  --drive-id "b!abc123xyz"

# Run a new full sync
PYTHONPATH=./src python3 -m depthfusion.connectors.sharepoint sync
```

### "Lock file exists" (stale PID)

If a previous sync was killed mid-run, it may leave a lock file:

```bash
# Check the lock file
cat ~/.depthfusion/sharepoint.lock
# Outputs: PID of the last sync process

# Verify the process is not running
ps aux | grep <PID>

# If the process is not running, remove the lock
rm ~/.depthfusion/sharepoint.lock

# Retry sync
PYTHONPATH=./src python3 -m depthfusion.connectors.sharepoint sync
```

### "Required environment variable not set" (ConfigurationError)

One or more of `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`, or `SHAREPOINT_TENANT_ID` is missing from the environment. Verify they are exported (not just set) and that you are running in the same shell or service context where they are defined.

```bash
# Debug: print current values (redact before sharing)
printenv | grep SHAREPOINT
```

### Schema migration shows "schema_version: not set"

The `config.json` was not created or is malformed. Run the installer with `--force`:

```bash
python3 -m depthfusion.install.install --migrate-schema --force
```

If the file still shows no `schema_version`, check write permissions on `~/.depthfusion/`:

```bash
ls -la ~/.depthfusion/
# config.json should be owned by your user with rw permissions
```

### ChromaDB collection count shows 0 after migration

The schema migration does not re-index existing data — it only updates metadata. If collections show 0 items, the V1 data directory was likely in a different location.

```bash
# Check where V1 data actually lives
python3 - <<'EOF'
import os, pathlib
data_dir = os.environ.get("DEPTHFUSION_DATA_DIR") or str(pathlib.Path.home() / ".depthfusion")
print("Data dir:", data_dir)
print("Exists:", pathlib.Path(data_dir).exists())
import chromadb
client = chromadb.PersistentClient(path=data_dir)
for col in client.list_collections():
    print(f"  {col.name}: {col.count()} items")
EOF
```

If the data is in a non-default location, set `DEPTHFUSION_DATA_DIR` before running the migration and the installer.

### "Files.Read.All permission denied" (403 Forbidden)

Admin consent was not granted for the app registration, or the credentials are for the wrong tenant.

1. Verify the tenant ID in `SHAREPOINT_TENANT_ID` matches the tenant where the app registration lives.
2. In Azure Portal → Entra ID → App registrations → your app → API permissions: confirm the status shows "Granted for {tenant}".
3. If not granted, ask a Global Admin to click "Grant admin consent".

### V1 sync.sh still running alongside V2

V1's cron jobs for `sync.sh` should be removed after migration to avoid conflicts:

```bash
crontab -e
# Remove or comment out lines calling sync.sh
```

V2 connector sync and V1 rsync sync both write to `~/.depthfusion/` but in different formats. Running both concurrently can cause data inconsistencies.
