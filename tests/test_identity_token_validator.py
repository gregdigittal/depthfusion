"""Tests for depthfusion.identity — TokenValidator, models, JwksCache.from_env.

These tests construct real RS256-signed JWTs using the fixture RSA private key
in ``tests/fixtures/entra/mock-signing-key.pem`` and verify them against the
matching public JWK in ``tests/fixtures/entra/mock-jwks.json`` through a stub
``JwksCache`` (so no network is touched). Claim-level checks (iss/aud/nonce/
exp/nbf/alg) are exercised in isolation.

The local worktree's ``src`` is inserted at the front of ``sys.path`` so the
package under test is this worktree's copy, regardless of any editable install
that may point at a sibling worktree.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

# --- Ensure THIS worktree's src wins over any editable install -------------- #
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding  # noqa: E402

from depthfusion.identity import (  # noqa: E402
    DeviceCodeResult,
    JwksCache,
    Principal,
    PrincipalStore,
    TokenValidator,
)
from depthfusion.identity.errors import (  # noqa: E402
    JwksFetchError,
    TokenExpiredError,
    TokenInvalidError,
)
from depthfusion.identity.oidc_client import OidcClient  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "entra"
_JWKS_PATH = _FIXTURES / "mock-jwks.json"
_KEY_PATH = _FIXTURES / "mock-signing-key.pem"

_ISSUER = "https://login.microsoftonline.com/test-tenant/v2.0"
_AUDIENCE = "api://depthfusion-test"
_KID = "test-key-1"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_private_key():
    return serialization.load_pem_private_key(
        _KEY_PATH.read_bytes(), password=None
    )


def _make_jwt(
    claims: dict,
    *,
    alg: str = "RS256",
    kid: str | None = _KID,
    sign: bool = True,
) -> str:
    """Build a (real or fake-signature) JWT from ``claims``."""
    header: dict = {"typ": "JWT", "alg": alg}
    if kid is not None:
        header["kid"] = kid
    header_seg = _b64url(json.dumps(header).encode())
    payload_seg = _b64url(json.dumps(claims).encode())
    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")

    if sign and alg == "RS256":
        key = _load_private_key()
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        sig_seg = _b64url(signature)
    else:
        sig_seg = _b64url(b"not-a-real-signature")
    return f"{header_seg}.{payload_seg}.{sig_seg}"


class _StubJwksCache:
    """A JwksCache stand-in that returns the fixture public JWK for any kid."""

    def __init__(self, jwk: dict, *, known_kids: set[str] | None = None) -> None:
        self._jwk = jwk
        self._known_kids = known_kids

    async def get_key(self, kid: str) -> dict:
        if self._known_kids is not None and kid not in self._known_kids:
            raise JwksFetchError(f"kid {kid!r} not found")
        return self._jwk


def _fixture_jwk() -> dict:
    return json.loads(_JWKS_PATH.read_text())["keys"][0]


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "preferred_username": "alice@contoso.com",
        "name": "Alice Example",
        "groups": ["group-a", "group-b"],
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": now + 3600,
        "nbf": now - 60,
        "iat": now - 60,
    }
    claims.update(overrides)
    return claims


def _validator(jwks_cache=None) -> TokenValidator:
    cache = jwks_cache or _StubJwksCache(_fixture_jwk())
    return TokenValidator(
        jwks_cache=cache,  # type: ignore[arg-type]
        expected_issuer=_ISSUER,
        expected_audience=_AUDIENCE,
        clock_skew_seconds=300,
    )


# --------------------------------------------------------------------------- #
# Happy path                                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_valid_token_returns_claims() -> None:
    token = _make_jwt(_base_claims())
    claims = await _validator().validate(token)
    assert claims["sub"] == "user-123"
    assert claims["preferred_username"] == "alice@contoso.com"
    assert claims["groups"] == ["group-a", "group-b"]


# --------------------------------------------------------------------------- #
# Expiry                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_expired_token_raises_token_expired() -> None:
    # exp well beyond the 300s skew window in the past.
    token = _make_jwt(_base_claims(exp=int(time.time()) - 4000))
    with pytest.raises(TokenExpiredError):
        await _validator().validate(token)


# --------------------------------------------------------------------------- #
# Claim mismatches                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_wrong_issuer_raises_token_invalid() -> None:
    token = _make_jwt(_base_claims(iss="https://evil.example/v2.0"))
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


@pytest.mark.asyncio
async def test_wrong_audience_raises_token_invalid() -> None:
    token = _make_jwt(_base_claims(aud="api://some-other-app"))
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


@pytest.mark.asyncio
async def test_nonce_mismatch_raises_token_invalid() -> None:
    token = _make_jwt(_base_claims(nonce="server-nonce"))
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token, nonce="different-nonce")


@pytest.mark.asyncio
async def test_matching_nonce_passes() -> None:
    token = _make_jwt(_base_claims(nonce="abc123"))
    claims = await _validator().validate(token, nonce="abc123")
    assert claims["nonce"] == "abc123"


@pytest.mark.asyncio
async def test_bad_alg_raises_token_invalid() -> None:
    # alg=none must be rejected before any signature/claim processing.
    token = _make_jwt(_base_claims(), alg="none", sign=False)
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


@pytest.mark.asyncio
async def test_hs256_alg_rejected() -> None:
    token = _make_jwt(_base_claims(), alg="HS256", sign=False)
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


@pytest.mark.asyncio
async def test_tampered_signature_raises_token_invalid() -> None:
    valid = _make_jwt(_base_claims())
    header_seg, payload_seg, _sig = valid.split(".")
    tampered = f"{header_seg}.{payload_seg}.{_b64url(b'garbage-signature')}"
    with pytest.raises(TokenInvalidError):
        await _validator().validate(tampered)


@pytest.mark.asyncio
async def test_not_yet_valid_nbf_raises_token_invalid() -> None:
    # nbf far in the future, beyond skew.
    token = _make_jwt(_base_claims(nbf=int(time.time()) + 4000))
    with pytest.raises(TokenInvalidError):
        await _validator().validate(token)


@pytest.mark.asyncio
async def test_malformed_token_raises_token_invalid() -> None:
    with pytest.raises(TokenInvalidError):
        await _validator().validate("not.a-valid-jwt-at-all")


@pytest.mark.asyncio
async def test_missing_sub_raises_token_invalid() -> None:
    # A correctly-signed token with all other claims valid but no 'sub'
    # must be rejected — every authenticated principal needs a subject.
    claims = _base_claims()
    del claims["sub"]
    token = _make_jwt(claims)
    with pytest.raises(TokenInvalidError, match="sub"):
        await _validator().validate(token)


# --------------------------------------------------------------------------- #
# JWKS fetch / from_env                                                        #
# --------------------------------------------------------------------------- #
def test_jwks_cache_from_env_unset_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEPTHFUSION_JWKS_URI", raising=False)
    with pytest.raises(JwksFetchError):
        JwksCache.from_env()


def test_jwks_cache_from_env_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPTHFUSION_JWKS_URI", "   ")
    with pytest.raises(JwksFetchError):
        JwksCache.from_env()


def test_jwks_cache_from_env_set_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPTHFUSION_JWKS_URI", "https://example/keys")
    cache = JwksCache.from_env()
    assert cache.jwks_uri == "https://example/keys"


@pytest.mark.asyncio
async def test_unknown_kid_propagates_jwks_fetch_error() -> None:
    cache = _StubJwksCache(_fixture_jwk(), known_kids={"other-kid"})
    token = _make_jwt(_base_claims(), kid="missing-kid")
    with pytest.raises(JwksFetchError):
        await _validator(jwks_cache=cache).validate(token)


# --------------------------------------------------------------------------- #
# Dataclass defaults                                                           #
# --------------------------------------------------------------------------- #
def test_principal_defaults() -> None:
    p = Principal(principal_id="sub-1")
    assert p.principal_id == "sub-1"
    assert p.groups == []  # default_factory list, not shared
    assert p.upn == ""
    assert p.display_name == ""
    assert p.device_id is None
    assert p.access_token is None
    assert p.id_token is None
    assert p.expires_at is None
    # Ensure the default list is not shared between instances.
    p.groups.append("x")
    assert Principal(principal_id="sub-2").groups == []


def test_device_code_result_all_fields() -> None:
    result = DeviceCodeResult(
        device_code="dc",
        user_code="UC-123",
        verification_uri="https://aka.ms/devicelogin",
        expires_in=900,
        interval=5,
        verification_uri_complete="https://aka.ms/devicelogin?code=UC-123",
        message="Enter the code to sign in.",
    )
    assert result.device_code == "dc"
    assert result.user_code == "UC-123"
    assert result.verification_uri == "https://aka.ms/devicelogin"
    assert result.expires_in == 900
    assert result.interval == 5
    assert result.verification_uri_complete.endswith("UC-123")
    assert result.message == "Enter the code to sign in."


def test_device_code_result_defaults() -> None:
    result = DeviceCodeResult(
        device_code="dc",
        user_code="uc",
        verification_uri="https://example/dev",
        expires_in=600,
    )
    assert result.interval == 5
    assert result.verification_uri_complete is None
    assert result.message is None


# --------------------------------------------------------------------------- #
# build_pkce_url CSRF state                                                    #
# --------------------------------------------------------------------------- #
def test_build_pkce_url_returns_state_value() -> None:
    client = OidcClient(
        client_id="test-client",
        tenant_id="test-tenant",
    )
    url, verifier, nonce, state = client.build_pkce_url()
    assert len(state) >= 16
    assert state != nonce  # distinct values


# --------------------------------------------------------------------------- #
# AC-3 persistence wiring: login -> exchange_code -> upsert -> get_principal   #
# --------------------------------------------------------------------------- #
def _oidc_client_for_fixture() -> OidcClient:
    """OidcClient whose derived validator matches the fixture iss/aud/kid."""
    return OidcClient(
        client_id="test-client",
        tenant_id="test-tenant",
        authorize_endpoint="https://example/authorize",
        token_endpoint="https://example/token",
        device_code_endpoint="https://example/devicecode",
    )


@pytest.mark.asyncio
async def test_exchange_code_persists_principal_on_login(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exchange_code(store=...) persists the principal so get_principal works.

    This is the S-156 AC-3 round-trip: a successful login must refresh stored
    group membership. Token fields must NOT be persisted (AC-4).
    """
    monkeypatch.setenv("DEPTHFUSION_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("DEPTHFUSION_OIDC_AUDIENCE", _AUDIENCE)

    cache = _StubJwksCache(_fixture_jwk())
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    client = _oidc_client_for_fixture()

    # Mock the token endpoint POST so no network is touched. Return a real,
    # fixture-signed id_token plus an access_token to prove it is NOT persisted.
    id_token = _make_jwt(_base_claims(sub="user-ac3", groups=["g-old"]))

    async def _fake_post_token(_data: dict) -> dict:
        return {"id_token": id_token, "access_token": "secret-access-token"}

    monkeypatch.setattr(client, "_post_token", _fake_post_token)

    # Nothing persisted before login.
    assert store.get_principal("user-ac3") is None

    principal = await client.exchange_code(
        code="auth-code",
        verifier="verifier",
        jwks_cache=cache,
        store=store,
    )
    assert principal.principal_id == "user-ac3"
    assert principal.access_token == "secret-access-token"  # in-memory only

    # The login path persisted the principal.
    stored = store.get_principal("user-ac3")
    assert stored is not None
    assert stored.principal_id == "user-ac3"
    assert stored.groups == ["g-old"]
    # AC-4: token fields are NOT persisted by the store.
    assert stored.access_token is None
    assert stored.id_token is None


@pytest.mark.asyncio
async def test_exchange_code_refreshes_groups_on_relogin(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second login overwrites stored groups (AC-3 group refresh)."""
    monkeypatch.setenv("DEPTHFUSION_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("DEPTHFUSION_OIDC_AUDIENCE", _AUDIENCE)

    cache = _StubJwksCache(_fixture_jwk())
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    client = _oidc_client_for_fixture()

    async def _post_with_groups(groups: list[str]):
        token = _make_jwt(_base_claims(sub="user-refresh", groups=groups))

        async def _fake(_data: dict) -> dict:
            return {"id_token": token, "access_token": "tok"}

        return _fake

    # First login: groups = [g1].
    monkeypatch.setattr(client, "_post_token", await _post_with_groups(["g1"]))
    await client.exchange_code(
        code="c", verifier="v", jwks_cache=cache, store=store
    )
    assert store.get_principal("user-refresh").groups == ["g1"]

    # Second login: groups changed to [g2, g3] — must be refreshed in store.
    monkeypatch.setattr(
        client, "_post_token", await _post_with_groups(["g2", "g3"])
    )
    await client.exchange_code(
        code="c", verifier="v", jwks_cache=cache, store=store
    )
    assert store.get_principal("user-refresh").groups == ["g2", "g3"]


@pytest.mark.asyncio
async def test_exchange_code_without_store_does_not_persist(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting store keeps the old behaviour: nothing is persisted."""
    monkeypatch.setenv("DEPTHFUSION_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("DEPTHFUSION_OIDC_AUDIENCE", _AUDIENCE)

    cache = _StubJwksCache(_fixture_jwk())
    store = PrincipalStore(db_path=tmp_path / "identity.db")
    client = _oidc_client_for_fixture()

    id_token = _make_jwt(_base_claims(sub="user-nostore"))

    async def _fake_post_token(_data: dict) -> dict:
        return {"id_token": id_token, "access_token": "tok"}

    monkeypatch.setattr(client, "_post_token", _fake_post_token)

    principal = await client.exchange_code(
        code="c", verifier="v", jwks_cache=cache
    )
    assert principal.principal_id == "user-nostore"
    # No store passed -> nothing persisted.
    assert store.get_principal("user-nostore") is None
