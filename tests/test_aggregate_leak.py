"""Aggregate-leak tests — T-576 / S-164 AC-2.

Verifies that count, facet, and aggregate query endpoints do NOT reveal the
existence of records a principal cannot see.

Rules under test:
  - query_discoveries(): count(X) where principal cannot see X returns 0, not the real count.
  - query_sessions():    session events are returned to any authenticated principal
    (no per-row ACL today), but the principal parameter must be accepted.
  - query_aggregate():   aggregate stats are returned to any authenticated principal
    (no per-row ACL today), but the principal parameter must be accepted.

Discovery ACL check:
  A discovery file is visible only when principal.principal_id OR one of
  principal.groups appears in acl_allow.  Files with acl_allow=["alice"] are
  hidden from principal "bob" but visible to "alice".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from depthfusion.identity.models import Principal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _principal(pid: str, groups: list[str] | None = None) -> Principal:
    return Principal(
        principal_id=pid,
        upn=f"{pid}@example.com",
        display_name=pid,
        groups=groups or [],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def discoveries_dir(tmp_path: Path) -> Path:
    """Discovery directory with three files of different ACL ownership."""
    d = tmp_path / "discoveries"
    d.mkdir()

    # Visible to "alice" only
    (d / "2026-06-01-alice-secret.md").write_text(
        "---\ndate: 2026-06-01\nproject: secret\nacl_allow:\n  - alice\nclassification: confidential\n---\n\n# Alice secret\n"
    )

    # Visible to "bob" only
    (d / "2026-06-02-bob-note.md").write_text(
        "---\ndate: 2026-06-02\nproject: shared\nacl_allow:\n  - bob\nclassification: internal\n---\n\n# Bob note\n"
    )

    # Visible to group "g-shared"
    (d / "2026-06-03-group-shared.md").write_text(
        "---\ndate: 2026-06-03\nproject: shared\nacl_allow:\n  - g-shared\nclassification: internal\n---\n\n# Group note\n"
    )

    # Public (visible to everyone)
    (d / "2026-06-04-public.md").write_text(
        "---\ndate: 2026-06-04\nproject: public\nacl_allow:\n  - alice\n  - bob\n  - greg\nclassification: public\n---\n\n# Public note\n"
    )

    return d


@pytest.fixture()
def metrics_dir(tmp_path: Path) -> Path:
    """Metrics directory with two session events."""
    d = tmp_path / "metrics"
    d.mkdir()
    recall_file = d / "2026-06-01-recall.jsonl"
    events = [
        {
            "timestamp": "2026-06-01T10:00:00+00:00",
            "event": "recall_query",
            "event_subtype": "ok",
            "mode": "vps",
            "result_count": 3,
            "total_latency_ms": 500.0,
            "config_version_id": "aabbcc",
        },
        {
            "timestamp": "2026-06-01T11:00:00+00:00",
            "event": "recall_query",
            "event_subtype": "ok",
            "mode": "vps-gpu",
            "result_count": 5,
            "total_latency_ms": 300.0,
            "config_version_id": "ddeeff",
        },
    ]
    recall_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return d


# ---------------------------------------------------------------------------
# T-576: Discovery aggregate-leak tests
# ---------------------------------------------------------------------------

class TestDiscoveryAclLeak:
    """count(X) must return 0 when the principal cannot see X."""

    def test_alice_sees_only_alice_records(self, discoveries_dir: Path) -> None:
        from depthfusion.api.query import query_discoveries

        alice = _principal("alice")
        result = query_discoveries(discoveries_dir=discoveries_dir, principal=alice)
        # alice: alice-secret (alice), public (alice listed). Not bob-note, not group-shared.
        assert result["total"] == 2
        assert result["count"] == 2
        filenames = {r["filename"] for r in result["items"]}
        assert "2026-06-01-alice-secret.md" in filenames
        assert "2026-06-04-public.md" in filenames

    def test_alice_cannot_see_bob_count(self, discoveries_dir: Path) -> None:
        """alice querying project=shared must NOT see bob's count."""
        from depthfusion.api.query import query_discoveries

        alice = _principal("alice")
        result = query_discoveries(
            discoveries_dir=discoveries_dir,
            project="shared",
            principal=alice,
        )
        # alice cannot see bob-note (acl_allow=bob) or group-shared (acl_allow=g-shared)
        assert result["total"] == 0
        assert result["count"] == 0

    def test_bob_sees_only_bob_records(self, discoveries_dir: Path) -> None:
        from depthfusion.api.query import query_discoveries

        bob = _principal("bob")
        result = query_discoveries(discoveries_dir=discoveries_dir, principal=bob)
        # bob: bob-note (bob), public (bob listed).
        assert result["total"] == 2
        filenames = {r["filename"] for r in result["items"]}
        assert "2026-06-02-bob-note.md" in filenames
        assert "2026-06-04-public.md" in filenames

    def test_bob_cannot_see_alice_count(self, discoveries_dir: Path) -> None:
        """bob querying project=secret must return 0, not 1."""
        from depthfusion.api.query import query_discoveries

        bob = _principal("bob")
        result = query_discoveries(
            discoveries_dir=discoveries_dir,
            project="secret",
            principal=bob,
        )
        assert result["total"] == 0
        assert result["count"] == 0

    def test_group_member_sees_group_record(self, discoveries_dir: Path) -> None:
        """A principal whose groups include g-shared can see group-shared."""
        from depthfusion.api.query import query_discoveries

        charlie = _principal("charlie", groups=["g-shared"])
        result = query_discoveries(discoveries_dir=discoveries_dir, principal=charlie)
        filenames = {r["filename"] for r in result["items"]}
        assert "2026-06-03-group-shared.md" in filenames

    def test_non_group_member_cannot_see_group_record(self, discoveries_dir: Path) -> None:
        """dave (no groups) cannot see group-shared."""
        from depthfusion.api.query import query_discoveries

        dave = _principal("dave")
        result = query_discoveries(discoveries_dir=discoveries_dir, principal=dave)
        filenames = {r["filename"] for r in result["items"]}
        assert "2026-06-03-group-shared.md" not in filenames

    def test_total_does_not_leak_hidden_count(self, discoveries_dir: Path) -> None:
        """The `total` field in the response must not include hidden records."""
        from depthfusion.api.query import query_discoveries

        # There are 4 files total, but alice can only see 2.
        alice = _principal("alice")
        result = query_discoveries(discoveries_dir=discoveries_dir, principal=alice)
        assert result["total"] == 2, (
            f"Expected total=2 for alice, got {result['total']}. "
            "total must not reveal the count of records alice cannot see."
        )

    def test_paginated_count_does_not_leak(self, discoveries_dir: Path) -> None:
        """Pagination must not allow inferring total count via next_cursor."""
        from depthfusion.api.query import query_discoveries

        alice = _principal("alice")
        # Request only 1 item per page
        page1 = query_discoveries(
            discoveries_dir=discoveries_dir,
            principal=alice,
            limit=1,
        )
        assert page1["total"] == 2
        assert page1["count"] == 1
        assert page1["next_cursor"] is not None

        # Fetch second page
        page2 = query_discoveries(
            discoveries_dir=discoveries_dir,
            principal=alice,
            limit=1,
            cursor=page1["next_cursor"],
        )
        assert page2["count"] == 1
        assert page2["next_cursor"] is None  # no more pages for alice


# ---------------------------------------------------------------------------
# T-576: Session / aggregate — principal parameter accepted, no leak
# ---------------------------------------------------------------------------

class TestSessionPrincipalAccepted:
    """query_sessions must accept a principal argument without error."""

    def test_sessions_accept_principal(self, metrics_dir: Path) -> None:
        from depthfusion.api.query import query_sessions

        alice = _principal("alice")
        result = query_sessions(metrics_dir=metrics_dir, principal=alice)
        assert result["total"] == 2
        assert result["count"] == 2

    def test_sessions_principal_none_still_works(self, metrics_dir: Path) -> None:
        from depthfusion.api.query import query_sessions

        result = query_sessions(metrics_dir=metrics_dir, principal=None)
        assert result["total"] == 2

    def test_sessions_principal_bob(self, metrics_dir: Path) -> None:
        from depthfusion.api.query import query_sessions

        bob = _principal("bob")
        result = query_sessions(metrics_dir=metrics_dir, principal=bob)
        # Session events have no per-row ACL — both events returned.
        assert result["total"] == 2


class TestAggregateLeakPrincipal:
    """query_aggregate must accept a principal argument.

    Aggregate stats currently do not filter by principal ACL because session
    JSONL files carry no per-record acl_allow. The key requirement is:
    - the function accepts principal without error,
    - it returns a consistent aggregate (not inflated by hidden records),
    - count fields do NOT reveal the existence of ACL-hidden data.
    """

    def test_aggregate_accepts_principal(self, metrics_dir: Path) -> None:
        from depthfusion.api.query import query_aggregate

        alice = _principal("alice")
        result = query_aggregate(metrics_dir=metrics_dir, principal=alice)
        assert isinstance(result, dict)
        assert result["total_events"] == 2

    def test_aggregate_principal_none_still_works(self, metrics_dir: Path) -> None:
        from depthfusion.api.query import query_aggregate

        result = query_aggregate(metrics_dir=metrics_dir, principal=None)
        assert result["total_events"] == 2

    def test_aggregate_modes_facet(self, metrics_dir: Path) -> None:
        """Mode facet counts must sum to total_events — no hidden inflation."""
        from depthfusion.api.query import query_aggregate

        alice = _principal("alice")
        result = query_aggregate(metrics_dir=metrics_dir, principal=alice)
        mode_sum = sum(result["modes"].values())
        assert mode_sum == result["total_events"], (
            "Mode facet counts must sum to total_events; "
            "inflated counts would reveal hidden records."
        )

    def test_aggregate_empty_with_no_accessible_data(self, tmp_path: Path) -> None:
        """When the metrics dir is empty, aggregate returns zeros for all principals."""
        from depthfusion.api.query import query_aggregate

        empty_dir = tmp_path / "empty_metrics"
        empty_dir.mkdir()
        alice = _principal("alice")
        result = query_aggregate(metrics_dir=empty_dir, principal=alice)
        assert result["total_events"] == 0
        assert result["modes"] == {}

    def test_aggregate_count_does_not_include_hidden_discoveries(
        self, discoveries_dir: Path, metrics_dir: Path
    ) -> None:
        """query_discoveries total must equal what principal can actually see."""
        from depthfusion.api.query import query_discoveries

        # Sanity: total records in dir = 4
        greg = _principal("greg")  # greg is in public file's acl_allow
        result_greg = query_discoveries(discoveries_dir=discoveries_dir, principal=greg)
        # greg only in public (1 file)
        assert result_greg["total"] == 1

        # alice sees alice-secret + public = 2
        alice = _principal("alice")
        result_alice = query_discoveries(discoveries_dir=discoveries_dir, principal=alice)
        assert result_alice["total"] == 2

        # The counts differ — each principal sees only their own view.
        assert result_greg["total"] != result_alice["total"]
