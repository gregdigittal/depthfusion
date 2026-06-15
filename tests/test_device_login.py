"""Tests for OidcClient.device_login() — the high-level CLI helper (T-545).

Covers:
- message_callback is called with the DeviceCodeResult
- default stderr output when no callback is provided
- return value is whatever poll_device_code returns
- poll_interval and max_wait are forwarded to poll_device_code

All HTTP interactions are mocked; no network is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure THIS worktree's src wins over any installed package
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from depthfusion.identity import DeviceCodeResult, Principal  # noqa: E402
from depthfusion.identity.oidc_client import OidcClient  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEVICE_CODE_RESULT = DeviceCodeResult(
    device_code="dev-code-xyz",
    user_code="ABCD-1234",
    verification_uri="https://microsoft.com/devicelogin",
    expires_in=900,
    interval=5,
    verification_uri_complete="https://microsoft.com/devicelogin?code=ABCD-1234",
)

_PRINCIPAL = Principal(
    principal_id="sub-abc",
    upn="user@example.com",
    display_name="Test User",
)


def _make_client() -> OidcClient:
    return OidcClient(
        client_id="test-client",
        tenant_id="test-tenant",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_login_calls_message_callback():
    """device_login forwards the DeviceCodeResult to the caller-supplied callback."""
    client = _make_client()
    mock_jwks = MagicMock()
    received: list[DeviceCodeResult] = []

    def _callback(result: DeviceCodeResult) -> None:
        received.append(result)

    with patch.object(
        client,
        "start_device_code",
        new=AsyncMock(return_value=_DEVICE_CODE_RESULT),
    ), patch.object(
        client,
        "poll_device_code",
        new=AsyncMock(return_value=_PRINCIPAL),
    ):
        await client.device_login(mock_jwks, message_callback=_callback)

    assert len(received) == 1
    assert received[0] is _DEVICE_CODE_RESULT


@pytest.mark.asyncio
async def test_device_login_prints_to_stderr(capsys):
    """Without a callback, device_login prints the URI and user_code to stderr."""
    client = _make_client()
    mock_jwks = MagicMock()

    with patch.object(
        client,
        "start_device_code",
        new=AsyncMock(return_value=_DEVICE_CODE_RESULT),
    ), patch.object(
        client,
        "poll_device_code",
        new=AsyncMock(return_value=_PRINCIPAL),
    ):
        await client.device_login(mock_jwks)

    captured = capsys.readouterr()
    assert "ABCD-1234" in captured.err
    # verification_uri_complete takes precedence over verification_uri
    assert "https://microsoft.com/devicelogin?code=ABCD-1234" in captured.err
    assert "900" in captured.err  # expires_in


@pytest.mark.asyncio
async def test_device_login_prints_to_stderr_no_complete_uri(capsys):
    """Falls back to verification_uri when verification_uri_complete is None."""
    client = _make_client()
    mock_jwks = MagicMock()

    result_no_complete = DeviceCodeResult(
        device_code="dev-code-xyz",
        user_code="XYZW-5678",
        verification_uri="https://microsoft.com/devicelogin",
        expires_in=600,
        interval=5,
        verification_uri_complete=None,
    )

    with patch.object(
        client,
        "start_device_code",
        new=AsyncMock(return_value=result_no_complete),
    ), patch.object(
        client,
        "poll_device_code",
        new=AsyncMock(return_value=_PRINCIPAL),
    ):
        await client.device_login(mock_jwks)

    captured = capsys.readouterr()
    assert "https://microsoft.com/devicelogin" in captured.err
    assert "XYZW-5678" in captured.err


@pytest.mark.asyncio
async def test_device_login_returns_principal():
    """device_login returns exactly what poll_device_code returns."""
    client = _make_client()
    mock_jwks = MagicMock()

    with patch.object(
        client,
        "start_device_code",
        new=AsyncMock(return_value=_DEVICE_CODE_RESULT),
    ), patch.object(
        client,
        "poll_device_code",
        new=AsyncMock(return_value=_PRINCIPAL),
    ) as mock_poll:
        result = await client.device_login(mock_jwks, message_callback=lambda _: None)

    assert result is _PRINCIPAL
    mock_poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_device_login_passes_poll_params():
    """poll_interval and max_wait are forwarded to poll_device_code correctly."""
    client = _make_client()
    mock_jwks = MagicMock()

    with patch.object(
        client,
        "start_device_code",
        new=AsyncMock(return_value=_DEVICE_CODE_RESULT),
    ), patch.object(
        client,
        "poll_device_code",
        new=AsyncMock(return_value=_PRINCIPAL),
    ) as mock_poll:
        await client.device_login(
            mock_jwks,
            poll_interval=10.0,
            max_wait=300.0,
            message_callback=lambda _: None,
        )

    mock_poll.assert_awaited_once_with(
        _DEVICE_CODE_RESULT.device_code,
        mock_jwks,
        300.0,   # max_wait
        10.0,    # poll_interval (explicit override)
    )


@pytest.mark.asyncio
async def test_device_login_defaults_poll_interval_from_result():
    """When poll_interval is None, the interval from DeviceCodeResult is used."""
    client = _make_client()
    mock_jwks = MagicMock()

    with patch.object(
        client,
        "start_device_code",
        new=AsyncMock(return_value=_DEVICE_CODE_RESULT),
    ), patch.object(
        client,
        "poll_device_code",
        new=AsyncMock(return_value=_PRINCIPAL),
    ) as mock_poll:
        await client.device_login(
            mock_jwks,
            message_callback=lambda _: None,
            # poll_interval defaults to None
        )

    # interval from _DEVICE_CODE_RESULT is 5
    mock_poll.assert_awaited_once_with(
        _DEVICE_CODE_RESULT.device_code,
        mock_jwks,
        900.0,   # default max_wait
        5.0,     # from DeviceCodeResult.interval
    )
