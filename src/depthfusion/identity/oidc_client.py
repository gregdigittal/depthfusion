"""OIDC client for Microsoft Entra ID (public client, PKCE + device-code).

This client targets the *public-client* profile: there is **no client secret**.
The authorization-code flow is protected with PKCE (S256) and a ``nonce``; the
device-authorization flow (RFC 8628) is supported for headless / CLI logins.

The client never holds key material — token signatures are verified by a
:class:`~depthfusion.identity.token_validator.TokenValidator` built from a
shared :class:`~depthfusion.identity.jwks_cache.JwksCache`.

Endpoints
---------
For Entra ID, given a tenant id the default endpoints are::

    authorize:    https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize
    token:        https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
    device-code:  https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode

All three may be overridden explicitly (useful for tests and sovereign clouds).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

from .errors import OidcFlowError
from .jwks_cache import JwksCache
from .models import DeviceCodeResult, Principal
from .principal_store import PrincipalStore
from .token_validator import TokenValidator

_DEFAULT_SCOPE = "openid profile offline_access"
_DEFAULT_HTTP_TIMEOUT = 30.0
_ENTRA_BASE = "https://login.microsoftonline.com"


def _generate_pkce_verifier() -> str:
    """Return a high-entropy PKCE code_verifier (RFC 7636 §4.1)."""
    # 32 random bytes -> 43-char base64url string (within the 43..128 range).
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def _pkce_challenge(verifier: str) -> str:
    """Return the S256 code_challenge for ``verifier`` (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcClient:
    """Public-client OIDC flows (PKCE auth-code and device-code) for Entra ID.

    Parameters
    ----------
    client_id:
        The application (client) id registered in Entra ID.
    tenant_id:
        The directory (tenant) id. Used to derive default endpoints.
    redirect_uri:
        The redirect URI registered for the authorization-code flow.
    scope:
        Space-delimited scope string. Defaults to
        ``"openid profile offline_access"``.
    authorize_endpoint, token_endpoint, device_code_endpoint:
        Explicit endpoint overrides. If omitted, derived from ``tenant_id``.
    timeout:
        Per-request httpx timeout (seconds) for token/device-code calls.
    """

    def __init__(
        self,
        client_id: str,
        tenant_id: str,
        redirect_uri: str = "http://localhost",
        scope: str = _DEFAULT_SCOPE,
        *,
        authorize_endpoint: str | None = None,
        token_endpoint: str | None = None,
        device_code_endpoint: str | None = None,
        timeout: float = _DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        if not client_id:
            raise OidcFlowError("client_id is required")
        if not tenant_id and not (authorize_endpoint and token_endpoint):
            raise OidcFlowError("tenant_id is required when endpoints are not given")
        self._client_id = client_id
        self._tenant_id = tenant_id
        self._redirect_uri = redirect_uri
        self._scope = scope
        self._timeout = timeout
        base = f"{_ENTRA_BASE}/{tenant_id}/oauth2/v2.0"
        self._authorize_endpoint = authorize_endpoint or f"{base}/authorize"
        self._token_endpoint = token_endpoint or f"{base}/token"
        self._device_code_endpoint = device_code_endpoint or f"{base}/devicecode"

    @classmethod
    def from_env(cls, *, timeout: float = _DEFAULT_HTTP_TIMEOUT) -> "OidcClient":
        """Build a client from ``DEPTHFUSION_*`` environment variables.

        Reads ``DEPTHFUSION_OIDC_CLIENT_ID``, ``DEPTHFUSION_OIDC_TENANT_ID``,
        optional ``DEPTHFUSION_OIDC_REDIRECT_URI`` and
        ``DEPTHFUSION_OIDC_SCOPE``.
        """
        client_id = os.environ.get("DEPTHFUSION_OIDC_CLIENT_ID", "").strip()
        tenant_id = os.environ.get("DEPTHFUSION_OIDC_TENANT_ID", "").strip()
        if not client_id or not tenant_id:
            raise OidcFlowError(
                "DEPTHFUSION_OIDC_CLIENT_ID and DEPTHFUSION_OIDC_TENANT_ID "
                "must be set"
            )
        redirect_uri = os.environ.get(
            "DEPTHFUSION_OIDC_REDIRECT_URI", "http://localhost"
        ).strip()
        scope = os.environ.get("DEPTHFUSION_OIDC_SCOPE", _DEFAULT_SCOPE).strip()
        return cls(
            client_id=client_id,
            tenant_id=tenant_id,
            redirect_uri=redirect_uri,
            scope=scope,
            timeout=timeout,
        )

    # ------------------------------------------------------------------ #
    # Authorization-code flow (PKCE)                                     #
    # ------------------------------------------------------------------ #
    def build_pkce_url(self) -> tuple[str, str, str, str]:
        """Build a PKCE authorization-request URL.

        Returns
        -------
        (url, verifier, nonce, state):
            ``url`` is the authorize endpoint the user-agent should visit.
            ``verifier`` is the PKCE code_verifier to keep and pass to
            :meth:`exchange_code`. ``nonce`` must be validated against the
            returned ID token. ``state`` must be stored and validated against
            the ``state`` parameter returned in the redirect callback to
            prevent CSRF attacks.
        """
        verifier = _generate_pkce_verifier()
        challenge = _pkce_challenge(verifier)
        nonce = secrets.token_urlsafe(24)
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "scope": self._scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
            "state": state,
            "response_mode": "query",
        }
        url = f"{self._authorize_endpoint}?{urlencode(params)}"
        return url, verifier, nonce, state

    async def exchange_code(
        self,
        code: str,
        verifier: str,
        jwks_cache: JwksCache,
        nonce: str | None = None,
        *,
        store: PrincipalStore | None = None,
    ) -> Principal:
        """Exchange an authorization ``code`` for tokens and build a Principal.

        When ``store`` is provided, the resulting principal is persisted via
        :meth:`PrincipalStore.upsert_principal` before being returned, so that
        current group membership is refreshed on every login (S-156 AC-3).
        Token fields are intentionally **not** persisted by the store.

        Raises
        ------
        OidcFlowError
            If the token endpoint returns an error response.
        TokenInvalidError / TokenExpiredError / JwksFetchError
            If the returned ID token fails validation.
        """
        data = {
            "client_id": self._client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "code_verifier": verifier,
            "scope": self._scope,
        }
        payload = await self._post_token(data)
        return await self._principal_from_token_response(
            payload, jwks_cache, nonce, store=store
        )

    # ------------------------------------------------------------------ #
    # Device-authorization flow (RFC 8628)                               #
    # ------------------------------------------------------------------ #
    async def start_device_code(self) -> DeviceCodeResult:
        """Begin a device-authorization flow.

        Returns
        -------
        DeviceCodeResult
            The provider's device-authorization response.

        Raises
        ------
        OidcFlowError
            If the device-code endpoint returns an error.
        """
        data = {"client_id": self._client_id, "scope": self._scope}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._device_code_endpoint, data=data)
        except httpx.HTTPError as exc:
            raise OidcFlowError(f"device-code request failed: {exc}") from exc

        body = self._safe_json(response)
        if response.status_code >= 400 or "error" in body:
            raise OidcFlowError(
                f"device-code request error: "
                f"{body.get('error', response.status_code)} "
                f"{body.get('error_description', '')}".strip()
            )

        try:
            return DeviceCodeResult(
                device_code=body["device_code"],
                user_code=body["user_code"],
                verification_uri=body.get("verification_uri")
                or body.get("verification_url", ""),
                expires_in=int(body.get("expires_in", 900)),
                interval=int(body.get("interval", 5)),
                verification_uri_complete=body.get("verification_uri_complete"),
                message=body.get("message"),
            )
        except KeyError as exc:
            raise OidcFlowError(
                f"device-code response missing field {exc}"
            ) from exc

    async def poll_device_code(
        self,
        device_code: str,
        jwks_cache: JwksCache,
        timeout: float = 300.0,
        interval: float = 5.0,
        *,
        store: PrincipalStore | None = None,
    ) -> Principal:
        """Poll the token endpoint until the user completes the device flow.

        Honours the ``authorization_pending`` and ``slow_down`` responses:
        on ``slow_down`` the poll interval is increased by 5 seconds per
        RFC 8628 §3.5.

        When ``store`` is provided, the resulting principal is persisted via
        :meth:`PrincipalStore.upsert_principal` before being returned, so that
        current group membership is refreshed on every login (S-156 AC-3).
        Token fields are intentionally **not** persisted by the store.

        Raises
        ------
        OidcFlowError
            On timeout, ``expired_token``, ``access_denied``,
            ``authorization_declined``, or any other terminal error.
        """
        deadline = time.monotonic() + timeout
        current_interval = max(interval, 1.0)

        while True:
            if time.monotonic() >= deadline:
                raise OidcFlowError("device-code polling timed out")

            data = {
                "client_id": self._client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            }
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._token_endpoint, data=data)
            except httpx.HTTPError as exc:
                raise OidcFlowError(f"device-code poll failed: {exc}") from exc

            body = self._safe_json(response)
            error = body.get("error")

            if not error and response.status_code < 400:
                return await self._principal_from_token_response(
                    body, jwks_cache, nonce=None, store=store
                )

            if error == "authorization_pending":
                await asyncio.sleep(current_interval)
                continue
            if error == "slow_down":
                current_interval += 5.0
                await asyncio.sleep(current_interval)
                continue

            # Terminal errors: expired_token, access_denied,
            # authorization_declined, bad_verification_code, etc.
            raise OidcFlowError(
                f"device-code poll error: {error} "
                f"{body.get('error_description', '')}".strip()
            )

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #
    async def _post_token(self, data: dict[str, str]) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._token_endpoint, data=data)
        except httpx.HTTPError as exc:
            raise OidcFlowError(f"token request failed: {exc}") from exc

        body = self._safe_json(response)
        if response.status_code >= 400 or "error" in body:
            raise OidcFlowError(
                f"token endpoint error: "
                f"{body.get('error', response.status_code)} "
                f"{body.get('error_description', '')}".strip()
            )
        return body

    async def _principal_from_token_response(
        self,
        body: dict,
        jwks_cache: JwksCache,
        nonce: str | None,
        *,
        store: PrincipalStore | None = None,
    ) -> Principal:
        id_token = body.get("id_token")
        access_token = body.get("access_token")
        if not id_token:
            raise OidcFlowError("token response did not include an id_token")

        validator = self._build_validator(jwks_cache)
        claims = await validator.validate(id_token, nonce=nonce)

        expires_at: float | None = None
        if "exp" in claims:
            try:
                expires_at = float(claims["exp"])
            except (TypeError, ValueError):
                expires_at = None
        if expires_at is None and "expires_in" in body:
            try:
                expires_at = time.time() + float(body["expires_in"])
            except (TypeError, ValueError):
                expires_at = None

        groups = claims.get("groups")
        if not isinstance(groups, list):
            groups = []

        principal = Principal(
            principal_id=str(claims.get("sub", "")),
            upn=str(claims.get("preferred_username", "")),
            display_name=str(claims.get("name", "")),
            groups=[str(g) for g in groups],
            device_id=claims.get("deviceid") or claims.get("device_id"),
            access_token=access_token,
            id_token=id_token,
            expires_at=expires_at,
        )

        # AC-3: persist current group membership on each successful login.
        # PrincipalStore.upsert_principal stores only the non-secret identity
        # fields (principal_id, upn, display_name, groups) — tokens are NOT
        # persisted (AC-4).
        if store is not None:
            store.upsert_principal(principal)

        return principal

    def _build_validator(self, jwks_cache: JwksCache) -> TokenValidator:
        issuer = os.environ.get("DEPTHFUSION_OIDC_ISSUER", "").strip() or (
            f"{_ENTRA_BASE}/{self._tenant_id}/v2.0"
        )
        audience = (
            os.environ.get("DEPTHFUSION_OIDC_AUDIENCE", "").strip()
            or self._client_id
        )
        return TokenValidator(
            jwks_cache=jwks_cache,
            expected_issuer=issuer,
            expected_audience=audience,
        )

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict:
        try:
            parsed = response.json()
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


__all__ = ["OidcClient"]
