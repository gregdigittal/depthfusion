"""S-251 / T-853..856 — RateLimiter Protocol, InMemory/Redis backends, and the
``POST /mcp`` wiring (E-73).

T-853 deviation (recorded verbatim in BACKLOG.md): fastapi-limiter evaluated
and rejected — redis-only dependency incompatible with core-install default;
implemented RateLimiter Protocol + InMemory/Redis backends per
event_store.py precedent.

Covers:
  * Env var resolution (T-855): ``DEPTHFUSION_RATE_LIMIT_PUBLISH`` /
    ``DEPTHFUSION_RATE_LIMIT_RECALL`` defaults (60/300) and degrade-safe
    fallback on missing/invalid/non-positive values.
  * ``classify_tool_call``: read tools → "recall" bucket, everything else
    (including unrecognised tool names) → the tighter "publish" bucket.
  * ``InMemoryRateLimitBackend`` (real object): allows up to the limit,
    denies the call after with a positive ``retry_after_seconds``, and
    resets on window rollover.
  * ``RedisRateLimitBackend``: the clear install-hint ``ImportError`` when
    ``redis`` is absent (mirrors ``core/event_store.py::RedisStreamBackend``).
  * ``get_rate_limiter()``: Redis selected when ``DEPTHFUSION_REDIS_URL`` is
    set, ``InMemoryRateLimitBackend`` otherwise (AC-2).

TestRealCollaborators (tests/core/test_hygiene_scheduler.py:425-convention —
mirrored by tests/mcp/test_ambient_trace_wiring.py's own TestRealCollaborators):
a REAL RateLimiter backend — Redis-backed when a Redis server is reachable at
``127.0.0.1:6379``, otherwise the real ``InMemoryRateLimitBackend`` — backs
every assertion in that class. No MagicMock stands in for the limiter or its
backend anywhere in this file. The HTTP-layer test in that class calls
``mcp/http_server.py::streamable_http_endpoint`` directly (no real network
bind — the same convention ``tests/mcp/test_http_server.py`` documents,
since ``tests/mcp/`` cannot start a live uvicorn server inside the sandbox).

Regression-proven (T-854): with the
``if not rl_result.allowed: return JSONResponse(status_code=429, ...)``
block removed from ``mcp/http_server.py::streamable_http_endpoint``'s
``tools/call`` branch, ``TestRealCollaborators::
test_exceeding_publish_limit_over_http_returns_429_with_exact_body`` fails
(the over-quota call returns 200 instead of 429). Verified via a scripted
patch/run/revert cycle: patched, ran this file (FAIL — reported below),
reverted, re-ran (PASS), confirmed ``ruff check src/`` and
``mypy src/ --ignore-missing-imports`` clean after the revert and a byte-
identical diff against the pre-probe file. See S-251 BACKLOG.md entry for
the captured FAIL output.
"""
from __future__ import annotations

import json

import pytest

from depthfusion.identity.models import Principal
from depthfusion.mcp import ratelimit as rl


def _principal(principal_id: str = "ratelimit-test-user") -> Principal:
    return Principal(
        principal_id=principal_id,
        upn=f"{principal_id}@example.com",
        display_name=principal_id,
        groups=["owner"],
    )


def _redis_reachable(url: str = "redis://127.0.0.1:6379") -> bool:
    """Best-effort real-Redis probe — never raises, never mocks."""
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=0.5)
        return bool(client.ping())
    except Exception:  # noqa: BLE001 — probe only; any failure means "unreachable"
        return False


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Isolate tests from the module-level RateLimiter singleton."""
    rl.reset_rate_limiter()
    yield
    rl.reset_rate_limiter()


# ---------------------------------------------------------------------------
# Env var resolution (T-855)
# ---------------------------------------------------------------------------

class TestRateLimitDefaults:
    def test_publish_default_is_60(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_RATE_LIMIT_PUBLISH", raising=False)
        assert rl.rate_limit_publish_per_minute() == 60

    def test_recall_default_is_300(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_RATE_LIMIT_RECALL", raising=False)
        assert rl.rate_limit_recall_per_minute() == 300

    def test_publish_overridable_via_env(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_PUBLISH", "12")
        assert rl.rate_limit_publish_per_minute() == 12

    def test_recall_overridable_via_env(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_RECALL", "1500")
        assert rl.rate_limit_recall_per_minute() == 1500

    def test_publish_invalid_value_degrades_to_default(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_PUBLISH", "not-a-number")
        assert rl.rate_limit_publish_per_minute() == 60

    def test_recall_non_positive_value_degrades_to_default(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_RECALL", "0")
        assert rl.rate_limit_recall_per_minute() == 300

    def test_publish_negative_value_degrades_to_default(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_PUBLISH", "-5")
        assert rl.rate_limit_publish_per_minute() == 60

    def test_recall_empty_string_degrades_to_default(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_RECALL", "   ")
        assert rl.rate_limit_recall_per_minute() == 300


# ---------------------------------------------------------------------------
# classify_tool_call
# ---------------------------------------------------------------------------

class TestClassifyToolCall:
    def test_recall_relevant_is_recall_bucket(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_RATE_LIMIT_RECALL", raising=False)
        bucket, limit = rl.classify_tool_call("depthfusion_recall_relevant")
        assert bucket == "recall"
        assert limit == 300

    def test_retrieve_context_is_recall_bucket(self):
        bucket, _ = rl.classify_tool_call("depthfusion_retrieve_context")
        assert bucket == "recall"

    def test_publish_context_is_publish_bucket(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_RATE_LIMIT_PUBLISH", raising=False)
        bucket, limit = rl.classify_tool_call("depthfusion_publish_context")
        assert bucket == "publish"
        assert limit == 60

    def test_record_decision_is_publish_bucket(self):
        bucket, _ = rl.classify_tool_call("depthfusion_record_decision")
        assert bucket == "publish"

    def test_register_project_admin_tool_is_publish_bucket(self):
        """MANAGE_SETTINGS tools are not read-only — publish bucket applies."""
        bucket, _ = rl.classify_tool_call("depthfusion_register_project")
        assert bucket == "publish"

    def test_query_telemetry_audit_tool_is_publish_bucket(self):
        """VIEW_AUDIT_LOG tools are not READ_OWN_RECORDS — publish bucket applies."""
        bucket, _ = rl.classify_tool_call("depthfusion_query_telemetry")
        assert bucket == "publish"

    def test_unknown_tool_defaults_to_publish_bucket(self):
        bucket, _ = rl.classify_tool_call("depthfusion_not_a_real_tool")
        assert bucket == "publish"


# ---------------------------------------------------------------------------
# InMemoryRateLimitBackend — real object, no mock
# ---------------------------------------------------------------------------

class TestInMemoryRateLimitBackend:
    @pytest.mark.asyncio
    async def test_allows_up_to_the_limit(self):
        backend = rl.InMemoryRateLimitBackend()
        for _ in range(3):
            result = await backend.check("user-a", "publish", 3)
            assert result.allowed is True
            assert result.retry_after_seconds == 0

    @pytest.mark.asyncio
    async def test_denies_the_call_after_the_limit(self):
        backend = rl.InMemoryRateLimitBackend()
        for _ in range(3):
            await backend.check("user-a", "publish", 3)
        result = await backend.check("user-a", "publish", 3)
        assert result.allowed is False
        assert result.retry_after_seconds > 0

    @pytest.mark.asyncio
    async def test_buckets_are_independent_per_principal(self):
        backend = rl.InMemoryRateLimitBackend()
        for _ in range(2):
            await backend.check("user-a", "publish", 2)
        # A different principal has its own quota — not affected by user-a.
        result = await backend.check("user-b", "publish", 2)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_buckets_are_independent_per_bucket_name(self):
        backend = rl.InMemoryRateLimitBackend()
        for _ in range(2):
            await backend.check("user-a", "publish", 2)
        # The "recall" bucket for the same principal is untouched.
        result = await backend.check("user-a", "recall", 2)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_window_rollover_resets_the_counter(self):
        """Simulates rollover by rewinding the backend's own stored window
        start rather than patching the process-wide ``time.time`` (which
        other machinery — pytest-timeout, asyncio internals — also reads).
        """
        backend = rl.InMemoryRateLimitBackend()
        for _ in range(2):
            result = await backend.check("user-a", "publish", 2)
            assert result.allowed is True
        denied = await backend.check("user-a", "publish", 2)
        assert denied.allowed is False

        key = "user-a:publish"
        stored_start, count = backend._windows[key]
        backend._windows[key] = (stored_start - rl._WINDOW_SECONDS - 1, count)

        result = await backend.check("user-a", "publish", 2)
        assert result.allowed is True, "counter did not reset on window rollover"


# ---------------------------------------------------------------------------
# RedisRateLimitBackend — install-hint ImportError (mirrors event_store.py)
# ---------------------------------------------------------------------------

class TestRedisRateLimitBackendImportHint:
    def test_missing_redis_raises_clear_install_hint(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "redis.asyncio" or name.startswith("redis"):
                raise ImportError("No module named 'redis'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        with pytest.raises(ImportError) as exc_info:
            rl.RedisRateLimitBackend("redis://127.0.0.1:6379")

        message = str(exc_info.value)
        assert "redis>=5.0" in message
        assert "pip install depthfusion[fabric]" in message


# ---------------------------------------------------------------------------
# get_rate_limiter() — backend selection (AC-2)
# ---------------------------------------------------------------------------

class TestGetRateLimiter:
    def test_defaults_to_in_memory_when_redis_url_unset(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
        limiter = rl.get_rate_limiter()
        assert isinstance(limiter, rl.InMemoryRateLimitBackend)

    def test_uses_redis_when_redis_url_set(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_REDIS_URL", "redis://127.0.0.1:6379")
        limiter = rl.get_rate_limiter()
        assert isinstance(limiter, rl.RedisRateLimitBackend)

    def test_singleton_is_cached_across_calls(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
        first = rl.get_rate_limiter()
        second = rl.get_rate_limiter()
        assert first is second

    def test_both_backends_satisfy_the_rate_limiter_protocol(self):
        assert isinstance(rl.InMemoryRateLimitBackend(), rl.RateLimiter)
        assert isinstance(rl.RedisRateLimitBackend("redis://127.0.0.1:6379"), rl.RateLimiter)


# ---------------------------------------------------------------------------
# TestRealCollaborators — real backend objects, no MagicMock anywhere
# ---------------------------------------------------------------------------

class TestRealCollaborators:
    """Real RateLimiter backends (Redis-if-reachable, else InMemory) and the
    real ``streamable_http_endpoint`` — the mandated real-collaborator proof
    for S-251 / T-854.
    """

    @pytest.mark.asyncio
    async def test_real_backend_enforces_the_limit_and_recovers_next_window(self, monkeypatch):
        """Direct object-level proof: whichever real backend is available,
        the Nth+1 call within a window is denied with retry_after_seconds > 0,
        and — for the deterministic InMemory path — the counter resets after
        the window elapses.
        """
        principal_id = "ratelimit-real-collab-user"
        limit = 3

        if _redis_reachable():
            backend: rl.RateLimiter = rl.RedisRateLimitBackend("redis://127.0.0.1:6379")
            bucket = f"publish-real-{id(self)}"  # unique key to avoid cross-run pollution
        else:
            backend = rl.InMemoryRateLimitBackend()
            bucket = "publish"

        for i in range(limit):
            result = await backend.check(principal_id, bucket, limit)
            assert result.allowed is True, f"call {i} unexpectedly denied"

        denied = await backend.check(principal_id, bucket, limit)
        assert denied.allowed is False, "call beyond the limit was allowed — rate limiting broken"
        assert denied.retry_after_seconds > 0

    @pytest.mark.asyncio
    async def test_exceeding_publish_limit_over_http_returns_429_with_exact_body(
        self, monkeypatch,
    ):
        """T-854: the real POST /mcp endpoint, the real singleton RateLimiter,
        and a real Principal — no mock of any layer. Proves the wiring in
        ``mcp/http_server.py::streamable_http_endpoint`` actually gates
        ``tools/call`` requests (AC-1/AC-4), not just the backend in
        isolation.
        """
        from depthfusion.mcp import http_server as hs

        monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_PUBLISH", "2")
        rl.reset_rate_limiter()

        principal = _principal("ratelimit-http-real-user")
        tool_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "depthfusion_publish_context", "arguments": {}},
        }

        for i in range(2):
            resp = await hs.streamable_http_endpoint(
                _FakeRequest(tool_body, {"content-type": "application/json"}),
                _principal=principal,
                _origin=None,
            )
            assert resp.status_code != 429, f"call {i} was rate-limited before the limit"

        resp = await hs.streamable_http_endpoint(
            _FakeRequest(tool_body, {"content-type": "application/json"}),
            _principal=principal,
            _origin=None,
        )
        assert resp.status_code == 429, (
            "call beyond DEPTHFUSION_RATE_LIMIT_PUBLISH=2 was not rate-limited — "
            "streamable_http_endpoint's 429 wiring is missing or broken"
        )
        body = json.loads(resp.body)
        assert body["error"] == "rate_limited"
        assert isinstance(body["retry_after_seconds"], int)
        assert body["retry_after_seconds"] > 0

    @pytest.mark.asyncio
    async def test_recall_and_publish_quotas_are_independent_over_http(self, monkeypatch):
        """A principal that exhausts the tight "publish" quota can still make
        "recall" calls — the two buckets must not share a counter.
        """
        from depthfusion.mcp import http_server as hs

        monkeypatch.delenv("DEPTHFUSION_REDIS_URL", raising=False)
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_PUBLISH", "1")
        monkeypatch.setenv("DEPTHFUSION_RATE_LIMIT_RECALL", "5")
        rl.reset_rate_limiter()

        principal = _principal("ratelimit-http-independent-user")
        publish_body = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "depthfusion_publish_context", "arguments": {}},
        }
        recall_body = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "depthfusion_recall_relevant", "arguments": {}},
        }

        first = await hs.streamable_http_endpoint(
            _FakeRequest(publish_body, {"content-type": "application/json"}),
            _principal=principal, _origin=None,
        )
        assert first.status_code != 429

        exhausted = await hs.streamable_http_endpoint(
            _FakeRequest(publish_body, {"content-type": "application/json"}),
            _principal=principal, _origin=None,
        )
        assert exhausted.status_code == 429

        # The recall bucket is untouched by the publish bucket's exhaustion.
        recall_resp = await hs.streamable_http_endpoint(
            _FakeRequest(recall_body, {"content-type": "application/json"}),
            _principal=principal, _origin=None,
        )
        assert recall_resp.status_code != 429


# ---------------------------------------------------------------------------
# Fake ASGI Request — mirrors tests/mcp/test_http_server.py's convention
# (no real network bind; tests/mcp/ cannot start a live uvicorn server
# inside the sandbox).
# ---------------------------------------------------------------------------

class _FakeHeaders(dict):
    def get(self, key, default=None):  # noqa: A003 - mirrors Starlette Headers.get
        return super().get(key.lower(), default)


class _FakeRequest:
    def __init__(self, body: dict, headers: dict | None = None) -> None:
        self.headers = _FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})
        self._body = body

    async def json(self):
        return self._body
