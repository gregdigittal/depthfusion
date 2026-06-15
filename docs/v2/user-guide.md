# DepthFusion V2 — User Guide

This guide covers installing the DepthFusion V2 desktop app, signing in for the first time, and using its core features: search, offline mode, settings, and sign-out.

---

## 1. Installing the Desktop App

### 1.1 macOS (Apple Silicon and Intel)

**System requirements:** macOS 13 (Ventura) or later · 4 GB RAM minimum · 500 MB disk space

1. Download `DepthFusion-<version>-macos.dmg` from the releases page or from your team's shared distribution link.
2. Open the `.dmg` file.
3. Drag **DepthFusion** into your Applications folder.
4. On first launch, macOS may show a security prompt: "DepthFusion cannot be opened because it is from an unidentified developer."
   - Go to **System Settings → Privacy & Security** and click **Open Anyway**.
   - Alternatively, right-click the app in Finder and choose **Open**.
5. The app will appear in your Dock and menu bar.

**First launch:** The app will ask for the server address. Enter the address your admin gave you (e.g., `https://depthfusion.yourcompany.com`). If your team uses Tailscale, enter the Tailscale hostname instead.

### 1.2 Windows

**System requirements:** Windows 10 or 11 (64-bit) · 4 GB RAM minimum · 500 MB disk space · WebView2 runtime (ships with Windows 11; auto-installed on Windows 10 if missing)

1. Download `DepthFusion-<version>-windows-setup.exe` from the releases page.
2. Run the installer. Windows Defender SmartScreen may show a prompt — click **More info** → **Run anyway**.
3. The installer places DepthFusion in `%LOCALAPPDATA%\DepthFusion\` and creates a Start menu shortcut.
4. Launch DepthFusion from the Start menu or the desktop shortcut.
5. On first launch, enter the server address your admin provided.

**Silent install (IT deployment):**
```powershell
DepthFusion-<version>-windows-setup.exe /S /ServerUrl=https://depthfusion.yourcompany.com
```

---

## 2. Signing In

### 2.1 The OIDC Sign-In Flow

DepthFusion uses your organization's identity provider (Azure Entra ID, Okta, or Google Workspace). There is no separate DepthFusion password.

1. Launch DepthFusion.
2. Click **Sign in with [Your Company]** on the welcome screen.
3. Your default browser opens to your organization's login page.
4. Sign in with your usual work credentials (username + password + MFA if required).
5. Your browser shows: **"DepthFusion sign-in complete. You can close this tab."**
6. Switch back to the DepthFusion app — it should now show your name and the home screen.

**What to expect:**
- The first time you sign in on a new device, your device is submitted for **enrollment approval**. You will see a "Waiting for device approval" screen.
- Your admin will approve the device (usually within a few minutes to a few hours, depending on your team's policy).
- Once approved, the app automatically proceeds — you do not need to sign in again.

### 2.2 Troubleshooting Sign-In

| Problem | Solution |
|---|---|
| Browser doesn't open | Click **Open browser manually** and paste the URL shown in the app |
| "Sign-in complete" but app doesn't proceed | Click **I've signed in** in the app |
| "Device pending approval" for more than a day | Contact your DepthFusion admin |
| "Access denied" after approval | Sign out and sign back in to refresh your token |
| Can't reach the server | Verify you're on the correct network (VPN or Tailscale if required) |

---

## 3. Search

### 3.1 Basic Search

The main search bar is the primary interface. Type any query and press Enter (or click the search icon).

DepthFusion returns results ranked by relevance using hybrid retrieval (BM25 + semantic reranking). Results show:
- **Content preview** — the relevant text from the memory entry
- **Source** — which session or agent captured this memory
- **Date** — when it was captured
- **Type** — the memory type: `decision`, `semantic`, `operational`, `procedural`, `episodic`, `social`, or `temporal`

### 3.2 Search Filters

Click the **Filters** button next to the search bar to narrow results:

| Filter | Options |
|---|---|
| **Project** | Select from registered projects, or "All projects" |
| **Date range** | Last 7 days / 30 days / 90 days / Custom |
| **Memory type** | decision · semantic · operational · procedural · episodic · social · temporal |
| **Classification** | PUBLIC · INTERNAL · CONFIDENTIAL (if you have operator+ access) |
| **Source** | Agent name or session ID |

### 3.3 Result Actions

Right-click any result (or hover to show action buttons) for:

- **Copy** — copy the memory content to clipboard
- **Pin** — mark as high-priority so it appears in session seeds (requires operator role)
- **Rate** — thumbs up / thumbs down; used to improve recall quality over time
- **View full** — open the full memory entry with all metadata
- **Open source** — open the original session file (if available locally)

### 3.4 Session Seed

At the top of the home screen, **Session Seed** shows the most relevant memories for your current project context. This is the same content DepthFusion automatically surfaces when you start a new Claude Code session.

Click **Refresh seed** to regenerate based on your current project, or click **Choose project** to get a seed for a different project.

---

## 4. Offline Mode

DepthFusion V2 supports fully offline search when the server is unreachable.

### 4.1 How Offline Mode Works

When the app cannot reach the server (network unavailable, VPN disconnected, etc.), it automatically switches to **offline mode**:

- A small "Offline" badge appears in the header
- Search runs against the **local cache** — the most recent results synced from the server
- Write operations (publishing new discoveries, feedback) are **queued** and replayed when the server is reachable again

The local cache is encrypted at rest (see `docs/v2/security-model.md` §4.2) and is cleared when you sign out.

### 4.2 Cache Sync

The app syncs its local cache in the background whenever:
- You sign in
- The app starts and the server is reachable
- You press **Sync now** in Settings → Offline

You can configure the cache size limit in **Settings → Offline → Cache size**. The default is 500 MB. When the cache is full, older entries are evicted first.

### 4.3 Offline Limitations

| Feature | Online | Offline |
|---|---|---|
| Search (cached entries) | Full | Yes — cached entries only |
| Session seed | Full | Yes — from cache |
| Publish new discovery | Full | Queued for replay |
| Graph traverse | Full | Limited (cached graph only) |
| Real-time sync from other agents | Full | No |
| Admin operations | Full | No |

---

## 5. Settings

Access settings via the gear icon in the top-right corner or **File → Settings** (Windows) / **DepthFusion → Settings** (macOS).

### 5.1 Account

| Setting | Description |
|---|---|
| **Display name** | Your name as shown in the app. Synced from your IdP profile. |
| **Email** | Read-only. From your IdP identity. |
| **Role** | Your current role (e.g., contributor). Contact your admin to change. |
| **Active devices** | List of devices enrolled to your account. |

### 5.2 Server

| Setting | Description |
|---|---|
| **Server URL** | The DepthFusion API server address. Set by your admin. |
| **Certificate pinning** | Optional. Upload a PEM certificate to pin the server's TLS cert. |
| **Connection timeout** | Default 30 seconds. Increase if on a slow network. |

### 5.3 Offline

| Setting | Description |
|---|---|
| **Cache size limit** | Maximum disk space for the offline cache. Default 500 MB. |
| **Auto-sync** | Sync cache on app start and periodically in background. Default on. |
| **Sync interval** | How often to background-sync. Default 30 minutes. |
| **Sync now** | Force an immediate sync. |
| **Clear cache** | Deletes all locally cached data. Requires re-sync on next use. |

### 5.4 Privacy

| Setting | Description |
|---|---|
| **Telemetry** | Send anonymous usage data (search latency, error rates) to help improve DepthFusion. Default off. |
| **Feedback signal** | Allow thumbs-up/down ratings to be used to improve recall ranking. Default on. |

### 5.5 Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Cmd/Ctrl + K` | Open search |
| `Cmd/Ctrl + Shift + S` | Open session seed |
| `Cmd/Ctrl + ,` | Open settings |
| `Cmd/Ctrl + R` | Refresh / sync |
| `Esc` | Close modal / clear search |

---

## 6. Sign Out and Data Wipe

### 6.1 Sign Out

**Sign out** clears your in-memory session token and removes your refresh token from the OS keychain. Your data on the server is not affected — your memories remain and your device enrollment remains active.

To sign out:
- **macOS:** DepthFusion → Sign out (menu bar) or Settings → Account → Sign out
- **Windows:** File → Sign out or Settings → Account → Sign out

After signing out, DepthFusion returns to the sign-in screen. Your device is still enrolled — signing back in is immediate (no re-approval needed unless your admin has revoked the device).

### 6.2 Data Wipe (Local Cache Only)

To remove all locally cached data from this device:

1. Go to **Settings → Offline → Clear cache**
2. Confirm the prompt

This removes the encrypted cache files from `~/.depthfusion/cache/`. Your data on the server is not affected.

### 6.3 Full Removal (Uninstall)

To completely remove DepthFusion from a device:

**macOS:**
```bash
# Move app to Trash
sudo rm -rf /Applications/DepthFusion.app

# Remove all local data
rm -rf ~/.depthfusion
rm -rf ~/Library/Application\ Support/DepthFusion
rm -rf ~/Library/Caches/DepthFusion
```

**Windows:**
1. **Settings → Apps → Installed apps → DepthFusion → Uninstall**
2. To remove remaining data:
```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\DepthFusion"
Remove-Item -Recurse -Force "$env:APPDATA\DepthFusion"
```

After uninstalling, contact your DepthFusion admin to revoke your device enrollment. This prevents a stale device record from occupying a license seat and ensures the audit log reflects the device as decommissioned.

---

## 7. Getting Help

| Resource | Location |
|---|---|
| Admin runbooks | `docs/v2/admin-runbooks.md` |
| Security model | `docs/v2/security-model.md` |
| GitHub issues | `https://github.com/gregdigittal/depthfusion/issues` |
| Your admin | Contact the person who sent you the server address |

---

*Last updated: 2026-06-11 — V2 user guide, initial release.*
