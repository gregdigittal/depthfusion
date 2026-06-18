"""Tests for T-573: post-rank ACL verification + leak counters.

Verifies that verify_acl():
- Removes results whose acl_allow does not include the principal.
- Passes results that do include the principal.
- Passes results with no acl_allow (public / legacy).
- Honours group membership.
- Bypasses the check entirely when principal=None.
- Handles JSON-encoded acl_allow strings (Chroma storage format).
- Increments the acl_leak_prevented telemetry counter on leaks.
- Never raises (fail-open contract).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.retrieval.acl_verifier import verify_acl

# ---------------------------------------------------------------------------
# Minimal Principal stub
# ---------------------------------------------------------------------------


@dataclass
class _P:
    principal_id: str
    groups: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc(chunk_id: str, acl_allow: list[str] | None) -> dict[str, Any]:
    d: dict[str, Any] = {"chunk_id": chunk_id, "content": "text"}
    if acl_allow is not None:
        d["acl_allow"] = acl_allow
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVerifyAcl:
    """Core filtering contract."""

    def test_authorized_result_passes(self) -> None:
        p = _P("alice")
        results = [_doc("r1", ["alice"])]
        assert verify_acl(results, principal=p) == results

    def test_unauthorized_result_removed(self) -> None:
        p = _P("alice")
        results = [_doc("r1", ["bob"])]
        assert verify_acl(results, principal=p) == []

    def test_mixed_results_filtered(self) -> None:
        p = _P("alice")
        r_alice = _doc("r1", ["alice"])
        r_bob = _doc("r2", ["bob"])
        out = verify_acl([r_alice, r_bob], principal=p)
        assert len(out) == 1
        assert out[0]["chunk_id"] == "r1"

    def test_no_acl_allow_treated_as_public(self) -> None:
        """Documents without acl_allow should be visible to everyone."""
        p = _P("alice")
        results = [_doc("r1", None)]
        out = verify_acl(results, principal=p)
        assert len(out) == 1

    def test_empty_acl_allow_treated_as_public(self) -> None:
        p = _P("alice")
        results = [{"chunk_id": "r1", "acl_allow": []}]
        out = verify_acl(results, principal=p)
        assert len(out) == 1

    def test_group_membership_grants_access(self) -> None:
        p = _P("carol", groups=["team-alpha"])
        results = [_doc("r1", ["team-alpha"])]
        out = verify_acl(results, principal=p)
        assert len(out) == 1

    def test_group_non_membership_denied(self) -> None:
        p = _P("dave", groups=["team-beta"])
        results = [_doc("r1", ["team-alpha"])]
        out = verify_acl(results, principal=p)
        assert out == []

    def test_none_principal_bypasses_acl(self) -> None:
        """principal=None = internal system call; no filtering applied."""
        results = [_doc("r1", ["alice"]), _doc("r2", ["bob"])]
        out = verify_acl(results, principal=None)
        assert out == results

    def test_json_string_acl_allow_parsed(self) -> None:
        """Chroma stores acl_allow as a JSON-encoded string."""
        p = _P("alice")
        result: dict[str, Any] = {
            "chunk_id": "r1",
            "acl_allow": json.dumps(["alice", "bob"]),
        }
        out = verify_acl([result], principal=p)
        assert len(out) == 1

    def test_json_string_unauthorized(self) -> None:
        p = _P("carol")
        result: dict[str, Any] = {
            "chunk_id": "r1",
            "acl_allow": json.dumps(["alice"]),
        }
        out = verify_acl([result], principal=p)
        assert out == []

    def test_empty_results_returns_empty(self) -> None:
        p = _P("alice")
        assert verify_acl([], principal=p) == []

    def test_fail_open_on_bad_input(self) -> None:
        """verify_acl must not raise when data is malformed."""
        p = _P("alice")
        # Inject a pathological dict that might trip internal code.
        bad = {"chunk_id": None, "acl_allow": object()}  # type: ignore[dict-item]
        # Should not raise, may return [] or [bad] — just mustn't crash.
        try:
            verify_acl([bad], principal=p)  # type: ignore[list-item]
        except Exception as exc:
            pytest.fail(f"verify_acl raised unexpectedly: {exc}")

    def test_record_id_resolution_fallback(self) -> None:
        """Leaking result without chunk_id should still log gracefully."""
        p = _P("alice")
        result = {"id": "my-id", "acl_allow": ["bob"]}
        out = verify_acl([result], principal=p)
        assert out == []


class TestLeakCounter:
    """acl_leak_prevented counter must be incremented on filtered results.

    MetricsCollector is lazily imported inside _emit_leak_counter, so we
    patch it at the metrics.collector module level.
    """

    def test_counter_incremented_on_leak(self) -> None:
        p = _P("alice")
        leaked = [_doc("r1", ["bob"]), _doc("r2", ["charlie"])]

        with patch(
            "depthfusion.metrics.collector.MetricsCollector"
        ) as MockCollector:
            mock_instance = MagicMock()
            MockCollector.return_value = mock_instance

            verify_acl(leaked, principal=p)

            mock_instance.record.assert_called_once()
            call_args = mock_instance.record.call_args
            assert call_args[0][0] == "acl_leak_prevented"
            assert call_args[0][1] == 2.0  # two leaks

    def test_counter_not_called_when_no_leak(self) -> None:
        p = _P("alice")
        clean = [_doc("r1", ["alice"]), _doc("r2", None)]

        records: list[tuple] = []

        def _fake_record(name: str, value: float, labels: dict | None = None) -> None:
            records.append((name, value))

        with patch(
            "depthfusion.metrics.collector.MetricsCollector.record",
            side_effect=_fake_record,
        ):
            verify_acl(clean, principal=p)

        acl_records = [r for r in records if r[0] == "acl_leak_prevented"]
        assert acl_records == [], "No leak counter should fire when no results are removed"

    def test_counter_not_called_for_none_principal(self) -> None:
        """principal=None bypasses ACL; counter must not fire."""
        results = [_doc("r1", ["bob"])]
        records: list[tuple] = []

        def _fake_record(name: str, value: float, labels: dict | None = None) -> None:
            records.append((name, value))

        with patch(
            "depthfusion.metrics.collector.MetricsCollector.record",
            side_effect=_fake_record,
        ):
            verify_acl(results, principal=None)

        acl_records = [r for r in records if r[0] == "acl_leak_prevented"]
        assert acl_records == [], "No leak counter should fire for None principal"
