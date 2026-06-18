# Tauri Updater Signing Key Setup

DepthFusion uses Ed25519 signatures to verify update bundles before installation.
The public key is embedded in the shipped binary (`tauri.conf.json → plugins.updater.pubkey`).
The corresponding private key must be stored as a GitHub Actions secret.

## Current State

A bootstrap keypair was generated during initial V2 setup. **Before production release,
rotate this keypair** using the procedure below — the bootstrap private key was briefly
visible in the development environment and must not be used in production.

## Generating a New Keypair (rotate before production)

**Prerequisites:** Node.js + Tauri CLI installed.

```bash
# Install Tauri CLI if needed
npm install -g @tauri-apps/cli

# Generate a keypair (interactive — will prompt for a password)
# Leave the password empty for CI-compatible key
npx tauri signer generate -w ~/.tauri/depthfusion-updater.key
```

This produces two files:
- `~/.tauri/depthfusion-updater.key` — the private key (KEEP SECRET)
- `~/.tauri/depthfusion-updater.key.pub` — the public key

## Embedding the Public Key

The `.pub` file contains something like:

```
untrusted comment: minisign public key XXXXXXXXXXXXXXXX
XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

The second line is the public key. Replace `pubkey` in `src-tauri/tauri.conf.json`:

```json
"updater": {
  "pubkey": "<paste the second line here>"
}
```

Commit this change to the repository.

## Adding the Private Key to GitHub Secrets

1. Open GitHub → Settings → Secrets → Actions
2. Create a new secret: `TAURI_SIGNING_PRIVATE_KEY`
3. Paste the content of `~/.tauri/depthfusion-updater.key` (the full file, including the header)
4. If you set a password, also create `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` with that password

The signing workflow (`.github/workflows/sign-and-notarize.yml`) reads these secrets
during the release build and signs the update bundles automatically.

## How It Works

1. CI builds the update bundle (`.dmg`, `.msi`, `.AppImage`)
2. The private key signs each bundle, producing a `.sig` file
3. The update server hosts both the bundle and its `.sig`
4. The Tauri app downloads the bundle, verifies the `.sig` against the embedded public key
5. If verification fails, the update is **rejected** — the binary is not installed

This ensures only bundles produced by the legitimate CI pipeline can be installed,
even if the update endpoint is compromised.

## Verification

After embedding a new public key and adding the CI secret, test the signing:

```bash
# In CI or locally with the private key:
export TAURI_SIGNING_PRIVATE_KEY=$(cat ~/.tauri/depthfusion-updater.key)
cargo tauri build  # will automatically sign the bundle
```

A successful signing produces `.sig` files alongside the bundle artifacts.
