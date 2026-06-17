//! Rust-core export enforcement (E-59 S-192, T-663 / T-664).
//!
//! Export limits — copy, save, print, download-original — are **policy
//! decisions, not UI conveniences**. The webview must never be the place those
//! decisions are made, because anything the webview can do, a tampered webview
//! (devtools, injected script) can also do. So the enforcement lives here, in
//! the Rust core, behind Tauri IPC:
//!
//! * [`policy`] — the role × classification → action matrix (T-661's server
//!   policy mirrored on the device) plus a verified **signed policy snapshot**
//!   (T-662) so offline evaluation cannot be widened by editing on-disk state.
//! * [`commands`] — the policy-gated clipboard / file-save / print IPC commands
//!   (T-663). Each consults the policy and returns a typed [`ExportDenial`] on
//!   deny; the webview gets a structured reason it can explain to the user, not
//!   a silent failure.
//! * [`stream`] — the original-file streaming gate (T-664). An original byte
//!   stream reaches disk **only** when policy allows the `download-original`
//!   action; on deny nothing is written and no blob URL of the original is ever
//!   handed back to the webview.
//!
//! Fail-closed is the rule throughout: an unverifiable snapshot, an unknown
//! role, an unknown classification, or any internal error denies — it never
//! widens access.
//!
//! Several items here are the stable surface the provenance-footer / red-team
//! tasks (T-665 / T-666) and the audit tasks (T-667 / T-668) will consume; not
//! every variant is wired to a command yet, so `dead_code` is allowed at the
//! module root.
#![allow(dead_code)]

pub mod commands;
pub mod policy;
pub mod stream;

use serde::{Deserialize, Serialize};

/// Typed denial returned to the webview when an export-class action is refused.
///
/// The webview cannot bypass enforcement, but it *should* be able to explain
/// *why* an action was refused — that is what each variant carries. The variant
/// set is the public contract the TS frontend matches on.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ExportDenial {
    /// The principal's role is not permitted to perform this action at this
    /// classification level (the policy matrix said no).
    PolicyDenied {
        action: String,
        role: String,
        classification: String,
    },
    /// The role string did not resolve to a known role — fail closed.
    UnknownRole { role: String },
    /// The classification string did not resolve to a known level — fail
    /// closed.
    UnknownClassification { classification: String },
    /// The offline policy snapshot could not be trusted (unsigned, tampered, or
    /// expired). Offline evaluation must deny rather than fall back to a
    /// forgeable on-disk policy.
    SnapshotRefused { reason: String },
    /// An internal error prevented a decision (I/O, serialisation). Fail closed.
    Internal { message: String },
}

impl std::fmt::Display for ExportDenial {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ExportDenial::PolicyDenied {
                action,
                role,
                classification,
            } => write!(
                f,
                "policy denied: role '{role}' may not '{action}' at '{classification}'"
            ),
            ExportDenial::UnknownRole { role } => write!(f, "unknown role: '{role}'"),
            ExportDenial::UnknownClassification { classification } => {
                write!(f, "unknown classification: '{classification}'")
            }
            ExportDenial::SnapshotRefused { reason } => {
                write!(f, "policy snapshot refused: {reason}")
            }
            ExportDenial::Internal { message } => write!(f, "internal error: {message}"),
        }
    }
}

impl std::error::Error for ExportDenial {}

/// The outcome of an export-class IPC command: either the action proceeds (with
/// any side-effect result) or it is refused with a typed denial. This is the
/// shape every T-663 command returns to the webview.
///
/// `Allowed` carries a generic payload `T` so the same envelope serves
/// clipboard (the copied text, possibly with a provenance footer), file-save
/// (the on-disk path), and print (a print-job descriptor).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum ExportOutcome<T> {
    /// The action was permitted; `value` is the action-specific result.
    Allowed { value: T },
    /// The action was refused; `denial` explains why.
    Denied { denial: ExportDenial },
}

impl<T> ExportOutcome<T> {
    /// Build an `Allowed` outcome.
    pub fn allowed(value: T) -> Self {
        ExportOutcome::Allowed { value }
    }

    /// Build a `Denied` outcome.
    pub fn denied(denial: ExportDenial) -> Self {
        ExportOutcome::Denied { denial }
    }

    /// `true` when the action was permitted.
    pub fn is_allowed(&self) -> bool {
        matches!(self, ExportOutcome::Allowed { .. })
    }

    /// `true` when the action was refused.
    pub fn is_denied(&self) -> bool {
        matches!(self, ExportOutcome::Denied { .. })
    }
}
