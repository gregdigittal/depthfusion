"""Tests for device_keychain.py (T-553).

All OS-keychain interactions are mocked so the tests never touch the real
macOS Keychain, Windows Credential Manager, or Linux Secret Service.

Coverage:
- DeviceCredential.to_json / from_json round-trip
- from_json raises DeviceKeychainError on bad / missing-field JSON
- _KeychainBackend.get / set / delete (mocked keyring)
- _KeychainBackend raises KeychainNotAvailableError when keyring absent
- DeviceKeychain.load() — None when empty; DeviceCredential when present
- DeviceKeychain.save() — persists serialised credential
- DeviceKeychain.delete() — delegates to backend
- DeviceKeychain.enroll() — happy path via mocked HTTP; saves credential
- DeviceKeychain.enroll() — raises EnrollmentError on 4xx response
- DeviceKeychain.enroll() — raises EnrollmentError on missing JSON fields
- DeviceKeychain.enroll() — raises EnrollmentError on network error
- DeviceKeychain.ensure_enrolled() — returns existing credential without HTTP
- DeviceKeychain.ensure_enrolled() — enrolls when no stored credential
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure THIS worktree's src wins over any installed package
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from depthfusion.identity.device_keychain import (  # noqa: E402
    DeviceCredential,
    DeviceKeychain,
    DeviceKeychainError,
    EnrollmentError,
    KeychainNotAvailableError,
    _KeychainBackend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend(stored: str | None = None) -> _KeychainBackend:
    """Return a _KeychainBackend whose keyring is fully mocked."""
    mock_kr = MagicMock()
    mock_kr.get_password.return_value = stored
    mock_kr.set_password.return_value = None
    mock_kr.delete_password.return_value = None

    backend = _KeychainBackend.__new__(_KeychainBackend)
    backend._service = "depthfusion"
    backend._account = "device_credential"
    backend._keyring = mock_kr
    return backend


def _make_cred(device_id: str = "dev-123", credential: str = "secret-token") -> DeviceCredential:
    return DeviceCredential(device_id=device_id, credential=credential, platform="linux")


def _make_principal(access_token: str = "at-abc", principal_id: str = "sub-xyz") -> Any:
    p = MagicMock()
    p.access_token = access_token
    p.principal_id = principal_id
    return p


# ---------------------------------------------------------------------------
# DeviceCredential serialisation
# ---------------------------------------------------------------------------


class TestDeviceCredentialSerialisation:
    def test_round_trip_basic(self) -> None:
        cred = _make_cred()
        assert DeviceCredential.from_json(cred.to_json()) == cred

    def test_round_trip_with_extra(self) -> None:
        cred = DeviceCredential(
            device_id="d1",
            credential="c1",
            platform="darwin",
            extra={"lease_expires": 1234567890},
        )
        restored = DeviceCredential.from_json(cred.to_json())
        assert restored.device_id == "d1"
        assert restored.extra["lease_expires"] == 1234567890

    def test_from_json_bad_json(self) -> None:
        with pytest.raises(DeviceKeychainError, match="malformed"):
            DeviceCredential.from_json("not-json{{{")

    def test_from_json_missing_device_id(self) -> None:
        raw = json.dumps({"credential": "tok"})
        with pytest.raises(DeviceKeychainError, match="missing required field"):
            DeviceCredential.from_json(raw)

    def test_from_json_missing_credential(self) -> None:
        raw = json.dumps({"device_id": "d1"})
        with pytest.raises(DeviceKeychainError, match="missing required field"):
            DeviceCredential.from_json(raw)

    def test_to_json_excludes_empty_extra(self) -> None:
        cred = _make_cred()
        data = json.loads(cred.to_json())
        assert "extra" not in data

    def test_platform_preserved(self) -> None:
        cred = DeviceCredential(device_id="d", credential="c", platform="win32")
        restored = DeviceCredential.from_json(cred.to_json())
        assert restored.platform == "win32"


# ---------------------------------------------------------------------------
# _KeychainBackend
# ---------------------------------------------------------------------------


class TestKeychainBackend:
    def test_get_returns_stored_value(self) -> None:
        backend = _make_backend(stored='{"device_id":"d","credential":"c","platform":"linux"}')
        assert backend.get() is not None

    def test_get_returns_none_when_empty(self) -> None:
        backend = _make_backend(stored=None)
        assert backend.get() is None

    def test_set_calls_keyring(self) -> None:
        backend = _make_backend()
        backend.set("some-secret")
        backend._keyring.set_password.assert_called_once_with(
            "depthfusion", "device_credential", "some-secret"
        )

    def test_set_raises_on_keyring_error(self) -> None:
        backend = _make_backend()
        backend._keyring.set_password.side_effect = RuntimeError("keychain locked")
        with pytest.raises(KeychainNotAvailableError, match="keychain"):
            backend.set("secret")

    def test_delete_suppresses_error(self) -> None:
        backend = _make_backend()
        backend._keyring.delete_password.side_effect = Exception("not found")
        backend.delete()  # Should not raise

    def test_import_keyring_raises_when_absent(self) -> None:
        with patch.dict(sys.modules, {"keyring": None}):
            with pytest.raises(KeychainNotAvailableError, match="keyring"):
                _KeychainBackend._import_keyring()

    def test_get_suppresses_get_error_and_returns_none(self) -> None:
        backend = _make_backend()
        backend._keyring.get_password.side_effect = OSError("no keychain")
        result = backend.get()
        assert result is None


# ---------------------------------------------------------------------------
# DeviceKeychain — load / save / delete
# ---------------------------------------------------------------------------


class TestDeviceKeychainLoadSaveDelete:
    def test_load_returns_none_when_empty(self) -> None:
        kc = DeviceKeychain(_backend=_make_backend(stored=None))
        assert kc.load() is None

    def test_load_returns_credential_when_present(self) -> None:
        cred = _make_cred()
        kc = DeviceKeychain(_backend=_make_backend(stored=cred.to_json()))
        loaded = kc.load()
        assert loaded is not None
        assert loaded.device_id == "dev-123"
        assert loaded.credential == "secret-token"

    def test_save_calls_backend_set(self) -> None:
        backend = _make_backend()
        kc = DeviceKeychain(_backend=backend)
        cred = _make_cred(device_id="d-save", credential="tok-save")
        kc.save(cred)
        backend._keyring.set_password.assert_called_once()
        call_args = backend._keyring.set_password.call_args
        stored_json = call_args[0][2]
        restored = DeviceCredential.from_json(stored_json)
        assert restored.device_id == "d-save"

    def test_delete_calls_backend_delete(self) -> None:
        backend = _make_backend()
        kc = DeviceKeychain(_backend=backend)
        kc.delete()
        backend._keyring.delete_password.assert_called_once()


# ---------------------------------------------------------------------------
# DeviceKeychain — enroll
# ---------------------------------------------------------------------------


class _MockResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> Any:
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("not dict")


def _mock_httpx_client(status_code: int = 200, body: Any = None):
    """Return a context-manager mock that yields an httpx-like client."""
    if body is None:
        body = {"device_id": "srv-dev-001", "credential": "server-cred"}
    response = _MockResponse(status_code, body)
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestDeviceKeychainEnroll:
    @pytest.mark.asyncio
    async def test_enroll_happy_path(self) -> None:
        backend = _make_backend(stored=None)
        kc = DeviceKeychain(_backend=backend)
        principal = _make_principal()

        cm = _mock_httpx_client(200, {"device_id": "srv-001", "credential": "cred-xyz"})
        with patch("depthfusion.identity.device_keychain.httpx.AsyncClient", return_value=cm):
            cred = await kc.enroll(
                principal=principal,
                enroll_url="https://server/v2/devices/enroll",
            )

        assert cred.device_id == "srv-001"
        assert cred.credential == "cred-xyz"
        # Credential was persisted
        backend._keyring.set_password.assert_called_once()

    @pytest.mark.asyncio
    async def test_enroll_4xx_raises_enrollment_error(self) -> None:
        backend = _make_backend(stored=None)
        kc = DeviceKeychain(_backend=backend)
        principal = _make_principal()

        cm = _mock_httpx_client(403, {"error": "forbidden"})
        with patch("depthfusion.identity.device_keychain.httpx.AsyncClient", return_value=cm):
            with pytest.raises(EnrollmentError, match="403"):
                await kc.enroll(
                    principal=principal,
                    enroll_url="https://server/v2/devices/enroll",
                )

    @pytest.mark.asyncio
    async def test_enroll_missing_device_id_raises_enrollment_error(self) -> None:
        backend = _make_backend(stored=None)
        kc = DeviceKeychain(_backend=backend)
        principal = _make_principal()

        cm = _mock_httpx_client(200, {"credential": "only-cred"})
        with patch("depthfusion.identity.device_keychain.httpx.AsyncClient", return_value=cm):
            with pytest.raises(EnrollmentError, match="missing required field"):
                await kc.enroll(
                    principal=principal,
                    enroll_url="https://server/v2/devices/enroll",
                )

    @pytest.mark.asyncio
    async def test_enroll_missing_credential_raises_enrollment_error(self) -> None:
        backend = _make_backend(stored=None)
        kc = DeviceKeychain(_backend=backend)
        principal = _make_principal()

        cm = _mock_httpx_client(200, {"device_id": "d1"})
        with patch("depthfusion.identity.device_keychain.httpx.AsyncClient", return_value=cm):
            with pytest.raises(EnrollmentError, match="missing required field"):
                await kc.enroll(
                    principal=principal,
                    enroll_url="https://server/v2/devices/enroll",
                )

    @pytest.mark.asyncio
    async def test_enroll_network_error_raises_enrollment_error(self) -> None:
        import httpx

        backend = _make_backend(stored=None)
        kc = DeviceKeychain(_backend=backend)
        principal = _make_principal()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("depthfusion.identity.device_keychain.httpx.AsyncClient", return_value=cm):
            with pytest.raises(EnrollmentError, match="enrollment request failed"):
                await kc.enroll(
                    principal=principal,
                    enroll_url="https://server/v2/devices/enroll",
                )

    @pytest.mark.asyncio
    async def test_enroll_passes_access_token_as_bearer(self) -> None:
        backend = _make_backend(stored=None)
        kc = DeviceKeychain(_backend=backend)
        principal = _make_principal(access_token="my-at-token")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_MockResponse(200, {"device_id": "d", "credential": "c"})
        )
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("depthfusion.identity.device_keychain.httpx.AsyncClient", return_value=cm):
            await kc.enroll(
                principal=principal,
                enroll_url="https://server/v2/devices/enroll",
            )

        _, kwargs = mock_client.post.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer my-at-token"

    @pytest.mark.asyncio
    async def test_enroll_extra_fields_preserved_in_credential(self) -> None:
        backend = _make_backend(stored=None)
        kc = DeviceKeychain(_backend=backend)
        principal = _make_principal()

        server_body = {
            "device_id": "d99",
            "credential": "c99",
            "lease_expires": 9999999,
            "owner": "alice",
        }
        cm = _mock_httpx_client(200, server_body)
        with patch("depthfusion.identity.device_keychain.httpx.AsyncClient", return_value=cm):
            cred = await kc.enroll(
                principal=principal,
                enroll_url="https://server/v2/devices/enroll",
            )

        assert cred.extra["lease_expires"] == 9999999
        assert cred.extra["owner"] == "alice"


# ---------------------------------------------------------------------------
# DeviceKeychain — ensure_enrolled
# ---------------------------------------------------------------------------


class TestEnsureEnrolled:
    @pytest.mark.asyncio
    async def test_returns_existing_without_network(self) -> None:
        cred = _make_cred(device_id="existing-dev")
        backend = _make_backend(stored=cred.to_json())
        kc = DeviceKeychain(_backend=backend)

        oidc_client = AsyncMock()
        jwks_cache = MagicMock()

        result = await kc.ensure_enrolled(
            oidc_client=oidc_client,
            jwks_cache=jwks_cache,
            enroll_url="https://server/v2/devices/enroll",
        )

        assert result.device_id == "existing-dev"
        oidc_client.device_login.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrolls_when_no_stored_credential(self) -> None:
        backend = _make_backend(stored=None)
        kc = DeviceKeychain(_backend=backend)

        principal = _make_principal(access_token="fresh-at")
        oidc_client = AsyncMock()
        oidc_client.device_login = AsyncMock(return_value=principal)
        jwks_cache = MagicMock()

        server_body = {"device_id": "new-dev", "credential": "new-cred"}
        cm = _mock_httpx_client(200, server_body)
        with patch("depthfusion.identity.device_keychain.httpx.AsyncClient", return_value=cm):
            result = await kc.ensure_enrolled(
                oidc_client=oidc_client,
                jwks_cache=jwks_cache,
                enroll_url="https://server/v2/devices/enroll",
            )

        assert result.device_id == "new-dev"
        oidc_client.device_login.assert_called_once()
        # Credential should now be persisted
        backend._keyring.set_password.assert_called_once()
