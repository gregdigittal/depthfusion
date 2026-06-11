# DepthFusion V2 Security Model

This document is the canonical security reference for DepthFusion V2. It covers identity, authorization, data classification, encryption, and network security.

---

## 1. Authentication

### 1.1 OIDC + PKCE Flow

DepthFusion V2 uses **OpenID Connect (OIDC) with Proof Key for Code Exchange (PKCE)** for all user authentication. There are no passwords stored anywhere in the system.

**Flow summary:**

```
User clicks "Sign in"
  → Desktop app generates code_verifier + code_challenge (S256)
  → Browser opens: provider_auth_url?client_id=…&code_challenge=…&scope=openid email profile
  → User authenticates at their IdP (Azure Entra ID, Okta, Google Workspace)
  → IdP redirects to loopback callback: http://127.0.0.1:<ephemeral_port>/callback?code=…
  → App exchanges: code + code_verifier → { id_token, access_token, refresh_token }
  → id_token validated (sig, iss, aud, iat, exp, nonce)
  → identity.sub + identity.email extracted → Principal created
  → refresh_token stored in OS keychain (never on disk in plaintext)
```

**Supported providers:**

| Provider | Discovery URL | Notes |
|---|---|---|
| Azure Entra ID | `https://login.microsoftonline.com/{tenant}/.well-known/openid-configuration` | Primary enterprise provider |
| Okta | `https://{domain}/.well-known/openid-configuration` | Supported |
| Google Workspace | `https://accounts.google.com/.well-known/openid-configuration` | Supported |

**Token validation rules (enforced in `depthfusion.identity.oidc`):**

- Signature verified against JWKS endpoint (keys cached 1 hour, rotated on `kid` miss)
- `iss` must match configured provider URL
- `aud` must include the registered `client_id`
- `exp` checked against system clock with 30-second skew tolerance
- `nonce` included in authorization request and validated in `id_token`
- `email_verified: true` required unless `DEPTHFUSION_ALLOW_UNVERIFIED_EMAIL=true` (dev only)

### 1.2 Device Enrollment

Each installation of the desktop app is a **device**. A device must be enrolled before it can access the memory API.

**Enrollment flow:**

```
First launch after sign-in:
  → App generates a device keypair (Ed25519, stored in OS keychain)
  → App sends enrollment request to API server:
      POST /v2/devices/enroll
      { principal_id, device_id (UUID), public_key (base64), device_name, platform }
      Bearer: id_token
  → Server records device in devices table (status=pending)
  → Admin approves (or auto-approval policy applies — see §2.2)
  → Device receives enrollment certificate signed by server's device CA
  → Subsequent API calls present: Bearer access_token + X-Device-Id header
  → Server verifies device is enrolled and not revoked before processing request
```

**Device states:** `pending` → `active` → `revoked`

A revoked device receives `403 Device revoked` on all authenticated endpoints. Token refresh is also blocked for revoked devices.

### 1.3 Token Vault

The token vault (`depthfusion.identity.token_vault`) manages credential lifecycle.

| Secret | Storage | Notes |
|---|---|---|
| `refresh_token` | OS keychain (macOS Keychain, Windows Credential Manager, Linux libsecret) | Never written to disk in plaintext |
| `access_token` | In-memory only, never persisted | 1-hour lifetime; refreshed automatically |
| Device private key | OS keychain | Non-exportable where the OS supports it |
| Enrollment certificate | `~/.depthfusion/certs/device.pem` | Public cert only; private key stays in keychain |

**Automatic refresh:** The token vault refreshes `access_token` when it has < 5 minutes remaining. If refresh fails (revoked device, expired session), the app transitions to `SIGNED_OUT` state and clears the in-memory token.

**Sign-out:** Clears the in-memory token and removes the refresh token from the OS keychain. Device record on the server is NOT automatically revoked on sign-out — explicit revocation is an admin action (see admin runbooks).

---

## 2. Authorization

### 2.1 RBAC Roles

DepthFusion V2 uses role-based access control. Roles are assigned at the user level and optionally overridden at the device level.

| Role | Description |
|---|---|
| `viewer` | Read-only access to recalled memories. Cannot publish, pin, or modify. |
| `contributor` | Can publish discoveries, use feedback tools, and modify their own entries. Cannot manage users or devices. |
| `operator` | Full contributor access plus: pin/unpin, set memory scores, run admin queries, view audit logs. |
| `admin` | Full system access: user/device management, role assignment, ACL administration, export controls. |

**Default role at enrollment:** `contributor` (configurable via `DEPTHFUSION_DEFAULT_ROLE`).

Roles are stored in the `user_roles` table and cached in the session JWT (`role` claim). Cache TTL is 15 minutes — role changes take effect within one token refresh cycle.

### 2.2 ACL Records

In addition to RBAC roles, **ACL records** control access to specific memory entries or memory namespaces.

**ACL record structure:**

```json
{
  "acl_id": "uuid",
  "subject_type": "user | device | group",
  "subject_id": "principal_id | device_id | group_name",
  "resource_type": "memory | namespace | project",
  "resource_id": "uuid | slug",
  "permission": "read | write | admin",
  "granted_by": "principal_id",
  "granted_at": "ISO-8601",
  "expires_at": "ISO-8601 | null"
}
```

ACL checks are evaluated **after** RBAC. A user needs to pass both their role check AND any applicable ACL check.

**Precedence:** Explicit `deny` ACL records take precedence over `allow` records. In the absence of an explicit ACL record, the RBAC role determines access.

### 2.3 Capability Matrix

| Action | viewer | contributor | operator | admin |
|---|---|---|---|---|
| `depthfusion_recall_relevant` | yes | yes | yes | yes |
| `depthfusion_session_seed` | yes | yes | yes | yes |
| `depthfusion_publish_context` | no | yes | yes | yes |
| `depthfusion_confirm_discovery` | no | yes | yes | yes |
| `depthfusion_recall_feedback` | no | yes | yes | yes |
| `depthfusion_pin_discovery` | no | no | yes | yes |
| `depthfusion_set_memory_score` | no | no | yes | yes |
| `depthfusion_record_decision` | no | yes | yes | yes |
| `depthfusion_record_incident` | no | yes | yes | yes |
| `depthfusion_mark_superseded` | no | no | yes | yes |
| `depthfusion_report_outcome` | no | yes | yes | yes |
| `depthfusion_graph_traverse` | yes | yes | yes | yes |
| `depthfusion_graph_status` | yes | yes | yes | yes |
| `depthfusion_set_scope` | no | yes | yes | yes |
| `depthfusion_list_projects` | yes | yes | yes | yes |
| `depthfusion_register_project` | no | no | yes | yes |
| `depthfusion_sync_project` | no | yes | yes | yes |
| `depthfusion_ingest_project` | no | no | yes | yes |
| `depthfusion_research_topic` | no | yes | yes | yes |
| `depthfusion_bridge` | no | yes | yes | yes |
| `depthfusion_ingest_conversation` | no | yes | yes | yes |
| `depthfusion_query_telemetry` | no | no | yes | yes |
| Device enrollment (auto-approve) | n/a | n/a | n/a | yes |
| User role assignment | no | no | no | yes |
| ACL management | no | no | no | yes |
| Audit log access | no | no | yes | yes |
| Export (classification ≤ INTERNAL) | no | yes | yes | yes |
| Export (classification = CONFIDENTIAL) | no | no | yes | yes |
| Export (classification = RESTRICTED) | no | no | no | yes |

---

## 3. Data Classification

All memory entries carry a `classification` field. The system enforces handling rules based on this field.

### 3.1 Classification Levels

| Level | Value | Description |
|---|---|---|
| Public | `PUBLIC` | Shareable outside the organization. No handling restrictions. |
| Internal | `INTERNAL` | Default for all captures. Internal use only. |
| Confidential | `CONFIDENTIAL` | Contains business-sensitive or customer data. Access restricted to `operator` and `admin`. |
| Restricted | `RESTRICTED` | Highest sensitivity. PII, credentials, regulated data. Access restricted to `admin` only. |

**Default classification at capture:** `INTERNAL`.

Classification is set by the capturing agent. Operators and admins can promote (increase) classification. Only admins can demote classification.

### 3.2 Handling Rules

| Rule | Description |
|---|---|
| **Access gating** | Recall and export endpoints filter results to entries the requesting principal can access (role + ACL + classification). |
| **No downgrade without audit** | Classification can only be lowered by admins; every downgrade is written to the audit log with the principal ID and reason. |
| **Export policy** | Entries with `CONFIDENTIAL` or `RESTRICTED` classification cannot be exported via `depthfusion_bridge` unless the bridge target is an approved provider in `~/.depthfusion/approved_bridge_providers.json`. |
| **Logging scrubbing** | Entries with `RESTRICTED` classification are never written to the JSONL metrics streams. Their `content` field is replaced with `[REDACTED]` in any log output. |
| **Bridge isolation** | `depthfusion_bridge` injects only entries the requesting user can access. If the user is `viewer`, no `CONFIDENTIAL` or `RESTRICTED` entries are injected as bridge context. |

---

## 4. Data at Rest Encryption

### 4.1 OS Keychain

All long-lived credentials (refresh tokens, device private keys) are stored in the OS keychain:

- **macOS:** Keychain Services (`SecItemAdd`, `SecItemCopyMatching`). Items are stored in the default keychain with `kSecAttrAccessible = kSecAttrAccessibleWhenUnlocked`.
- **Windows:** Windows Credential Manager (`CredWrite`, `CredRead`). Target name format: `DepthFusion/{principal_id}/{credential_type}`.
- **Linux:** libsecret (via the `keyring` Python package). Falls back to an encrypted file store (`~/.depthfusion/.keystore`) if libsecret is unavailable.

No plaintext credentials are ever written to the filesystem or to the SQLite databases.

### 4.2 Fernet Cache Encryption

The recall cache (`~/.depthfusion/cache/`) is encrypted at rest using **Fernet symmetric encryption** (`cryptography.fernet`).

- Cache encryption key is derived from the device's private key using HKDF-SHA256.
- Each cache file is an independent Fernet token — compromise of one file does not expose others.
- The cache is cleared on sign-out (`depthfusion.identity.token_vault.clear_session()`).
- Cache invalidation: any change to the user's role or ACLs triggers a full cache flush.

**Why Fernet for the cache?** The recall cache contains recalled memory fragments that may include `CONFIDENTIAL` content. Encrypting the cache means a stolen device's filesystem does not expose memory content even before the admin has had time to revoke the device.

### 4.3 SQLite Databases

DepthFusion V2 uses SQLite (WAL mode) for `memory_store.db`, `event_log.db`, and `audit.db`.

- SQLite files are not encrypted at the file level by default. Encryption is provided by the OS disk encryption (FileVault, BitLocker, LUKS).
- **Recommended:** Enable full-disk encryption on any host running DepthFusion. The admin runbook includes this as a prerequisite check.
- `RESTRICTED`-classified entries are additionally encrypted at the row level: the `content` column is stored as an encrypted blob (`content_enc`) and decrypted in-memory only when accessed by an `admin` principal.

---

## 5. Network Security

### 5.1 TLS Required

All API communication between the desktop app and the server must use TLS 1.2 or higher.

- The API server refuses plaintext HTTP connections on the public bind address.
- Loopback (`127.0.0.1`) connections are exempt from the TLS requirement for local-mode installs.
- The server's TLS certificate must be valid (not self-signed in production). The admin runbook covers certificate provisioning with Let's Encrypt.
- The desktop app validates the server's TLS certificate against the system trust store. Certificate pinning is available via `~/.depthfusion/pinned_cert.pem` (optional; see admin runbook).

### 5.2 Content Security Policy in Tauri

The desktop app's webview runs under a strict **Content Security Policy (CSP)**:

```
default-src 'none';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data: https:;
connect-src 'self' http://127.0.0.1:7300 https://<configured_api_host>;
frame-src 'none';
object-src 'none';
```

This prevents:
- Remote script injection (no `https:` in `script-src`)
- Iframe embedding
- Unexpected network calls (only `self` and the configured API host are in `connect-src`)

The `connect-src` value is generated at build time from the `DEPTHFUSION_API_HOST` environment variable and hardcoded into the app bundle. It cannot be changed at runtime.

### 5.3 API Server Binding

The API server follows the loopback-first binding rule from the DepthFusion security policy:

| Configuration | Bind address | Requirements |
|---|---|---|
| Default (local mode) | `127.0.0.1:7300` | No additional requirements |
| Public bind | `0.0.0.0:7300` | Requires: `DEPTHFUSION_API_TOKEN`, TLS certificate, firewall rule or Tailscale |

Public binding without `DEPTHFUSION_API_TOKEN` causes the server to refuse to start with:
```
ERROR: Public bind requested but DEPTHFUSION_API_TOKEN is not set.
       Set DEPTHFUSION_API_TOKEN or restrict to loopback with DEPTHFUSION_API_HOST=127.0.0.1
```

### 5.4 Audit Logging

All authentication events, authorization decisions, and admin actions are written to `audit.db`:

| Event | Fields logged |
|---|---|
| Sign-in success | `principal_id`, `device_id`, `ip`, `timestamp`, `provider` |
| Sign-in failure | `attempted_principal`, `ip`, `timestamp`, `reason` |
| Device enrollment | `principal_id`, `device_id`, `device_name`, `platform`, `approved_by` |
| Device revocation | `device_id`, `revoked_by`, `reason`, `timestamp` |
| Role change | `principal_id`, `old_role`, `new_role`, `changed_by`, `timestamp` |
| ACL grant/revoke | `subject`, `resource`, `permission`, `action`, `by`, `timestamp` |
| Classification change | `memory_id`, `old_level`, `new_level`, `changed_by`, `reason` |
| Export (CONFIDENTIAL/RESTRICTED) | `memory_id`, `exported_by`, `destination`, `timestamp` |
| Admin query | `query_type`, `by`, `timestamp` |

The audit log is append-only and cannot be modified through any API endpoint. Direct database writes require OS-level filesystem access — any such writes are a security incident.

---

## 6. Threat Model Summary

| Threat | Mitigated by |
|---|---|
| Stolen device (physical) | OS keychain for secrets + Fernet cache encryption + device revocation |
| Token theft | Short-lived access tokens (1 hr) + refresh token in keychain (not on disk) |
| Man-in-the-middle | TLS required + optional certificate pinning |
| Privilege escalation via role | Role claims in JWT + server-side re-validation on every request |
| Cross-user data leakage | ACL enforcement at API layer + classification-gated recall |
| Exfiltration via bridge | Bridge export policy + CONFIDENTIAL/RESTRICTED gating |
| Injection via memory content | Memory content treated as data, never eval'd; CSP blocks script injection |
| Insider threat (rogue operator) | Audit log + classification level requiring admin for RESTRICTED |

---

*Last updated: 2026-06-11 — V2 security model, initial release.*
