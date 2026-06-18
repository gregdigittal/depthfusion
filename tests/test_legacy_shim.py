"""Tests for the legacy-token compatibility shim.

Covers:
* Disabled shim (``DEPTHFUSION_V2_LEGACY_AUTH`` not set or ``!= "1"``)
* Enabled shim — token match produces a LegacyPrincipal
* Enabled shim — token mismatch returns None
* Empty / non-string bearer tokens
* Deprecation warning is logged on every successful authentication
* ``from_env`` factory respects environment variables
* Misconfiguration: env var enabled but token missing
* LegacyPrincipal is a subclass of Principal
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
import structlog.testing

from depthfusion.identity.legacy_shim import LegacyPrincipal, LegacyTokenShim
from depthfusion.identity.models import Principal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_GOOD_TOKEN = "supersecret-legacy-token"


@pytest.fixture()
def enabled_shim() -> LegacyTokenShim:
    """A shim that is enabled and accepts ``_GOOD_TOKEN``."""
    return LegacyTokenShim(_GOOD_TOKEN, enabled=True)


@pytest.fixture()
def disabled_shim() -> LegacyTokenShim:
    """A shim that is disabled (enabled=False)."""
    return LegacyTokenShim(_GOOD_TOKEN, enabled=False)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        LegacyTokenShim("", enabled=True)


def test_constructor_rejects_empty_token_disabled_too() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        LegacyTokenShim("", enabled=False)


# ---------------------------------------------------------------------------
# Disabled shim — always returns None, never logs
# ---------------------------------------------------------------------------


def test_disabled_shim_returns_none_for_correct_token(
    disabled_shim: LegacyTokenShim,
) -> None:
    result = disabled_shim.authenticate(_GOOD_TOKEN)
    assert result is None


def test_disabled_shim_returns_none_for_wrong_token(
    disabled_shim: LegacyTokenShim,
) -> None:
    result = disabled_shim.authenticate("wrong-token")
    assert result is None


def test_disabled_shim_returns_none_for_empty_string(
    disabled_shim: LegacyTokenShim,
) -> None:
    result = disabled_shim.authenticate("")
    assert result is None


def test_disabled_shim_enabled_property_is_false(
    disabled_shim: LegacyTokenShim,
) -> None:
    assert disabled_shim.enabled is False


# ---------------------------------------------------------------------------
# Enabled shim — correct token
# ---------------------------------------------------------------------------


def test_enabled_shim_returns_legacy_principal_for_correct_token(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN)
    assert result is not None
    assert isinstance(result, LegacyPrincipal)


def test_legacy_principal_is_subclass_of_principal(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN)
    assert isinstance(result, Principal)


def test_legacy_principal_has_expected_principal_id(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN)
    assert result is not None
    assert result.principal_id == "legacy:token"


def test_legacy_principal_has_display_name(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN)
    assert result is not None
    assert result.display_name == "Legacy API Token"


def test_legacy_principal_has_empty_groups(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN)
    assert result is not None
    assert result.groups == []


def test_legacy_principal_has_empty_upn(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN)
    assert result is not None
    assert result.upn == ""


def test_legacy_principal_has_none_access_token(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN)
    assert result is not None
    assert result.access_token is None


def test_legacy_principal_has_none_expires_at(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN)
    assert result is not None
    assert result.expires_at is None


def test_enabled_shim_enabled_property_is_true(
    enabled_shim: LegacyTokenShim,
) -> None:
    assert enabled_shim.enabled is True


# ---------------------------------------------------------------------------
# Enabled shim — wrong / invalid tokens
# ---------------------------------------------------------------------------


def test_enabled_shim_returns_none_for_wrong_token(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate("totally-wrong")
    assert result is None


def test_enabled_shim_returns_none_for_empty_string(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate("")
    assert result is None


def test_enabled_shim_returns_none_for_whitespace(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate("   ")
    assert result is None


def test_enabled_shim_returns_none_for_prefix_match(
    enabled_shim: LegacyTokenShim,
) -> None:
    """A prefix of the correct token must NOT authenticate."""
    result = enabled_shim.authenticate(_GOOD_TOKEN[:5])
    assert result is None


def test_enabled_shim_returns_none_for_suffix_match(
    enabled_shim: LegacyTokenShim,
) -> None:
    """A suffix of the correct token must NOT authenticate."""
    result = enabled_shim.authenticate(_GOOD_TOKEN[5:])
    assert result is None


def test_enabled_shim_case_sensitive(
    enabled_shim: LegacyTokenShim,
) -> None:
    result = enabled_shim.authenticate(_GOOD_TOKEN.upper())
    assert result is None


# ---------------------------------------------------------------------------
# Deprecation warning logging
# ---------------------------------------------------------------------------


def test_deprecation_warning_logged_on_success(
    enabled_shim: LegacyTokenShim,
) -> None:
    """A warning must be logged every time the legacy token is accepted."""
    with structlog.testing.capture_logs() as captured:
        enabled_shim.authenticate(_GOOD_TOKEN)

    warnings = [e for e in captured if e.get("log_level") == "warning"]
    assert len(warnings) >= 1
    assert any("deprecated_token_used" in e.get("event", "") for e in warnings)


def test_deprecation_warning_logged_on_every_call(
    enabled_shim: LegacyTokenShim,
) -> None:
    """Each successful authentication must emit its own warning."""
    with structlog.testing.capture_logs() as captured:
        enabled_shim.authenticate(_GOOD_TOKEN)
        enabled_shim.authenticate(_GOOD_TOKEN)
        enabled_shim.authenticate(_GOOD_TOKEN)

    warnings = [
        e
        for e in captured
        if e.get("log_level") == "warning"
        and "deprecated_token_used" in e.get("event", "")
    ]
    assert len(warnings) >= 3


def test_no_warning_logged_on_failed_auth(
    enabled_shim: LegacyTokenShim,
) -> None:
    """No deprecation log emitted when the token is wrong."""
    with structlog.testing.capture_logs() as captured:
        enabled_shim.authenticate("wrong-token")

    deprecation_warnings = [
        e
        for e in captured
        if "deprecated_token_used" in e.get("event", "")
    ]
    assert deprecation_warnings == []


def test_no_warning_logged_when_shim_disabled(
    disabled_shim: LegacyTokenShim,
) -> None:
    """Disabled shim must stay silent."""
    with structlog.testing.capture_logs() as captured:
        disabled_shim.authenticate(_GOOD_TOKEN)

    deprecation_warnings = [
        e
        for e in captured
        if "deprecated_token_used" in e.get("event", "")
    ]
    assert deprecation_warnings == []


# ---------------------------------------------------------------------------
# from_env factory
# ---------------------------------------------------------------------------


def test_from_env_disabled_when_env_var_absent() -> None:
    env = {}
    with patch.dict("os.environ", env, clear=True):
        shim = LegacyTokenShim.from_env()
    assert shim.enabled is False


def test_from_env_disabled_when_env_var_zero() -> None:
    env = {"DEPTHFUSION_V2_LEGACY_AUTH": "0", "DEPTHFUSION_API_TOKEN": _GOOD_TOKEN}
    with patch.dict("os.environ", env, clear=True):
        shim = LegacyTokenShim.from_env()
    assert shim.enabled is False


def test_from_env_disabled_when_env_var_false() -> None:
    env = {"DEPTHFUSION_V2_LEGACY_AUTH": "false", "DEPTHFUSION_API_TOKEN": _GOOD_TOKEN}
    with patch.dict("os.environ", env, clear=True):
        shim = LegacyTokenShim.from_env()
    assert shim.enabled is False


def test_from_env_enabled_when_env_var_one() -> None:
    env = {"DEPTHFUSION_V2_LEGACY_AUTH": "1", "DEPTHFUSION_API_TOKEN": _GOOD_TOKEN}
    with patch.dict("os.environ", env, clear=True):
        shim = LegacyTokenShim.from_env()
    assert shim.enabled is True


def test_from_env_authenticates_correct_token() -> None:
    env = {"DEPTHFUSION_V2_LEGACY_AUTH": "1", "DEPTHFUSION_API_TOKEN": _GOOD_TOKEN}
    with patch.dict("os.environ", env, clear=True):
        shim = LegacyTokenShim.from_env()
    result = shim.authenticate(_GOOD_TOKEN)
    assert result is not None
    assert isinstance(result, LegacyPrincipal)


def test_from_env_rejects_wrong_token() -> None:
    env = {"DEPTHFUSION_V2_LEGACY_AUTH": "1", "DEPTHFUSION_API_TOKEN": _GOOD_TOKEN}
    with patch.dict("os.environ", env, clear=True):
        shim = LegacyTokenShim.from_env()
    result = shim.authenticate("not-the-right-token")
    assert result is None


def test_from_env_disabled_when_token_missing_but_enabled() -> None:
    """When LEGACY_AUTH=1 but DEPTHFUSION_API_TOKEN is absent, disable with a warning."""
    env = {"DEPTHFUSION_V2_LEGACY_AUTH": "1"}
    with patch.dict("os.environ", env, clear=True):
        with structlog.testing.capture_logs() as captured:
            shim = LegacyTokenShim.from_env()

    assert shim.enabled is False
    misconfigured = [
        e for e in captured if "misconfigured" in e.get("event", "")
    ]
    assert len(misconfigured) >= 1


def test_from_env_with_whitespace_around_token() -> None:
    """Leading/trailing whitespace in the env var is stripped."""
    env = {
        "DEPTHFUSION_V2_LEGACY_AUTH": " 1 ",
        "DEPTHFUSION_API_TOKEN": f" {_GOOD_TOKEN} ",
    }
    with patch.dict("os.environ", env, clear=True):
        shim = LegacyTokenShim.from_env()
    assert shim.enabled is True
    # The token stored is the stripped version, so we pass the stripped value.
    result = shim.authenticate(_GOOD_TOKEN)
    assert result is not None


# ---------------------------------------------------------------------------
# __all__ surface
# ---------------------------------------------------------------------------


def test_module_all_exports() -> None:
    from depthfusion.identity import legacy_shim  # noqa: PLC0415

    assert "LegacyPrincipal" in legacy_shim.__all__
    assert "LegacyTokenShim" in legacy_shim.__all__
