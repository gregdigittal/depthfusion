//! System-browser OIDC flow with PKCE and deep-link callback handling.
//!
//! Flow:
//!   1. `build_pkce_url()`   — generate verifier + challenge + state, return auth URL
//!   2. Open URL in system browser via tauri-plugin-shell
//!   3. Browser redirects to depthfusion://callback?code=...&state=...
//!   4. `handle_callback()` — validate state, call `exchange_code()`
//!   5. `exchange_code()`   — POST to token endpoint, return TokenSet

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use once_cell::sync::Lazy;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use tokio::sync::Mutex;
use url::Url;

// ---------------------------------------------------------------------------
// Pending-session store (in-memory, single-session app)
// ---------------------------------------------------------------------------

struct PkceSession {
    code_verifier: String,
    state: String,
}

static PENDING: Lazy<Mutex<Option<PkceSession>>> = Lazy::new(|| Mutex::new(None));

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// OIDC provider configuration sourced from environment or settings.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OidcConfig {
    pub issuer: String,
    pub client_id: String,
    /// Typically `depthfusion://callback`
    pub redirect_uri: String,
    /// Space-separated scopes, e.g. `"openid profile email offline_access"`
    pub scopes: String,
}

/// Returned to the frontend after a successful token exchange.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenSet {
    pub access_token: String,
    pub id_token: Option<String>,
    pub refresh_token: Option<String>,
    pub expires_in: Option<u64>,
    pub token_type: String,
}

/// Minimal error type for IPC serialisation.
#[derive(Debug, Serialize, Deserialize)]
pub struct OidcError {
    pub code: String,
    pub message: String,
}

impl std::fmt::Display for OidcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.code, self.message)
    }
}

impl OidcError {
    fn new(code: &str, msg: impl Into<String>) -> Self {
        Self { code: code.to_string(), message: msg.into() }
    }
}

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------

fn random_bytes(n: usize) -> Vec<u8> {
    let mut buf = vec![0u8; n];
    rand::thread_rng().fill_bytes(&mut buf);
    buf
}

fn base64url(bytes: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(bytes)
}

fn s256_challenge(verifier: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    base64url(&hasher.finalize())
}

// ---------------------------------------------------------------------------
// Core functions
// ---------------------------------------------------------------------------

/// Build a PKCE authorisation URL.
///
/// Stores the `code_verifier` and `state` in the in-process session store so
/// that `handle_callback` can validate the deep-link response.
///
/// Returns the full URL string the caller should open in the system browser.
pub async fn build_pkce_url(config: &OidcConfig) -> Result<String, OidcError> {
    // Generate PKCE material
    let verifier_bytes = random_bytes(32);
    let code_verifier = base64url(&verifier_bytes);
    let code_challenge = s256_challenge(&code_verifier);

    // Generate opaque state token (CSRF protection)
    let state_bytes = random_bytes(16);
    let state = base64url(&state_bytes);

    // Persist session
    {
        let mut guard = PENDING.lock().await;
        *guard = Some(PkceSession { code_verifier: code_verifier.clone(), state: state.clone() });
    }

    // Build the authorisation endpoint URL
    // Convention: issuer + "/authorize" (works for Auth0, Keycloak, etc.)
    let auth_endpoint = format!("{}/authorize", config.issuer.trim_end_matches('/'));

    let mut url = Url::parse(&auth_endpoint).map_err(|e| {
        OidcError::new("INVALID_ISSUER", format!("Cannot parse issuer URL: {e}"))
    })?;

    url.query_pairs_mut()
        .append_pair("response_type", "code")
        .append_pair("client_id", &config.client_id)
        .append_pair("redirect_uri", &config.redirect_uri)
        .append_pair("scope", &config.scopes)
        .append_pair("state", &state)
        .append_pair("code_challenge", &code_challenge)
        .append_pair("code_challenge_method", "S256");

    Ok(url.to_string())
}

/// Validate the deep-link callback parameters and exchange the code.
///
/// `params` is a map parsed from the query string of the deep-link URI.
///
/// Returns a `TokenSet` on success.
pub async fn handle_callback(
    config: &OidcConfig,
    params: HashMap<String, String>,
) -> Result<TokenSet, OidcError> {
    // RFC 6749 §4.1.2.1 — check for IdP error BEFORE extracting `code` or
    // `state`. A genuine OAuth error redirect carries `error=` with NO `code`
    // param, so this check must precede any `?`-guarded code/state extraction.
    if let Some(err) = params.get("error") {
        let desc = params.get("error_description").map(|s| s.as_str()).unwrap_or("no description");
        return Err(OidcError::new("IDP_ERROR", format!("{err}: {desc}")));
    }

    // Extract and validate required fields
    let code = params
        .get("code")
        .ok_or_else(|| OidcError::new("MISSING_CODE", "No 'code' in callback params"))?
        .clone();

    let returned_state = params
        .get("state")
        .ok_or_else(|| OidcError::new("MISSING_STATE", "No 'state' in callback params"))?
        .clone();

    // Retrieve and clear the pending session
    let session = {
        let mut guard = PENDING.lock().await;
        guard.take().ok_or_else(|| {
            OidcError::new("NO_SESSION", "No pending OIDC session found — login may have timed out")
        })?
    };

    // CSRF: validate state
    if session.state != returned_state {
        return Err(OidcError::new(
            "STATE_MISMATCH",
            "Callback state does not match the expected value",
        ));
    }

    exchange_code(config, &code, &session.code_verifier).await
}

/// POST to the token endpoint and return a `TokenSet`.
///
/// Uses `code_verifier` for PKCE proof.
///
/// The token endpoint is derived as `<config.issuer>/token`. In tests, set
/// `config.issuer` to a mockito server's base URL so that the POST is directed
/// at the mock without any signature change to this function.
pub async fn exchange_code(
    config: &OidcConfig,
    code: &str,
    code_verifier: &str,
) -> Result<TokenSet, OidcError> {
    let token_endpoint = format!("{}/token", config.issuer.trim_end_matches('/'));

    let client = reqwest::Client::new();

    let mut form = HashMap::new();
    form.insert("grant_type", "authorization_code");
    form.insert("client_id", &config.client_id);
    form.insert("code", code);
    form.insert("redirect_uri", &config.redirect_uri);
    form.insert("code_verifier", code_verifier);

    let resp = client
        .post(&token_endpoint)
        .form(&form)
        .send()
        .await
        .map_err(|e| OidcError::new("HTTP_ERROR", format!("Token request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        return Err(OidcError::new(
            "TOKEN_ERROR",
            format!("Token endpoint returned {status}: {body}"),
        ));
    }

    let raw: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| OidcError::new("PARSE_ERROR", format!("Failed to parse token response: {e}")))?;

    let access_token = raw
        .get("access_token")
        .and_then(|v| v.as_str())
        .ok_or_else(|| OidcError::new("MISSING_ACCESS_TOKEN", "Token response missing access_token"))?
        .to_string();

    Ok(TokenSet {
        access_token,
        id_token: raw.get("id_token").and_then(|v| v.as_str()).map(|s| s.to_string()),
        refresh_token: raw.get("refresh_token").and_then(|v| v.as_str()).map(|s| s.to_string()),
        expires_in: raw.get("expires_in").and_then(|v| v.as_u64()),
        token_type: raw
            .get("token_type")
            .and_then(|v| v.as_str())
            .unwrap_or("Bearer")
            .to_string(),
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex as StdMutex;

    /// PENDING is a process-wide static `Mutex<Option<PkceSession>>` shared across
    /// every test in this module. `cargo test` runs tests in parallel by default,
    /// so any two state-dependent tests (those calling `build_pkce_url` /
    /// `handle_callback`) could clobber each other's pending session.
    ///
    /// This guard serialises those tests: each acquires `TEST_LOCK` at the top and
    /// holds it for the duration, so only one state-dependent test touches PENDING
    /// at a time. It is a zero-dependency, in-file alternative to `serial_test`.
    ///
    /// We use `std::sync::Mutex` (not tokio's) so the guard is held across `.await`
    /// points without needing an async lock — the critical section is short and the
    /// guard is the test body itself. Poisoning (from a panicking failed test) is
    /// recovered via `unwrap_or_else(|e| e.into_inner())` so one failing test does
    /// not cascade-poison every subsequent test.
    static TEST_LOCK: StdMutex<()> = StdMutex::new(());

    /// Acquire the shared serialisation lock, recovering from poisoning.
    fn lock_tests() -> std::sync::MutexGuard<'static, ()> {
        TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Drain any pending session so a test starts from a known-empty PENDING.
    async fn drain_pending() {
        let mut guard = PENDING.lock().await;
        *guard = None;
    }

    /// Read the current `state` value from the URL returned by build_pkce_url.
    fn state_from_url(url_str: &str) -> String {
        let url = Url::parse(url_str).expect("build_pkce_url returned an unparseable URL");
        url.query_pairs()
            .find(|(k, _)| k == "state")
            .map(|(_, v)| v.into_owned())
            .expect("URL is missing the 'state' query param")
    }

    /// Build a minimal OidcConfig pointing at the given base URL.
    fn make_config(base_url: &str) -> OidcConfig {
        OidcConfig {
            issuer: base_url.to_string(),
            client_id: "test-client".to_string(),
            redirect_uri: "depthfusion://callback".to_string(),
            scopes: "openid profile email".to_string(),
        }
    }

    // -----------------------------------------------------------------------
    // exchange_code tests — hermetic HTTP mocking via mockito
    // -----------------------------------------------------------------------

    /// (a) Happy path: 200 with a full token JSON body.
    ///     Assert that every field in the returned TokenSet maps correctly.
    #[tokio::test]
    async fn exchange_code_happy_path() {
        let mut server = mockito::Server::new_async().await;
        let _mock = server
            .mock("POST", "/token")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(
                r#"{
                    "access_token": "at-abc123",
                    "id_token": "it-xyz",
                    "refresh_token": "rt-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer"
                }"#,
            )
            .create_async()
            .await;

        let config = make_config(&server.url());
        let result = exchange_code(&config, "auth-code", "verifier").await;

        let ts = result.expect("expected Ok(TokenSet)");
        assert_eq!(ts.access_token, "at-abc123");
        assert_eq!(ts.id_token.as_deref(), Some("it-xyz"));
        assert_eq!(ts.refresh_token.as_deref(), Some("rt-refresh"));
        assert_eq!(ts.expires_in, Some(3600));
        assert_eq!(ts.token_type, "Bearer");
    }

    /// (b) Non-2xx path: 400 error response.
    ///     Assert Err with code TOKEN_ERROR and that the status and body are
    ///     surfaced in the error message.
    #[tokio::test]
    async fn exchange_code_non_2xx_returns_token_error() {
        let error_body = r#"{"error":"invalid_grant","error_description":"Code expired"}"#;
        let mut server = mockito::Server::new_async().await;
        let _mock = server
            .mock("POST", "/token")
            .with_status(400)
            .with_header("content-type", "application/json")
            .with_body(error_body)
            .create_async()
            .await;

        let config = make_config(&server.url());
        let result = exchange_code(&config, "bad-code", "verifier").await;

        let err = result.expect_err("expected Err for 400 response");
        assert_eq!(err.code, "TOKEN_ERROR");
        // The error message must include the HTTP status code.
        assert!(
            err.message.contains("400"),
            "expected '400' in error message, got: {}",
            err.message
        );
        // The error message must include the response body.
        assert!(
            err.message.contains("invalid_grant"),
            "expected body fragment in error message, got: {}",
            err.message
        );
    }

    /// (c) Missing access_token path: 200 but JSON omits access_token.
    ///     Assert Err with code MISSING_ACCESS_TOKEN.
    #[tokio::test]
    async fn exchange_code_missing_access_token() {
        let mut server = mockito::Server::new_async().await;
        let _mock = server
            .mock("POST", "/token")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"token_type":"Bearer","expires_in":3600}"#)
            .create_async()
            .await;

        let config = make_config(&server.url());
        let result = exchange_code(&config, "auth-code", "verifier").await;

        let err = result.expect_err("expected Err for missing access_token");
        assert_eq!(err.code, "MISSING_ACCESS_TOKEN");
    }

    /// (d) Malformed JSON path: 200 but body is not valid JSON.
    ///     Assert Err with code PARSE_ERROR.
    #[tokio::test]
    async fn exchange_code_malformed_json() {
        let mut server = mockito::Server::new_async().await;
        let _mock = server
            .mock("POST", "/token")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body("this is not json {{{{")
            .create_async()
            .await;

        let config = make_config(&server.url());
        let result = exchange_code(&config, "auth-code", "verifier").await;

        let err = result.expect_err("expected Err for malformed JSON");
        assert_eq!(err.code, "PARSE_ERROR");
    }

    // -----------------------------------------------------------------------
    // build_pkce_url tests — URL structure + S256 challenge shape
    // -----------------------------------------------------------------------

    /// (1) build_pkce_url structure: parse the returned URL and assert every
    ///     query param matches the config / PKCE conventions.
    ///
    ///     The verifier is module-private, so we cannot recompute the expected
    ///     challenge. Instead we assert the challenge is the correct shape for a
    ///     base64url-encoded SHA-256 digest: 43 chars (32 bytes → ceil(32/3)*4 = 44
    ///     with padding, minus 1 padding char under URL_SAFE_NO_PAD = 43) and valid
    ///     base64url.
    #[tokio::test]
    async fn build_pkce_url_structure() {
        let _guard = lock_tests();
        drain_pending().await;

        let config = make_config("https://idp.example.com");
        let url_str = build_pkce_url(&config).await.expect("expected Ok(url)");
        let url = Url::parse(&url_str).expect("returned URL must parse");

        // Authorisation endpoint is issuer + /authorize.
        assert_eq!(url.scheme(), "https");
        assert_eq!(url.host_str(), Some("idp.example.com"));
        assert_eq!(url.path(), "/authorize");

        let params: HashMap<String, String> =
            url.query_pairs().map(|(k, v)| (k.into_owned(), v.into_owned())).collect();

        assert_eq!(params.get("response_type").map(String::as_str), Some("code"));
        assert_eq!(params.get("client_id").map(String::as_str), Some(config.client_id.as_str()));
        assert_eq!(
            params.get("redirect_uri").map(String::as_str),
            Some(config.redirect_uri.as_str())
        );
        assert_eq!(params.get("scope").map(String::as_str), Some(config.scopes.as_str()));
        assert_eq!(params.get("code_challenge_method").map(String::as_str), Some("S256"));

        let state = params.get("state").expect("state must be present");
        assert!(!state.is_empty(), "state must be non-empty");

        let challenge = params.get("code_challenge").expect("code_challenge must be present");
        assert!(!challenge.is_empty(), "code_challenge must be non-empty");

        // SHA-256 → 32 bytes → 43 base64url chars (no padding).
        assert_eq!(
            challenge.len(),
            43,
            "expected a 43-char S256 challenge, got {} chars: {challenge}",
            challenge.len()
        );
        // Must be valid base64url (no-pad). Decoding back must give exactly 32 bytes.
        let decoded = URL_SAFE_NO_PAD
            .decode(challenge.as_bytes())
            .expect("code_challenge must be valid base64url");
        assert_eq!(decoded.len(), 32, "decoded S256 challenge must be 32 bytes");

        drain_pending().await;
    }

    // -----------------------------------------------------------------------
    // handle_callback tests — state validation + error paths
    // -----------------------------------------------------------------------

    /// (2) State round-trip + CSRF happy path: build_pkce_url, then call
    ///     handle_callback with the matching state + a dummy code, pointing the
    ///     config at a mockito mock that returns a valid token body — assert Ok.
    #[tokio::test]
    async fn handle_callback_state_roundtrip_ok() {
        let _guard = lock_tests();
        drain_pending().await;

        // The token endpoint must live under the same issuer the session was built
        // with, because build_pkce_url stores nothing about the issuer — only the
        // verifier + state. So build the URL with the mock server's base url too.
        let mut server = mockito::Server::new_async().await;
        let _mock = server
            .mock("POST", "/token")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"access_token":"at-ok","token_type":"Bearer","expires_in":3600}"#)
            .create_async()
            .await;

        let config = make_config(&server.url());
        let url_str = build_pkce_url(&config).await.expect("build_pkce_url Ok");
        let state = state_from_url(&url_str);

        let mut params = HashMap::new();
        params.insert("code".to_string(), "dummy-code".to_string());
        params.insert("state".to_string(), state);

        let result = handle_callback(&config, params).await;
        let ts = result.expect("expected Ok(TokenSet) for matching state + valid token body");
        assert_eq!(ts.access_token, "at-ok");

        drain_pending().await;
    }

    /// (3) STATE_MISMATCH: build_pkce_url then handle_callback with a wrong state.
    ///     Assert Err code STATE_MISMATCH and that the pending session was consumed
    ///     (a follow-up handle_callback yields NO_SESSION).
    #[tokio::test]
    async fn handle_callback_state_mismatch_consumes_session() {
        let _guard = lock_tests();
        drain_pending().await;

        let config = make_config("https://idp.example.com");
        let _url_str = build_pkce_url(&config).await.expect("build_pkce_url Ok");

        let mut params = HashMap::new();
        params.insert("code".to_string(), "dummy-code".to_string());
        params.insert("state".to_string(), "definitely-not-the-real-state".to_string());

        let err = handle_callback(&config, params).await.expect_err("expected STATE_MISMATCH");
        assert_eq!(err.code, "STATE_MISMATCH");

        // The pending session must have been taken even on mismatch — a second
        // callback (with any state) now finds no session.
        let mut params2 = HashMap::new();
        params2.insert("code".to_string(), "dummy-code".to_string());
        params2.insert("state".to_string(), "whatever".to_string());
        let err2 = handle_callback(&config, params2)
            .await
            .expect_err("session should already be consumed");
        assert_eq!(err2.code, "NO_SESSION");

        drain_pending().await;
    }

    /// (4a) MISSING_CODE: handle_callback with params lacking `code`.
    #[tokio::test]
    async fn handle_callback_missing_code() {
        let _guard = lock_tests();
        drain_pending().await;

        let config = make_config("https://idp.example.com");
        let mut params = HashMap::new();
        params.insert("state".to_string(), "some-state".to_string());

        let err = handle_callback(&config, params).await.expect_err("expected MISSING_CODE");
        assert_eq!(err.code, "MISSING_CODE");

        drain_pending().await;
    }

    /// (4b) MISSING_STATE: handle_callback with params lacking `state`.
    #[tokio::test]
    async fn handle_callback_missing_state() {
        let _guard = lock_tests();
        drain_pending().await;

        let config = make_config("https://idp.example.com");
        let mut params = HashMap::new();
        params.insert("code".to_string(), "some-code".to_string());

        let err = handle_callback(&config, params).await.expect_err("expected MISSING_STATE");
        assert_eq!(err.code, "MISSING_STATE");

        drain_pending().await;
    }

    /// (5) IDP_ERROR: real OAuth error redirect (no `code`) — assert Err code
    ///     IDP_ERROR with the description surfaced in the message.
    ///
    ///     A genuine IdP error redirect carries `?error=...&error_description=...`
    ///     but NO `code` param. The error check is now first in handle_callback
    ///     (RFC 6749 §4.1.2.1) so this reaches IDP_ERROR even without `code`.
    #[tokio::test]
    async fn handle_callback_idp_error_surfaces_description() {
        let _guard = lock_tests();
        drain_pending().await;

        let config = make_config("https://idp.example.com");
        // Real OAuth error redirect: no `code`, just `error` + `error_description`.
        let mut params = HashMap::new();
        params.insert("error".to_string(), "access_denied".to_string());
        params.insert(
            "error_description".to_string(),
            "User declined consent".to_string(),
        );

        let err = handle_callback(&config, params).await.expect_err("expected IDP_ERROR");
        assert_eq!(err.code, "IDP_ERROR");
        assert!(
            err.message.contains("access_denied"),
            "expected error name in message, got: {}",
            err.message
        );
        assert!(
            err.message.contains("User declined consent"),
            "expected error_description surfaced in message, got: {}",
            err.message
        );

        drain_pending().await;
    }

    /// (5b) IDP_ERROR regression: error redirect with no `code` AND no
    ///      `error_description` — must return IDP_ERROR (not MISSING_CODE)
    ///      and surface the "no description" fallback.
    #[tokio::test]
    async fn handle_callback_idp_error_no_code() {
        let _guard = lock_tests();
        drain_pending().await;

        let config = make_config("https://idp.example.com");
        let mut params = HashMap::new();
        params.insert("error".to_string(), "server_error".to_string());
        // No `code`, no `error_description` — tests both the ordering fix and
        // the fallback description path.

        let err = handle_callback(&config, params).await.expect_err("expected IDP_ERROR");
        assert_eq!(err.code, "IDP_ERROR");
        assert!(
            err.message.contains("server_error"),
            "expected error name in message, got: {}",
            err.message
        );
        assert!(
            err.message.contains("no description"),
            "expected fallback description in message, got: {}",
            err.message
        );

        drain_pending().await;
    }

    /// (6) NO_SESSION: handle_callback when no build_pkce_url preceded it (PENDING
    ///     drained first) — assert Err code NO_SESSION.
    #[tokio::test]
    async fn handle_callback_no_session() {
        let _guard = lock_tests();
        drain_pending().await;

        let config = make_config("https://idp.example.com");
        let mut params = HashMap::new();
        params.insert("code".to_string(), "some-code".to_string());
        params.insert("state".to_string(), "some-state".to_string());

        let err = handle_callback(&config, params).await.expect_err("expected NO_SESSION");
        assert_eq!(err.code, "NO_SESSION");

        drain_pending().await;
    }
}
