//! Original-file streaming gate (E-59 S-192 AC-2, T-664).
//!
//! The core invariant: **an original file reaches the user's disk only when
//! policy allows the `download-original` action, and the bytes flow through the
//! Rust core — never through the webview as a blob URL.**
//!
//! Why this matters: if the webview ever held the original bytes (e.g. as a
//! `blob:` URL or a base64 data URI), a tampered webview could re-save, re-post,
//! or screenshot them outside any policy. By keeping originals in the Rust core
//! and streaming straight to a destination path, the webview only ever learns
//! the *result* (a path, or a typed denial) — it never gets the exportable
//! bytes.
//!
//! [`stream_original_to_disk`] is the gate. It:
//!   1. evaluates the export policy for `download-original`;
//!   2. on **deny**, writes nothing and returns the typed denial;
//!   3. on **allow**, streams the source reader to the destination writer in
//!      bounded chunks (constant memory, no full-file buffering) and returns the
//!      byte count.
//!
//! The reader/writer abstraction (rather than hard-coding `File`) lets the unit
//! tests drive the gate with in-memory buffers — proving the deny path writes
//! zero bytes and the allow path streams correctly — without a real filesystem
//! or a running Tauri app.

use std::io::{self, Read, Write};
use std::path::Path;

use super::policy::{decide_export, ExportAction, SignedPolicySnapshot};
use super::{ExportDenial, ExportOutcome};

/// Chunk size for streaming. 64 KiB balances syscall overhead against memory —
/// the whole point is that an arbitrarily large original never sits in memory
/// (or in the webview) all at once.
const STREAM_CHUNK_BYTES: usize = 64 * 1024;

/// Result of an allowed stream: how many bytes were written to the destination.
/// The webview receives this (a count + the path it requested) — never the
/// bytes themselves.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct StreamResult {
    pub bytes_written: u64,
    pub destination: String,
}

/// Policy-gate then stream `source` to `dest`, returning a typed outcome.
///
/// On allow: streams all of `source` into `dest` in [`STREAM_CHUNK_BYTES`]
/// chunks and returns `ExportOutcome::Allowed { value: StreamResult }`.
///
/// On deny: returns `ExportOutcome::Denied { denial }` **without touching
/// `dest`** — not a single byte of the original is written.
///
/// `destination_label` is the path string echoed back to the webview in the
/// allowed result (the webview supplied where it wanted the file; we confirm it
/// only after the policy says yes).
pub fn stream_original_to_disk<R: Read, W: Write>(
    role: &str,
    classification: &str,
    offline_snapshot: Option<&SignedPolicySnapshot>,
    snapshot_key: Option<&[u8]>,
    now: f64,
    source: &mut R,
    dest: &mut W,
    destination_label: &str,
) -> ExportOutcome<StreamResult> {
    // 1. Gate FIRST. Nothing is read from the source or written to the dest
    //    until the policy decision is known — a denied original is never even
    //    opened for streaming.
    if let Err(denial) = decide_export(
        role,
        ExportAction::DownloadOriginal,
        classification,
        offline_snapshot,
        snapshot_key,
        now,
    ) {
        return ExportOutcome::denied(denial);
    }

    // 2. Allowed → stream in bounded chunks (constant memory).
    match copy_streamed(source, dest) {
        Ok(bytes_written) => ExportOutcome::allowed(StreamResult {
            bytes_written,
            destination: destination_label.to_string(),
        }),
        Err(e) => ExportOutcome::denied(ExportDenial::Internal {
            message: format!("stream failed: {e}"),
        }),
    }
}

/// Copy all of `source` into `dest` in [`STREAM_CHUNK_BYTES`] chunks, returning
/// the total bytes written. No allocation grows with the file size.
fn copy_streamed<R: Read, W: Write>(source: &mut R, dest: &mut W) -> io::Result<u64> {
    let mut buf = vec![0u8; STREAM_CHUNK_BYTES];
    let mut total: u64 = 0;
    loop {
        let n = source.read(&mut buf)?;
        if n == 0 {
            break;
        }
        dest.write_all(&buf[..n])?;
        total += n as u64;
    }
    dest.flush()?;
    Ok(total)
}

/// Convenience wrapper that opens a real file at `dest_path` and streams a real
/// file at `source_path` through the gate. Used by the IPC command; the
/// reader/writer-generic [`stream_original_to_disk`] is what the unit tests
/// drive with in-memory buffers.
///
/// On deny, the destination file is **not created** — the policy gate runs
/// before the destination is opened.
pub fn stream_original_file(
    role: &str,
    classification: &str,
    offline_snapshot: Option<&SignedPolicySnapshot>,
    snapshot_key: Option<&[u8]>,
    now: f64,
    source_path: &Path,
    dest_path: &Path,
) -> ExportOutcome<StreamResult> {
    // Gate first so a denied request never creates the destination file.
    if let Err(denial) = decide_export(
        role,
        ExportAction::DownloadOriginal,
        classification,
        offline_snapshot,
        snapshot_key,
        now,
    ) {
        return ExportOutcome::denied(denial);
    }

    let mut source = match std::fs::File::open(source_path) {
        Ok(f) => f,
        Err(e) => {
            return ExportOutcome::denied(ExportDenial::Internal {
                message: format!("cannot open source: {e}"),
            })
        }
    };
    let mut dest = match std::fs::File::create(dest_path) {
        Ok(f) => f,
        Err(e) => {
            return ExportOutcome::denied(ExportDenial::Internal {
                message: format!("cannot create destination: {e}"),
            })
        }
    };

    match copy_streamed(&mut source, &mut dest) {
        Ok(bytes_written) => ExportOutcome::allowed(StreamResult {
            bytes_written,
            destination: dest_path.to_string_lossy().to_string(),
        }),
        Err(e) => ExportOutcome::denied(ExportDenial::Internal {
            message: format!("stream failed: {e}"),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    use hmac::{Hmac, Mac};
    use sha2::Sha256;
    type HmacSha256 = Hmac<Sha256>;

    fn hex(bytes: &[u8]) -> String {
        let mut s = String::new();
        for b in bytes {
            s.push_str(&format!("{:02x}", b));
        }
        s
    }

    fn signed_snapshot_granting(level: &str, roles: &[&str], key: &[u8]) -> SignedPolicySnapshot {
        let mut policy = BTreeMap::new();
        policy.insert(level.to_string(), roles.iter().map(|r| r.to_string()).collect());
        let mut snap = SignedPolicySnapshot {
            version: 1,
            issued_at: 1000.0,
            expires_at: 5000.0,
            policy,
            signature: String::new(),
        };
        // sign using the same canonical body the verifier uses
        let mut mac = HmacSha256::new_from_slice(key).unwrap();
        // re-derive canonical body via a verify round-trip is private; instead
        // we rely on the policy module's own sign path by signing here with a
        // matching canonical body. We reconstruct it through a dummy verify:
        // simplest is to compute over the documented format.
        // Use the public verify to confirm our signature instead of duplicating
        // the canonical body: sign by brute trying — not possible. So compute
        // canonical the same way policy.rs does by serialising deterministically.
        let body = canonical_for_test(&snap);
        mac.update(body.as_bytes());
        snap.signature = hex(&mac.finalize().into_bytes());
        snap
    }

    /// Mirror of policy::SignedPolicySnapshot::canonical_bytes for test signing.
    fn canonical_for_test(snap: &SignedPolicySnapshot) -> String {
        let mut levels: Vec<(&String, Vec<String>)> = snap
            .policy
            .iter()
            .map(|(level, roles)| {
                let mut r = roles.clone();
                r.sort();
                (level, r)
            })
            .collect();
        levels.sort_by(|a, b| a.0.cmp(b.0));
        let policy_json = {
            let mut parts = Vec::new();
            for (level, roles) in &levels {
                let roles_json = roles
                    .iter()
                    .map(|r| format!("\"{}\"", r))
                    .collect::<Vec<_>>()
                    .join(",");
                parts.push(format!("\"{}\":[{}]", level, roles_json));
            }
            format!("{{{}}}", parts.join(","))
        };
        let ts = |v: f64| {
            if v == v.trunc() {
                format!("{:.1}", v)
            } else {
                let mut s = format!("{:.3}", v);
                while s.ends_with('0') {
                    s.pop();
                }
                s
            }
        };
        format!(
            "{{\"expires_at\":{},\"issued_at\":{},\"policy\":{},\"version\":{}}}",
            ts(snap.expires_at),
            ts(snap.issued_at),
            policy_json,
            snap.version,
        )
    }

    #[test]
    fn allowed_stream_writes_all_bytes() {
        // contributor may download-original at confidential (default matrix).
        let data = b"the original file contents, possibly large".to_vec();
        let mut source = io::Cursor::new(data.clone());
        let mut dest: Vec<u8> = Vec::new();

        let outcome = stream_original_to_disk(
            "contributor",
            "confidential",
            None,
            None,
            0.0,
            &mut source,
            &mut dest,
            "/tmp/original.bin",
        );

        match outcome {
            ExportOutcome::Allowed { value } => {
                assert_eq!(value.bytes_written, data.len() as u64);
                assert_eq!(value.destination, "/tmp/original.bin");
                assert_eq!(dest, data, "streamed bytes must match the source exactly");
            }
            ExportOutcome::Denied { denial } => panic!("expected allow, got {denial:?}"),
        }
    }

    #[test]
    fn denied_stream_writes_zero_bytes() {
        // viewer may NOT download-original anywhere → deny, dest untouched.
        let data = b"secret original".to_vec();
        let mut source = io::Cursor::new(data);
        let mut dest: Vec<u8> = Vec::new();

        let outcome = stream_original_to_disk(
            "viewer",
            "public",
            None,
            None,
            0.0,
            &mut source,
            &mut dest,
            "/tmp/should-not-exist.bin",
        );

        assert!(outcome.is_denied());
        assert!(dest.is_empty(), "denied original must NOT be written to disk");
    }

    #[test]
    fn analyst_download_denied_no_bytes() {
        // analyst never downloads originals (default matrix).
        let mut source = io::Cursor::new(b"x".to_vec());
        let mut dest: Vec<u8> = Vec::new();
        let outcome = stream_original_to_disk(
            "analyst",
            "internal",
            None,
            None,
            0.0,
            &mut source,
            &mut dest,
            "/tmp/x",
        );
        assert!(outcome.is_denied());
        assert!(dest.is_empty());
    }

    #[test]
    fn large_input_streams_across_multiple_chunks() {
        // 200 KiB > 3 chunks of 64 KiB → exercises the loop.
        let data = vec![0xABu8; 200 * 1024];
        let mut source = io::Cursor::new(data.clone());
        let mut dest: Vec<u8> = Vec::new();
        let outcome = stream_original_to_disk(
            "admin",
            "restricted",
            None,
            None,
            0.0,
            &mut source,
            &mut dest,
            "/tmp/big.bin",
        );
        match outcome {
            ExportOutcome::Allowed { value } => {
                assert_eq!(value.bytes_written, data.len() as u64);
                assert_eq!(dest.len(), data.len());
                assert_eq!(dest, data);
            }
            ExportOutcome::Denied { denial } => panic!("admin should be allowed: {denial:?}"),
        }
    }

    #[test]
    fn offline_snapshot_gate_allows_streaming_when_role_granted() {
        let key = b"stream-key";
        // grant contributor at confidential in the signed snapshot
        let snap = signed_snapshot_granting("confidential", &["contributor", "admin"], key);
        let data = b"streamed-under-snapshot".to_vec();
        let mut source = io::Cursor::new(data.clone());
        let mut dest: Vec<u8> = Vec::new();

        let outcome = stream_original_to_disk(
            "contributor",
            "confidential",
            Some(&snap),
            Some(key),
            2000.0,
            &mut source,
            &mut dest,
            "/tmp/ok.bin",
        );
        assert!(outcome.is_allowed());
        assert_eq!(dest, data);
    }

    #[test]
    fn offline_tampered_snapshot_denies_streaming() {
        let key = b"stream-key";
        let mut snap = signed_snapshot_granting("confidential", &["contributor"], key);
        // tamper after signing
        snap.policy.get_mut("confidential").unwrap().push("viewer".to_string());
        let mut source = io::Cursor::new(b"data".to_vec());
        let mut dest: Vec<u8> = Vec::new();

        let outcome = stream_original_to_disk(
            "contributor",
            "confidential",
            Some(&snap),
            Some(key),
            2000.0,
            &mut source,
            &mut dest,
            "/tmp/no.bin",
        );
        match outcome {
            ExportOutcome::Denied { denial } => {
                assert!(matches!(denial, ExportDenial::SnapshotRefused { .. }));
            }
            ExportOutcome::Allowed { .. } => panic!("tampered snapshot must deny"),
        }
        assert!(dest.is_empty());
    }

    #[test]
    fn real_file_deny_does_not_create_destination() {
        use std::time::{SystemTime, UNIX_EPOCH};
        let tag = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir();
        let src = dir.join(format!("df-stream-src-{tag}.bin"));
        let dst = dir.join(format!("df-stream-dst-{tag}.bin"));
        std::fs::write(&src, b"original bytes").unwrap();

        // viewer denied → destination must not be created.
        let outcome = stream_original_file(
            "viewer",
            "public",
            None,
            None,
            0.0,
            &src,
            &dst,
        );
        assert!(outcome.is_denied());
        assert!(!dst.exists(), "denied request must not create the destination file");

        // cleanup
        let _ = std::fs::remove_file(&src);
        let _ = std::fs::remove_file(&dst);
    }

    #[test]
    fn real_file_allow_streams_to_destination() {
        use std::time::{SystemTime, UNIX_EPOCH};
        let tag = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir();
        let src = dir.join(format!("df-stream-src2-{tag}.bin"));
        let dst = dir.join(format!("df-stream-dst2-{tag}.bin"));
        let payload = b"original file streamed through the rust core";
        std::fs::write(&src, payload).unwrap();

        let outcome = stream_original_file(
            "contributor",
            "confidential",
            None,
            None,
            0.0,
            &src,
            &dst,
        );
        assert!(outcome.is_allowed());
        let written = std::fs::read(&dst).unwrap();
        assert_eq!(written, payload);

        let _ = std::fs::remove_file(&src);
        let _ = std::fs::remove_file(&dst);
    }
}
