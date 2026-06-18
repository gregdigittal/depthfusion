"""Tests for E-59: Export Controls & IP Protection.

Verifies:
- Restricted/confidential records cannot be exported without approval
- Public/internal records follow their format allowlists
- Watermark policy is correctly propagated
- Custom policy matrix works correctly
"""
from __future__ import annotations

import pytest

from depthfusion.authz.export_controls import (
    CONFIDENTIAL_FOOTER_THRESHOLD,
    ClassificationLevel,
    ExportDecision,
    ExportFormat,
    ExportPolicy,
    ExportPolicyMatrix,
    WatermarkPolicy,
    apply_provenance_footer,
    build_provenance_footer,
    check_export_allowed,
    classification_rank,
    get_watermark_policy,
)

# ---------------------------------------------------------------------------
# Classification: RESTRICTED
# ---------------------------------------------------------------------------

class TestRestrictedExports:
    """Restricted records require approval for ANY export."""

    def test_restricted_denied_without_approval(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.JSON,
        )
        assert decision.allowed is False
        assert decision.approval_required is True

    def test_restricted_denied_all_formats_without_approval(self) -> None:
        for fmt in ExportFormat:
            decision = check_export_allowed(ClassificationLevel.RESTRICTED, fmt)
            assert decision.allowed is False, f"format {fmt} should be denied without approval"

    def test_restricted_allowed_with_approval_token(self) -> None:
        """An approval token unlocks restricted exports (token validation is T-662)."""
        # The default matrix has allowed_export_formats=[] for RESTRICTED,
        # so even with a token, no format is allowed until T-662 extends the policy.
        # The enforcement here tests the approval_required gate only.
        matrix = ExportPolicyMatrix()
        # Temporarily allow JSON for restricted to test approval path
        matrix.policies[ClassificationLevel.RESTRICTED] = ExportPolicy(
            allowed_export_formats=[ExportFormat.JSON],
            watermark_required=True,
            approval_required=True,
        )
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.JSON,
            matrix=matrix,
            approval_token="signed-approval-abc123",
        )
        assert decision.allowed is True
        assert decision.watermark_required is True

    def test_restricted_denied_empty_approval_token(self) -> None:
        """Empty string is not a valid approval token."""
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.JSON,
            approval_token="",
        )
        assert decision.allowed is False
        assert decision.approval_required is True

    def test_restricted_denied_reason_mentions_approval(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.CSV,
        )
        assert "approval" in decision.reason.lower()

    def test_restricted_watermark_required(self) -> None:
        """Watermark flag must be set even on denied restricted exports."""
        decision = check_export_allowed(
            ClassificationLevel.RESTRICTED,
            ExportFormat.JSON,
        )
        assert decision.watermark_required is True


# ---------------------------------------------------------------------------
# Classification: CONFIDENTIAL
# ---------------------------------------------------------------------------

class TestConfidentialExports:
    """Confidential records: limited formats, watermark required, no approval needed."""

    def test_confidential_json_allowed(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.JSON,
        )
        assert decision.allowed is True
        assert decision.watermark_required is True

    def test_confidential_csv_allowed(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.CSV,
        )
        assert decision.allowed is True

    def test_confidential_pdf_denied(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.PDF,
        )
        assert decision.allowed is False
        assert "confidential" in decision.reason.lower()

    def test_confidential_raw_denied(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.RAW,
        )
        assert decision.allowed is False

    def test_confidential_markdown_denied(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.MARKDOWN,
        )
        assert decision.allowed is False

    def test_confidential_no_approval_required(self) -> None:
        decision = check_export_allowed(
            ClassificationLevel.CONFIDENTIAL,
            ExportFormat.JSON,
        )
        assert decision.approval_required is False


# ---------------------------------------------------------------------------
# Classification: INTERNAL
# ---------------------------------------------------------------------------

class TestInternalExports:
    """Internal records: broader format access, no watermark, no approval."""

    @pytest.mark.parametrize("fmt", [
        ExportFormat.JSON,
        ExportFormat.CSV,
        ExportFormat.MARKDOWN,
    ])
    def test_internal_allowed_formats(self, fmt: ExportFormat) -> None:
        decision = check_export_allowed(ClassificationLevel.INTERNAL, fmt)
        assert decision.allowed is True
        assert decision.watermark_required is False

    @pytest.mark.parametrize("fmt", [ExportFormat.PDF, ExportFormat.RAW])
    def test_internal_denied_formats(self, fmt: ExportFormat) -> None:
        decision = check_export_allowed(ClassificationLevel.INTERNAL, fmt)
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# Classification: PUBLIC
# ---------------------------------------------------------------------------

class TestPublicExports:
    """Public records: all formats allowed, no restrictions."""

    @pytest.mark.parametrize("fmt", list(ExportFormat))
    def test_public_all_formats_allowed(self, fmt: ExportFormat) -> None:
        decision = check_export_allowed(ClassificationLevel.PUBLIC, fmt)
        assert decision.allowed is True
        assert decision.watermark_required is False
        assert decision.approval_required is False


# ---------------------------------------------------------------------------
# Custom policy matrix
# ---------------------------------------------------------------------------

class TestCustomPolicyMatrix:
    """ExportPolicyMatrix can be customised; custom policies are enforced."""

    def test_custom_matrix_overrides_internal(self) -> None:
        matrix = ExportPolicyMatrix()
        # Lock down internal to JSON-only + watermark
        matrix.policies[ClassificationLevel.INTERNAL] = ExportPolicy(
            allowed_export_formats=[ExportFormat.JSON],
            watermark_required=True,
            approval_required=False,
        )
        assert check_export_allowed(
            ClassificationLevel.INTERNAL, ExportFormat.JSON, matrix=matrix
        ).allowed is True
        assert check_export_allowed(
            ClassificationLevel.INTERNAL, ExportFormat.CSV, matrix=matrix
        ).allowed is False

    def test_missing_level_returns_most_restrictive(self) -> None:
        """A matrix missing a level returns deny-all (safe default)."""
        matrix = ExportPolicyMatrix(policies={})
        decision = check_export_allowed(
            ClassificationLevel.PUBLIC, ExportFormat.JSON, matrix=matrix
        )
        assert decision.allowed is False

    def test_version_field_preserved(self) -> None:
        matrix = ExportPolicyMatrix(version=42)
        assert matrix.version == 42


# ---------------------------------------------------------------------------
# ExportDecision model
# ---------------------------------------------------------------------------

class TestExportDecision:
    def test_decision_allowed_fields(self) -> None:
        d = ExportDecision(allowed=True, reason="ok")
        assert d.allowed is True
        assert d.watermark_required is False
        assert d.approval_required is False

    def test_decision_denied_fields(self) -> None:
        d = ExportDecision(
            allowed=False,
            reason="denied",
            watermark_required=True,
            approval_required=True,
        )
        assert d.allowed is False
        assert d.watermark_required is True
        assert d.approval_required is True


# ===========================================================================
# S-192 — Provenance footer + watermark policy hooks (T-665)
# ===========================================================================

from datetime import datetime, timezone  # noqa: E402

_BODY = "the secret quarterly numbers"
_PRINCIPAL = "alice"
_FIXED_TS = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


class TestProvenanceFooterThreshold:
    """Footer appended at/above CONFIDENTIAL when copy is allowed; omitted below."""

    def test_confidential_gets_footer(self) -> None:
        out = apply_provenance_footer(
            _BODY,
            _PRINCIPAL,
            ClassificationLevel.CONFIDENTIAL,
            copy_allowed=True,
            timestamp=_FIXED_TS,
        )
        assert out.startswith(_BODY)
        assert out != _BODY
        assert _PRINCIPAL in out
        assert "confidential" in out.lower()

    def test_restricted_gets_footer(self) -> None:
        """Above the threshold (RESTRICTED) also gets a footer."""
        out = apply_provenance_footer(
            _BODY,
            _PRINCIPAL,
            ClassificationLevel.RESTRICTED,
            copy_allowed=True,
            timestamp=_FIXED_TS,
        )
        assert out != _BODY
        assert _PRINCIPAL in out

    def test_public_no_footer(self) -> None:
        out = apply_provenance_footer(
            _BODY,
            _PRINCIPAL,
            ClassificationLevel.PUBLIC,
            copy_allowed=True,
            timestamp=_FIXED_TS,
        )
        assert out == _BODY

    def test_internal_no_footer_boundary(self) -> None:
        """INTERNAL is the level immediately below the threshold — no footer."""
        out = apply_provenance_footer(
            _BODY,
            _PRINCIPAL,
            ClassificationLevel.INTERNAL,
            copy_allowed=True,
            timestamp=_FIXED_TS,
        )
        assert out == _BODY

    def test_no_footer_when_copy_not_allowed(self) -> None:
        """Even at CONFIDENTIAL, if copy is disallowed nothing is appended."""
        out = apply_provenance_footer(
            _BODY,
            _PRINCIPAL,
            ClassificationLevel.CONFIDENTIAL,
            copy_allowed=False,
            timestamp=_FIXED_TS,
        )
        assert out == _BODY

    def test_footer_is_per_principal(self) -> None:
        alice_out = apply_provenance_footer(
            _BODY, "alice", ClassificationLevel.CONFIDENTIAL,
            copy_allowed=True, timestamp=_FIXED_TS,
        )
        bob_out = apply_provenance_footer(
            _BODY, "bob", ClassificationLevel.CONFIDENTIAL,
            copy_allowed=True, timestamp=_FIXED_TS,
        )
        assert "alice" in alice_out
        assert "bob" in bob_out
        assert alice_out != bob_out

    def test_footer_includes_timestamp(self) -> None:
        out = apply_provenance_footer(
            _BODY, _PRINCIPAL, ClassificationLevel.CONFIDENTIAL,
            copy_allowed=True, timestamp=_FIXED_TS,
        )
        assert _FIXED_TS.isoformat() in out

    def test_build_footer_nonempty(self) -> None:
        footer = build_provenance_footer(
            _PRINCIPAL, ClassificationLevel.CONFIDENTIAL, timestamp=_FIXED_TS
        )
        assert footer.strip()
        assert _PRINCIPAL in footer

    def test_threshold_constant_is_confidential(self) -> None:
        assert CONFIDENTIAL_FOOTER_THRESHOLD is ClassificationLevel.CONFIDENTIAL


class TestClassificationRank:
    def test_ladder_is_ascending(self) -> None:
        assert classification_rank(ClassificationLevel.PUBLIC) < classification_rank(
            ClassificationLevel.INTERNAL
        )
        assert classification_rank(ClassificationLevel.INTERNAL) < classification_rank(
            ClassificationLevel.CONFIDENTIAL
        )
        assert classification_rank(
            ClassificationLevel.CONFIDENTIAL
        ) < classification_rank(ClassificationLevel.RESTRICTED)


class TestWatermarkPolicyHook:
    """get_watermark_policy returns the active policy for a classification/principal."""

    def test_confidential_watermark_enabled(self) -> None:
        wp = get_watermark_policy(ClassificationLevel.CONFIDENTIAL, "alice")
        assert isinstance(wp, WatermarkPolicy)
        assert wp.enabled is True
        assert wp.principal_id == "alice"
        assert wp.classification is ClassificationLevel.CONFIDENTIAL
        assert "alice" in wp.label

    def test_public_watermark_disabled(self) -> None:
        wp = get_watermark_policy(ClassificationLevel.PUBLIC, "bob")
        assert wp.enabled is False

    def test_internal_watermark_disabled(self) -> None:
        wp = get_watermark_policy(ClassificationLevel.INTERNAL, "carol")
        assert wp.enabled is False

    def test_restricted_watermark_enabled(self) -> None:
        wp = get_watermark_policy(ClassificationLevel.RESTRICTED, "dave")
        assert wp.enabled is True

    def test_hook_honours_custom_matrix(self) -> None:
        matrix = ExportPolicyMatrix()
        matrix.policies[ClassificationLevel.INTERNAL] = ExportPolicy(
            allowed_export_formats=[ExportFormat.JSON],
            watermark_required=True,
            approval_required=False,
        )
        wp = get_watermark_policy(
            ClassificationLevel.INTERNAL, "eve", matrix=matrix
        )
        assert wp.enabled is True

    def test_label_is_per_principal(self) -> None:
        a = get_watermark_policy(ClassificationLevel.CONFIDENTIAL, "alice").label
        b = get_watermark_policy(ClassificationLevel.CONFIDENTIAL, "bob").label
        assert a != b


# ===========================================================================
# S-193 — Export auditing, rate-limit backstop & anomaly heuristics
# (T-667 + T-668)
# ===========================================================================

from pathlib import Path  # noqa: E402

from depthfusion.audit.log import AuditEventType, AuditStore  # noqa: E402
from depthfusion.authz.export_audit import (  # noqa: E402
    AnomalyAlert,
    AnomalyKind,
    ExportAnomalyDetector,
    ExportRateLimiter,
    audit_export_action,
)


@pytest.fixture()
def audit_store(tmp_path: Path) -> AuditStore:
    return AuditStore(db_path=tmp_path / "audit.db")


# ---------------------------------------------------------------------------
# T-667 AC-1: every export-class action (allow + deny) is audited with
# principal, record, action, decision and device.
# ---------------------------------------------------------------------------

class TestExportActionAuditing:
    def test_allowed_export_emits_audit_event(self, audit_store: AuditStore) -> None:
        result = audit_export_action(
            store=audit_store,
            principal_id="alice",
            resource_id="rec-1",
            classification=ClassificationLevel.PUBLIC,
            export_format=ExportFormat.JSON,
            device_id="dev-7",
            project_id="proj-a",
        )
        assert result.allowed is True

        events = audit_store.query(actor="alice")
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == AuditEventType.EXPORT_ALLOWED.value
        assert ev["actor_principal_id"] == "alice"   # principal
        assert ev["resource_id"] == "rec-1"          # record
        assert ev["device_id"] == "dev-7"            # device
        assert ev["success"] is True                 # decision = allow

    def test_denied_export_emits_audit_event(self, audit_store: AuditStore) -> None:
        # RESTRICTED with no approval token -> policy denial.
        result = audit_export_action(
            store=audit_store,
            principal_id="bob",
            resource_id="secret-9",
            classification=ClassificationLevel.RESTRICTED,
            export_format=ExportFormat.JSON,
            device_id="dev-3",
        )
        assert result.allowed is False
        assert result.rate_limited is False

        events = audit_store.query(actor="bob")
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == AuditEventType.EXPORT_DENIED.value
        assert ev["resource_id"] == "secret-9"
        assert ev["device_id"] == "dev-3"
        assert ev["success"] is False                # decision = deny

    def test_format_denial_is_audited(self, audit_store: AuditStore) -> None:
        # INTERNAL does not allow RAW export.
        result = audit_export_action(
            store=audit_store,
            principal_id="carol",
            resource_id="rec-2",
            classification=ClassificationLevel.INTERNAL,
            export_format=ExportFormat.RAW,
            device_id="dev-1",
        )
        assert result.allowed is False
        events = audit_store.query(actor="carol", event_type=AuditEventType.EXPORT_DENIED)
        assert len(events) == 1


# ---------------------------------------------------------------------------
# T-667 AC-2: per-principal export rate limit rejects bursts above threshold.
# ---------------------------------------------------------------------------

class TestExportRateLimiter:
    def test_under_limit_allows(self) -> None:
        rl = ExportRateLimiter(max_exports=3, window_seconds=3600)
        now = 1000.0
        assert rl.acquire("p", now=now) is True
        assert rl.acquire("p", now=now) is True
        assert rl.acquire("p", now=now) is True

    def test_burst_above_threshold_rejected(self) -> None:
        rl = ExportRateLimiter(max_exports=3, window_seconds=3600)
        now = 1000.0
        for _ in range(3):
            assert rl.acquire("p", now=now) is True
        # 4th within window is the backstop rejection.
        assert rl.acquire("p", now=now) is False
        assert rl.current_count("p", now=now) == 3

    def test_window_slides(self) -> None:
        rl = ExportRateLimiter(max_exports=2, window_seconds=100)
        assert rl.acquire("p", now=0.0) is True
        assert rl.acquire("p", now=10.0) is True
        assert rl.acquire("p", now=20.0) is False   # at limit
        # advance past window of the first two hits
        assert rl.acquire("p", now=200.0) is True

    def test_limiter_is_per_principal(self) -> None:
        rl = ExportRateLimiter(max_exports=1, window_seconds=3600)
        assert rl.acquire("alice", now=0.0) is True
        assert rl.acquire("alice", now=0.0) is False
        # bob has an independent budget
        assert rl.acquire("bob", now=0.0) is True

    def test_invalid_config_rejected(self) -> None:
        with pytest.raises(ValueError):
            ExportRateLimiter(max_exports=0)
        with pytest.raises(ValueError):
            ExportRateLimiter(window_seconds=0)

    def test_backstop_in_orchestration_emits_rate_limited_event(
        self, audit_store: AuditStore
    ) -> None:
        rl = ExportRateLimiter(max_exports=2, window_seconds=3600)
        alerts: list[AnomalyAlert] = []
        det = ExportAnomalyDetector(audit_store, alert_channel=alerts.append)
        now = 5000.0

        results = [
            audit_export_action(
                store=audit_store,
                principal_id="dave",
                resource_id=f"rec-{i}",
                classification=ClassificationLevel.PUBLIC,
                export_format=ExportFormat.JSON,
                rate_limiter=rl,
                anomaly_detector=det,
                now=now,
            )
            for i in range(3)
        ]
        # First two allowed, third backstopped.
        assert [r.allowed for r in results] == [True, True, False]
        assert results[2].rate_limited is True

        rl_events = audit_store.query(
            actor="dave", event_type=AuditEventType.EXPORT_RATE_LIMITED
        )
        assert len(rl_events) == 1
        # A rate-limit anomaly alert was raised to the admin channel.
        assert any(a.kind is AnomalyKind.RATE_LIMIT_EXCEEDED for a in alerts)


# ---------------------------------------------------------------------------
# T-668: anomaly heuristics (burst + cross-project sweep) -> admin alert.
# ---------------------------------------------------------------------------

class TestAnomalyHeuristics:
    def test_burst_raises_admin_alert(self, audit_store: AuditStore) -> None:
        alerts: list[AnomalyAlert] = []
        det = ExportAnomalyDetector(
            audit_store,
            alert_channel=alerts.append,
            burst_threshold=5,
            project_sweep_threshold=999,   # disable sweep for this test
            window_seconds=3600,
        )
        now = 10_000.0
        # 6 allowed exports in-window -> exceeds burst_threshold of 5.
        for i in range(6):
            audit_export_action(
                store=audit_store,
                principal_id="mallory",
                resource_id=f"rec-{i}",
                classification=ClassificationLevel.PUBLIC,
                export_format=ExportFormat.JSON,
                project_id="proj-x",
                anomaly_detector=det,
                now=now,
            )
        burst_alerts = [a for a in alerts if a.kind is AnomalyKind.BURST]
        assert burst_alerts, "expected a burst anomaly alert"
        assert burst_alerts[-1].principal_id == "mallory"
        assert burst_alerts[-1].count >= 6

        # Anomaly was also persisted to the audit log.
        anomaly_events = audit_store.query(
            actor="mallory", event_type=AuditEventType.ANOMALY_DETECTED
        )
        assert anomaly_events

    def test_cross_project_sweep_raises_admin_alert(
        self, audit_store: AuditStore
    ) -> None:
        alerts: list[AnomalyAlert] = []
        det = ExportAnomalyDetector(
            audit_store,
            alert_channel=alerts.append,
            burst_threshold=999,           # disable burst for this test
            project_sweep_threshold=3,
            window_seconds=3600,
        )
        now = 20_000.0
        for proj in ("p1", "p2", "p3"):
            audit_export_action(
                store=audit_store,
                principal_id="eve",
                resource_id=f"rec-{proj}",
                classification=ClassificationLevel.PUBLIC,
                export_format=ExportFormat.JSON,
                project_id=proj,
                anomaly_detector=det,
                now=now,
            )
        sweep = [a for a in alerts if a.kind is AnomalyKind.CROSS_PROJECT_SWEEP]
        assert sweep, "expected a cross-project-sweep alert"
        assert sweep[-1].count == 3
        assert set(sweep[-1].projects) == {"p1", "p2", "p3"}

    def test_no_alert_under_thresholds(self, audit_store: AuditStore) -> None:
        alerts: list[AnomalyAlert] = []
        det = ExportAnomalyDetector(
            audit_store,
            alert_channel=alerts.append,
            burst_threshold=10,
            project_sweep_threshold=10,
            window_seconds=3600,
        )
        now = 30_000.0
        for i in range(3):
            audit_export_action(
                store=audit_store,
                principal_id="frank",
                resource_id=f"rec-{i}",
                classification=ClassificationLevel.PUBLIC,
                export_format=ExportFormat.JSON,
                project_id="solo",
                anomaly_detector=det,
                now=now,
            )
        assert alerts == []

    def test_old_exports_outside_window_excluded(
        self, audit_store: AuditStore
    ) -> None:
        alerts: list[AnomalyAlert] = []
        det = ExportAnomalyDetector(
            audit_store,
            alert_channel=alerts.append,
            burst_threshold=2,
            project_sweep_threshold=999,
            window_seconds=100,
        )
        # Two exports long ago (outside window) + one recent: count in-window = 1.
        for ts in (0.0, 10.0):
            audit_export_action(
                store=audit_store,
                principal_id="grace",
                resource_id="old",
                classification=ClassificationLevel.PUBLIC,
                export_format=ExportFormat.JSON,
                anomaly_detector=det,
                now=ts,
            )
        audit_export_action(
            store=audit_store,
            principal_id="grace",
            resource_id="new",
            classification=ClassificationLevel.PUBLIC,
            export_format=ExportFormat.JSON,
            anomaly_detector=det,
            now=1000.0,
        )
        assert not any(a.kind is AnomalyKind.BURST for a in alerts)
