"""Device enrollment via OIDC device-code flow with OS keychain storage.

This module implements the *service identity* for DepthFusion instances:

1. **Enrollment** — on first run, the device authenticates via the OIDC
   device-code flow (RFC 8628) and receives a ``device_id`` + credential
   from the server's ``POST /v2/devices/enroll`` endpoint.

2. **Keychain storage** — the credential is persisted in the OS-native
   secrets store:

   * macOS  → `Keychain Services` via the ``keyring`` library (backed by
               the system or login keychain).
   * Windows → `DPAPI` via ``win32cred`` (``keyring``'s wincred backend).
   * Linux   → `Secret Service` (GNOME Keyring / KWallet) via the
               ``secretstorage`` D-Bus interface (``keyring`` falls back
               to the plaintext file backend when no daemon is available).

3. **Retrieve / delete** — helpers to load an existing credential for
   sync authentication and to remove a credential on de-enrolment or
   factory reset.

Usage example::

    from depthfusion.identity.device_keychain import DeviceKeychain

    keychain = DeviceKeychain()
    if not keychain.load():
        # First run — obtain a token via device-code and enroll
        principal = await oidc_client.device_login(jwks_cache)
        cred = await keychain.enroll(
            principal=principal,
            enroll_url="https://my-df-server/v2/devices/enroll",
        )
        print(f"Enrolled as device {cred.device_id}")
    else:
        print("Already enrolled")

All public methods document which exceptions they raise.  Callers that
do not want to distinguish between *not enrolled* and *keychain errors*
can catch :class:`DeviceKeychainError`.
"""
from __future__ import annotations

import json
import logging
import platform
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERVICE_NAME = "depthfusion"
_ACCOUNT_NAME = "device_credential"


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class DeviceKeychainError(Exception):
    """Base class for all keychain errors in this module."""


class KeychainNotAvailableError(DeviceKeychainError):
    """The OS keychain backend is not available or usable.

    On Linux this typically means no Secret Service daemon is running.
    On Windows it means the ``win32cred`` extension is absent.
    """


class EnrollmentError(DeviceKeychainError):
    """The enrollment POST to ``/v2/devices/enroll`` failed."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class DeviceCredential:
    """A device-bound credential returned by the enrollment endpoint.

    Attributes
    ----------
    device_id:
        Opaque identifier assigned by the server.
    credential:
        The shared secret / token the device uses for subsequent
        machine-to-machine sync authentication.
    platform:
        The OS platform string (e.g. ``"darwin"``, ``"linux"``, ``"win32"``).
    extra:
        Any additional fields returned by the server (preserved verbatim).
    """

    device_id: str
    credential: str
    platform: str = field(default_factory=lambda: sys.platform)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialise to a compact JSON string suitable for keychain storage."""
        payload: dict[str, Any] = {
            "device_id": self.device_id,
            "credential": self.credential,
            "platform": self.platform,
        }
        if self.extra:
            payload["extra"] = self.extra
        return json.dumps(payload)

    @classmethod
    def from_json(cls, raw: str) -> "DeviceCredential":
        """Deserialise from a JSON string previously produced by :meth:`to_json`.

        Raises
        ------
        DeviceKeychainError
            If the JSON is malformed or missing required fields.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeviceKeychainError(f"credential JSON is malformed: {exc}") from exc
        try:
            return cls(
                device_id=data["device_id"],
                credential=data["credential"],
                platform=data.get("platform", sys.platform),
                extra=data.get("extra", {}),
            )
        except KeyError as exc:
            raise DeviceKeychainError(
                f"credential JSON missing required field {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# OS-keychain adapter
# ---------------------------------------------------------------------------


class _KeychainBackend:
    """Thin wrapper around the ``keyring`` library.

    The ``keyring`` package auto-selects the best available backend for the
    current OS:

    * macOS   → ``KeychainBackend`` (Keychain Services)
    * Windows → ``WinVaultKeyring`` (DPAPI via Windows Credential Manager)
    * Linux   → ``SecretService`` (GNOME Keyring / KWallet via D-Bus)

    When no system keychain is available (e.g. a headless Linux box with no
    Secret Service daemon and no ``keyrings.alt`` installed), ``keyring``
    falls back to the plaintext file-backed keyring — which we accept as a
    graceful degradation because the alternative is crashing on first run.
    """

    def __init__(self, service: str = _SERVICE_NAME, account: str = _ACCOUNT_NAME) -> None:
        self._service = service
        self._account = account
        self._keyring = self._import_keyring()

    @staticmethod
    def _import_keyring() -> Any:
        try:
            import keyring  # type: ignore[import-untyped]
            return keyring
        except ImportError as exc:
            raise KeychainNotAvailableError(
                "The 'keyring' package is required for device keychain storage. "
                "Install it with: pip install keyring"
            ) from exc

    def get(self) -> str | None:
        """Return the stored credential string, or ``None`` if absent."""
        try:
            return self._keyring.get_password(self._service, self._account)
        except Exception as exc:
            logger.warning("keychain_get_failed", error=str(exc))
            return None

    def set(self, secret: str) -> None:
        """Persist ``secret`` in the OS keychain.

        Raises
        ------
        KeychainNotAvailableError
            If the keychain backend raises an unrecoverable error.
        """
        try:
            self._keyring.set_password(self._service, self._account, secret)
        except Exception as exc:
            raise KeychainNotAvailableError(
                f"Could not write to OS keychain: {exc}"
            ) from exc

    def delete(self) -> None:
        """Remove the credential.  Silent if no credential was stored."""
        try:
            self._keyring.delete_password(self._service, self._account)
        except Exception:
            pass  # Already absent — treat as success.


# ---------------------------------------------------------------------------
# High-level enrollment / retrieval API
# ---------------------------------------------------------------------------


class DeviceKeychain:
    """Enrollment and keychain management for a DepthFusion device identity.

    Parameters
    ----------
    service_name:
        The keychain *service* namespace.  Defaults to ``"depthfusion"``.
    account_name:
        The keychain *account* (username) key.  Defaults to
        ``"device_credential"``.
    _backend:
        Inject a custom backend (used by tests to avoid touching the real
        OS keychain).
    """

    def __init__(
        self,
        service_name: str = _SERVICE_NAME,
        account_name: str = _ACCOUNT_NAME,
        *,
        _backend: _KeychainBackend | None = None,
    ) -> None:
        self._backend = _backend or _KeychainBackend(service_name, account_name)

    # ------------------------------------------------------------------ #
    # Read / write                                                        #
    # ------------------------------------------------------------------ #

    def load(self) -> DeviceCredential | None:
        """Return the enrolled credential, or ``None`` if not yet enrolled.

        Raises
        ------
        DeviceKeychainError
            If a credential is present but cannot be deserialised.
        """
        raw = self._backend.get()
        if raw is None:
            return None
        return DeviceCredential.from_json(raw)

    def save(self, cred: DeviceCredential) -> None:
        """Persist ``cred`` in the OS keychain.

        Raises
        ------
        KeychainNotAvailableError
            If the OS keychain is unavailable or rejects the write.
        """
        self._backend.set(cred.to_json())
        logger.info("device_credential_saved", device_id=cred.device_id)

    def delete(self) -> None:
        """Remove any stored credential (de-enrolment / factory reset)."""
        self._backend.delete()
        logger.info("device_credential_deleted")

    # ------------------------------------------------------------------ #
    # Enrollment                                                         #
    # ------------------------------------------------------------------ #

    async def enroll(
        self,
        *,
        principal: Any,
        enroll_url: str,
        http_timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
    ) -> DeviceCredential:
        """Enroll this device via ``POST /v2/devices/enroll``.

        The caller must have already authenticated via the OIDC device-code
        flow and obtained a :class:`~depthfusion.identity.models.Principal`.
        This method POSTs the principal's access token + platform info to
        ``enroll_url`` and persists the returned credential in the OS keychain.

        Parameters
        ----------
        principal:
            An authenticated :class:`~depthfusion.identity.models.Principal`
            (any object with ``access_token`` and ``principal_id`` attributes).
        enroll_url:
            Full URL of the device enrollment endpoint, e.g.
            ``"https://my-df-server/v2/devices/enroll"``.
        http_timeout:
            Per-request timeout in seconds.  Default 30.
        extra_headers:
            Additional request headers (e.g. custom ``X-`` headers).

        Returns
        -------
        DeviceCredential
            The server-assigned device identity.  Also persisted in the
            OS keychain by this method.

        Raises
        ------
        EnrollmentError
            If the server returns a non-2xx status or an unrecognised body.
        KeychainNotAvailableError
            If the OS keychain is unavailable after the server responds.
        """
        access_token = getattr(principal, "access_token", None) or ""
        principal_id = getattr(principal, "principal_id", "") or ""

        headers: dict[str, str] = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        body = {
            "principal_id": principal_id,
            "platform": sys.platform,
            "platform_detail": f"{platform.system()} {platform.release()}",
        }

        logger.info("device_enroll_start", enroll_url=enroll_url, platform=sys.platform)

        try:
            async with httpx.AsyncClient(timeout=http_timeout) as client:  # type: ignore[misc]
                response = await client.post(enroll_url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise EnrollmentError(f"enrollment request failed: {exc}") from exc

        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise EnrollmentError(
                f"enrollment endpoint returned {response.status_code}: {detail}"
            )

        try:
            data = response.json()
        except Exception as exc:
            raise EnrollmentError(
                f"enrollment response is not valid JSON: {exc}"
            ) from exc

        try:
            cred = DeviceCredential(
                device_id=data["device_id"],
                credential=data["credential"],
                platform=sys.platform,
                extra={
                    k: v
                    for k, v in data.items()
                    if k not in ("device_id", "credential")
                },
            )
        except KeyError as exc:
            raise EnrollmentError(
                f"enrollment response missing required field {exc}"
            ) from exc

        self.save(cred)
        logger.info("device_enroll_complete", device_id=cred.device_id)
        return cred

    # ------------------------------------------------------------------ #
    # Convenience: enroll-or-load                                        #
    # ------------------------------------------------------------------ #

    async def ensure_enrolled(
        self,
        *,
        oidc_client: Any,
        jwks_cache: Any,
        enroll_url: str,
        http_timeout: float = 30.0,
        message_callback: Any = None,
    ) -> DeviceCredential:
        """Return the stored credential, enrolling via device-code if absent.

        On first call, this orchestrates the full flow:

        1. Start the OIDC device-code flow via ``oidc_client.device_login()``.
        2. POST to ``enroll_url`` to register the device and receive a
           ``device_id`` + credential.
        3. Save the credential in the OS keychain.
        4. Return the :class:`DeviceCredential`.

        On subsequent calls, the stored credential is returned directly
        without any network activity.

        Parameters
        ----------
        oidc_client:
            An :class:`~depthfusion.identity.oidc_client.OidcClient` instance.
        jwks_cache:
            A :class:`~depthfusion.identity.jwks_cache.JwksCache` instance
            shared with ``oidc_client``.
        enroll_url:
            URL of the ``POST /v2/devices/enroll`` endpoint.
        http_timeout:
            Timeout for the enrollment HTTP call.
        message_callback:
            Optional callable forwarded to ``oidc_client.device_login()``
            for displaying the user code (defaults to stderr).

        Returns
        -------
        DeviceCredential
        """
        existing = self.load()
        if existing is not None:
            logger.debug("device_already_enrolled", device_id=existing.device_id)
            return existing

        principal = await oidc_client.device_login(
            jwks_cache,
            message_callback=message_callback,
        )
        return await self.enroll(
            principal=principal,
            enroll_url=enroll_url,
            http_timeout=http_timeout,
        )


__all__ = [
    "DeviceCredential",
    "DeviceKeychain",
    "DeviceKeychainError",
    "EnrollmentError",
    "KeychainNotAvailableError",
]
