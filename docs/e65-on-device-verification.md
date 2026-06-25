# E-65 On-Device Verification — macOS

Steps to close the four ACs that require a live macOS install.

## Prerequisites

- DepthFusion DMG installed and launched at least once
- Entra ID tenant configured (see `.env.example` for `DEPTHFUSION_OIDC_*` vars)
- macOS Keychain Access app available

---

## S-215 AC-3 — API key stored in OS keychain

After completing the Solo setup wizard with a real `sk-ant-` key:

```bash
# Verify the entry exists in the keychain
security find-generic-password -s "depthfusion" -a "session_tokens" -w
```

Expected: JSON blob containing `"token_type":"ApiKey"` and your key as `access_token`.  
If the command returns `security: SecKeychainSearchCopyNext: The specified item could not be found.` — the vault write failed; check Console.app for the Tauri process error.

---

## S-214 AC-4 — Re-run setup wizard from Settings

1. Sign in to DepthFusion (OIDC flow — see below if not yet configured).
2. Open Settings (gear icon, bottom-left nav).
3. Scroll to the **Setup** card.
4. Click **Re-run setup wizard**.
5. Expected: app reloads and the mode-selection wizard appears.

---

## S-216 AC-4 — OIDC sign-in via deep-link callback

Requires `DEPTHFUSION_OIDC_*` env vars set in the app bundle or `~/.claude/depthfusion.env`.

1. Launch DepthFusion. The unauthenticated screen shows **Sign In**.
2. Click **Sign In** — your default browser opens the Microsoft/Entra login page.
3. Complete login. Browser redirects to `depthfusion://callback?code=...`.
4. Expected: DepthFusion app receives the deep-link, exchanges the code, stores tokens, and shows the main dashboard.

To check the Entra app registration has the right redirect URI:
```
depthfusion://callback
```
This must be in the app registration's **Authentication → Redirect URIs** as a **Public client / native** URI.

---

## S-217 AC-3 — OIDC sign-in completion

Continuation of S-216: after the browser redirect resolves:

```bash
# Verify OIDC tokens landed in keychain (distinct from solo ApiKey entry)
security find-generic-password -s "depthfusion" -a "session_tokens" -w | python3 -m json.tool | grep token_type
```

Expected: `"token_type": "Bearer"` (not `"ApiKey"`).

---

## Marking ACs complete

Once each step above passes, mark the AC `[x]` in `BACKLOG.md` and note the date.
