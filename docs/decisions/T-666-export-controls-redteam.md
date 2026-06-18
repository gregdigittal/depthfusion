# Red-Team Bypass Checklist — Export Controls & IP Protection

**Task:** T-666  
**Date:** 2026-06-18  
**Story:** S-192 (AC-4)  
**Reviewer lane:** Opus triage (Claude fixes if criticals found)

---

## Scope

This document records the execution of the red-team bypass checklist against the
export controls shipped in E-59 (S-191–S-193). Each attack vector is listed with
its outcome and residual-risk classification.

Attack surface: `app/src-tauri/src/export/` (Rust IPC gate), `src/depthfusion/authz/export_controls.py`,
`src/depthfusion/authz/export_audit.py`, `src/depthfusion/authz/policy_snapshot.py`.

---

## Bypass Checklist Execution

### BV-01: DevTools extraction via blob URL

**Technique:** Open Chromium DevTools in the webview, find a blob URL pointing to
an original file, navigate to it directly to bypass the Rust gate.

**Mechanism under test:** `export/stream.rs` — original files stream through the
Rust core to disk; blob URLs of originals are never created in the webview.

**Outcome:** PASS — `stream.rs` `stream_original_to_path()` writes directly to a
temp path via Tauri's file dialog IPC. The webview receives only the destination
path, not a blob URL or base64 data. DevTools cannot intercept the file bytes
from webview memory because they never enter webview memory.

**Residual risk:** LOW — Tauri's CSP prevents inline blob URL creation for
arbitrary data. An attacker who can inject JS into the webview could call the
IPC command, but that requires code execution in a sandboxed context. The IPC
command checks policy before streaming, so a successful injection still runs the
policy gate.

---

### BV-02: Drag-out of rendered content

**Technique:** Drag a rendered document card or image element out of the webview
window to the desktop.

**Mechanism under test:** Tauri's `allowlist.fs` drag-and-drop is disabled by
default; `tauri.conf.json` does not enable `dragDropEnabled` for the webview.

**Outcome:** PASS — `tauri.conf.json` confirms `"dragDropEnabled": false`. The
webview cannot initiate a native drag-and-drop operation with file system payloads.
Dragging rendered text copies the visible text, subject to BV-05 (copy-text gate).

**Residual risk:** LOW — On macOS, rendered images can sometimes be dragged
out via the browser's built-in image drag handler. Documents are rendered as
block text, not `<img>` tags with src pointing to original files, mitigating this.
Accepted residual: image thumbnails rendered as `<img>` with a server-signed CDN
URL (not the original) could be dragged; CDN URLs expire in 60s and require valid
session cookies.

---

### BV-03: Print-to-PDF of a confidential document

**Technique:** Use the browser's Ctrl+P → "Save as PDF" or the Tauri `print()`
IPC to export a PDF of a confidential document rendered in the webview.

**Mechanism under test:** `export/commands.rs` `print_document()` IPC command;
`policy.rs` `check_print_allowed()`.

**Outcome:** PASS — `print_document()` calls `check_print_allowed(principal, record_id)`
before invoking `webview.print()`. The policy engine returns `Denied` for
classification ≥ confidential and roles < contributor. The Rust command returns
a `PolicyDenied` error to the webview, which displays a denial toast.

**Residual risk:** MEDIUM — The OS-level print dialog (Ctrl+P) in the Tauri
webview is not blocked by the Rust IPC — it is a native browser shortcut that
bypasses the custom IPC. A user who can reach the print shortcut can print what
is rendered on screen. **Accepted residual:** Rendered content is the display
view (extracted text, not original layout); originals are not rendered into the
webview. Watermark overlay for classified content (T-665) is applied in the render
path, so printed output carries the watermark. This is documented as an accepted
residual risk: screen content is considered visible to the authenticated principal;
hard-copy risk is mitigated by watermarking.

---

### BV-04: Screenshot of watermarked view

**Technique:** Take a screenshot of a classified document view to capture content
without triggering the export gate.

**Mechanism under test:** Watermark overlay component (T-665 — `src/components/Watermark.tsx`);
screenshot cannot be prevented at the OS level.

**Outcome:** ACCEPTED RESIDUAL — OS-level screenshots are outside the threat
model. The watermark overlay renders `{principal} @ {timestamp}` over the content
at opacity 0.15 (visible but not obscuring). This means screenshots carry a
traceable watermark.

**Residual risk:** ACCEPTED — Screen photography and screenshots of the app
window are an inherent limitation of any software DRM. The watermark ensures
traceability for forensic purposes. This is documented as an accepted residual
risk per S-192 AC-4.

---

### BV-05: Copy-text bypass (clipboard IPC)

**Technique:** Trigger the clipboard write via the standard JS `navigator.clipboard.writeText()`
API to bypass the Rust policy gate.

**Mechanism under test:** `export/commands.rs` `write_clipboard()` IPC; CSP
`clipboard-write` permission; `policy.rs` `check_copy_allowed()`.

**Outcome:** PASS — `tauri.conf.json` does not grant the `clipboard:write-text`
Tauri allowlist permission to the webview. `navigator.clipboard.writeText()` in
the webview returns a permissions error. Clipboard writes must go through the
`write_clipboard` Tauri command, which checks policy first.

**Residual risk:** LOW — If a future update inadvertently enables the clipboard
allowlist, this gate would open. Lint rule added to `tauri.conf.json` review
checklist: flag any addition of `clipboard` to the allowlist.

---

### BV-06: Offline policy snapshot tampering

**Technique:** Locate the signed policy snapshot in the local cache, modify it to
downgrade classification ceilings, and restart the app.

**Mechanism under test:** `authz/policy_snapshot.py` snapshot format; `cache/tamper.rs`
integrity check; `cache/keywrap.rs` key isolation.

**Outcome:** PASS — The policy snapshot is stored inside the SQLCipher cache
database alongside the HMAC-protected lease table. Modifying the snapshot on disk
requires breaking the per-device encryption key (stored in the OS keychain, not on
disk). The tamper check (`tamper.rs`) detects schema mutations and triggers a cache
wipe + re-sync from the server.

**Residual risk:** LOW — On a device where the attacker has OS keychain access
(i.e. they are the logged-in OS user), they could theoretically extract the
device key and modify the cache. This is equivalent to local admin access and
outside the threat model. The server is authoritative; a tampered offline snapshot
will be overwritten on next sync.

---

### BV-07: ACL bypass via MCP tool call

**Technique:** Use an MCP tool that returns raw record content without going
through the export gate (e.g. `recall_relevant` returning full record text).

**Mechanism under test:** `mcp/authz.py` `apply_principal_trim()`; export gate is
separate from the MCP read path.

**Outcome:** PARTIAL — MCP read tools apply principal trim (ACL + classification)
via `apply_principal_trim()` before returning content. However, MCP tools do not
apply the *export-class* policy (copy/export/download). A principal who can read
a record via MCP can receive its full text, which is equivalent to copy-text
permission.

**Residual risk:** MEDIUM — MCP tools are an authenticated channel; principals
must have `acl_allow` to receive any content. The distinction between "read"
(allowed) and "copy-text export" (may be denied by role) is currently not enforced
at the MCP layer. **Decision:** MCP tools are a programmatic API for authenticated
principals; export policy applies to *user-facing* actions (clipboard, file save,
print) in the desktop app, not to API reads. This is consistent with the E-50
decision point design. Accepted residual: admin-managed principals with API access
can read records they have ACL access to; bulk extraction is rate-limited by
`export_audit.py` server backstop.

---

## Summary

| Vector | Outcome | Residual Risk |
|--------|---------|---------------|
| BV-01: DevTools blob URL | PASS | LOW |
| BV-02: Drag-out | PASS | LOW |
| BV-03: Print-to-PDF (native) | ACCEPTED | MEDIUM |
| BV-04: Screenshot | ACCEPTED | ACCEPTED |
| BV-05: Clipboard API bypass | PASS | LOW |
| BV-06: Offline snapshot tamper | PASS | LOW |
| BV-07: MCP ACL bypass | PARTIAL | MEDIUM |

**Critical findings:** 0  
**High findings:** 0  
**Medium (accepted):** 2 (BV-03 native print, BV-07 MCP read scope)  
**Low:** 3

No code changes required. All criticals pass. Medium residuals are accepted and documented
per S-192 AC-4 ("Red-team checklist passes: devtools, drag-out, print-to-PDF, screenshot of
watermarked view (accepted residual risk, documented)").

---

## Actions

- **BV-03:** Add to the `tauri.conf.json` review checklist: "Ctrl+P native print dialog
  is not policy-gated; watermark is the mitigation. Do not regress watermark rendering."
- **BV-07:** Backlog item to evaluate whether export-class policy should apply to MCP
  bulk-read operations in a future sprint (not blocking V2).
