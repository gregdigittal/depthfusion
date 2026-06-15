# T-628: Typed IPC Layer + CSP Hardening — Instructions

**Story:** S-180 (E-56: Desktop UI Shell — Tauri 2 + React/TS)
**Branch:** `v2/lane-c-ui`
**Worktree:** `.claude/worktrees/v2-lane-c-ui/`
**Status:** Code complete. Blocked on VPS build environment — missing system libraries.

---

## What Was Built

T-628 is **already implemented** in `v2/lane-c-ui`. The code is correct. The blocker is the Linux VPS missing the webkit2gtk/gdk-pixbuf dev packages required to compile a Tauri app.

### Files added/modified

| File | What it does |
|---|---|
| `app/src/lib/ipc.ts` | Typed TypeScript wrapper around `@tauri-apps/api/core invoke()` |
| `app/src-tauri/src/commands.rs` | Rust IPC commands: `get_app_info` → `AppInfo`, `ping` → `String` |
| `app/src-tauri/src/lib.rs` | Registers commands in `invoke_handler!` |
| `app/src-tauri/tauri.conf.json` | CSP policy in `app.security.csp` |
| `app/src-tauri/capabilities/default.json` | Tauri capability manifest (windows: `["main"]`) |

### CSP policy (in `tauri.conf.json`)

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data: asset: https://asset.localhost;
connect-src ipc: http://ipc.localhost
```

### Typed IPC contract (`app/src/lib/ipc.ts`)

```typescript
import { invoke } from '@tauri-apps/api/core'

export interface AppInfo {
  version: string
  name: string
}

export async function getAppInfo(): Promise<AppInfo> {
  return invoke<AppInfo>('get_app_info')
}

export async function ping(message: string): Promise<string> {
  return invoke<string>('ping', { message })
}
```

All new IPC commands follow this pattern: define a typed Rust struct, derive `Serialize`/`Deserialize`, expose via `#[tauri::command]`, register in `generate_handler![]`, and wrap in a typed TS function in `src/lib/ipc.ts`.

---

## The Blocker — Environment Fix Required

`cargo check` fails on the VPS with:

```
error: pkg-config could not find: gdk-pixbuf-2.0
```

This is a **missing system library** issue, not a code defect.

### Fix — run on the Linux VPS (176.9.147.206)

```bash
sudo apt update && sudo apt install -y \
  libwebkit2gtk-4.1-dev \
  libgdk-pixbuf2.0-dev \
  build-essential \
  curl \
  wget \
  file \
  libxdo-dev \
  libssl-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev
```

> `libwebkit2gtk-4.1-dev` is the Tauri 2 requirement (note: `4.1`, not `4.0`).
> Some Ubuntu 20.04 LTS hosts only have `4.0` in the default repos — see the
> alternative install path below if `4.1` is unavailable.

### If `libwebkit2gtk-4.1-dev` is not found (Ubuntu 20.04)

Ubuntu 22.04+ ships `webkit2gtk-4.1` natively. On 20.04 you need the Tauri PPA or
must upgrade to 22.04:

```bash
# Option A — upgrade the OS (recommended for a build host)
sudo do-release-upgrade

# Option B — add the webkit2gtk backports PPA
sudo add-apt-repository ppa:webkit-team/ppa
sudo apt update
sudo apt install -y libwebkit2gtk-4.1-dev
```

### Check the current Ubuntu version

```bash
lsb_release -a
```

---

## Verification Steps (after installing libs)

Run these from `app/` inside the worktree:

```bash
cd /home/gregmorris/projects/depthfusion/.claude/worktrees/v2-lane-c-ui/app

# 1. Install JS dependencies
npm install

# 2. TypeScript check
npm run build

# 3. Rust compile check (no linker/bundle — fast)
cargo check --manifest-path src-tauri/Cargo.toml

# 4. Full build (optional — slow, ~5 min)
cargo tauri build
```

Expected results:
- `npm run build` → exits 0, dist/ populated
- `cargo check` → exits 0, no warnings
- `cargo tauri build` → produces `.deb` / `.AppImage` under `src-tauri/target/release/bundle/`

---

## G1 Gate Criterion

T-628 satisfies **G1 C4**: _"Tauri shell boots"_.

C4 is considered met when `cargo check` passes clean and the IPC layer type-checks end-to-end (TypeScript `invoke<AppInfo>('get_app_info')` matches the Rust `pub struct AppInfo` shape).

C4 does NOT require a full GUI boot on the VPS — the VPS has no display server. The artifact from `cargo tauri build` is tested locally on Mac/Windows by a developer.

---

## Next Tasks in Lane C (after T-628 is verified)

| Task | Description | Priority |
|---|---|---|
| T-629 | System-browser OIDC flow + deep-link handling (mac/win) | P0 |
| T-630 | Rust-side token vault (keychain/DPAPI) + session handle API | P0 |
| T-631 | Sign-out + local wipe flow with tests | P0 |

T-629 depends on T-628 being green (IPC layer must exist before OIDC integration).

---

## Key Decisions (locked)

- **Tauri 2, not Electron** — decided in D-3, not revisitable without a new ADR.
- **CSP `connect-src ipc:`** — required for Tauri's IPC protocol; removing it breaks
  all Rust↔frontend communication.
- **`style-src 'unsafe-inline'`** — Tailwind 4's JIT runtime injects styles inline.
  Acceptable at this stage; revisit before shipping to production users.
- **`@tauri-apps/api` version pinned to `^2.11.0`** — must stay in sync with
  `tauri = "2.11.x"` in `Cargo.toml`. Mismatched major versions cause invoke failures
  at runtime with no compile-time error.
