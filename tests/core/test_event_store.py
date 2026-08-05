"""Tests for S-250 checkpointing: CheckpointRecord, TTL, and ambient traces.

Covers:
- AC-1: CheckpointRecord literal fields (plan_state, files_modified,
  git_stash_ref, context_pct_at_checkpoint) and JSON round-trip.
- AC-2: publish_ambient_trace() writes event_type="ambient_trace".
- AC-3: get_checkpoint / get_latest_checkpoint lookup semantics.
- AC-5: checkpoint_ttl_days() env resolution and prune_expired_checkpoints()
  TTL-based deletion.

TestRealCollaborators (bottom of file) is the mandated regression guard:
every other test in this file uses tmp_path + a real JSONGraphStore, no
MagicMock — the whole file is "real collaborators" by construction, but the
class name follows the tests/core/test_hygiene_scheduler.py:333 convention
explicitly for the checkpoint publish -> graph write -> file write ->
read-back round trip, which is the piece most likely to be hidden by a
mocked graph backend or a mocked filesystem.
"""
from __future__ import annotations

import json

import pytest

from depthfusion.core.event_store import (
    CheckpointRecord,
    EventStore,
    InMemoryStreamBackend,
    checkpoint_store_dir,
    checkpoint_ttl_days,
    prune_expired_checkpoints,
)
from depthfusion.graph.store import JSONGraphStore

# ---------------------------------------------------------------------------
# CheckpointRecord — AC-1 literal fields
# ---------------------------------------------------------------------------


class TestCheckpointRecordFields:
    def test_has_exactly_the_ac1_literal_fields(self):
        """AC-1: plan_state, files_modified, git_stash_ref,
        context_pct_at_checkpoint must exist (plus identity fields)."""
        record = CheckpointRecord(
            checkpoint_id="cp1",
            session_id="sess1",
            project_slug="depthfusion",
            created_at="2026-08-05T00:00:00+00:00",
            plan_state="implementing S-250",
            files_modified=["src/a.py", "src/b.py"],
            git_stash_ref="stash@{0}",
            context_pct_at_checkpoint=42.5,
        )
        assert record.plan_state == "implementing S-250"
        assert record.files_modified == ["src/a.py", "src/b.py"]
        assert record.git_stash_ref == "stash@{0}"
        assert record.context_pct_at_checkpoint == 42.5

    def test_git_stash_ref_and_context_pct_default_none(self):
        record = CheckpointRecord(
            checkpoint_id="cp2",
            session_id="sess2",
            project_slug="depthfusion",
            created_at="2026-08-05T00:00:00+00:00",
            plan_state="",
        )
        assert record.git_stash_ref is None
        assert record.context_pct_at_checkpoint is None
        assert record.files_modified == []

    def test_to_dict_round_trips_through_from_dict(self):
        record = CheckpointRecord(
            checkpoint_id="cp3",
            session_id="sess3",
            project_slug="depthfusion",
            created_at="2026-08-05T00:00:00+00:00",
            plan_state="halfway through T-847",
            files_modified=["core/event_store.py"],
            git_stash_ref=None,
            context_pct_at_checkpoint=71.0,
        )
        rebuilt = CheckpointRecord.from_dict(json.loads(json.dumps(record.to_dict())))
        assert rebuilt == record

    def test_from_dict_defaults_missing_optional_fields(self):
        """A legacy/partial dict (missing optional keys) must not raise."""
        rebuilt = CheckpointRecord.from_dict({
            "checkpoint_id": "cp4",
            "plan_state": "x",
        })
        assert rebuilt.checkpoint_id == "cp4"
        assert rebuilt.session_id == ""
        assert rebuilt.files_modified == []
        assert rebuilt.git_stash_ref is None
        assert rebuilt.context_pct_at_checkpoint is None


# ---------------------------------------------------------------------------
# checkpoint_ttl_days() — AC-5
# ---------------------------------------------------------------------------


class TestCheckpointTtlDays:
    def test_defaults_to_seven(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_CHECKPOINT_TTL_DAYS", raising=False)
        assert checkpoint_ttl_days() == 7

    def test_reads_override(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_TTL_DAYS", "14")
        assert checkpoint_ttl_days() == 14

    def test_invalid_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_TTL_DAYS", "not-a-number")
        assert checkpoint_ttl_days() == 7

    def test_non_positive_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_TTL_DAYS", "0")
        assert checkpoint_ttl_days() == 7
        monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_TTL_DAYS", "-3")
        assert checkpoint_ttl_days() == 7

    def test_empty_string_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_TTL_DAYS", "")
        assert checkpoint_ttl_days() == 7


# ---------------------------------------------------------------------------
# prune_expired_checkpoints() — AC-5, filesystem-only (no EventStore needed)
# ---------------------------------------------------------------------------


class TestPruneExpiredCheckpoints:
    def test_missing_directory_returns_zero_and_creates_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
        assert prune_expired_checkpoints("no-such-project") == 0
        assert not (tmp_path / "checkpoints").exists()

    def test_deletes_only_expired_records(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta, timezone

        monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(tmp_path))
        proj_dir = tmp_path / "myproj"
        proj_dir.mkdir()

        now = datetime.now(timezone.utc)
        old = CheckpointRecord(
            checkpoint_id="old",
            session_id="s1",
            project_slug="myproj",
            created_at=(now - timedelta(days=10)).isoformat(),
            plan_state="stale",
        )
        fresh = CheckpointRecord(
            checkpoint_id="fresh",
            session_id="s2",
            project_slug="myproj",
            created_at=(now - timedelta(days=1)).isoformat(),
            plan_state="recent",
        )
        (proj_dir / "old.json").write_text(json.dumps(old.to_dict()))
        (proj_dir / "fresh.json").write_text(json.dumps(fresh.to_dict()))

        deleted = prune_expired_checkpoints("myproj", ttl_days=7)

        assert deleted == 1
        assert not (proj_dir / "old.json").exists()
        assert (proj_dir / "fresh.json").exists()

    def test_malformed_file_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(tmp_path))
        proj_dir = tmp_path / "myproj"
        proj_dir.mkdir()
        (proj_dir / "broken.json").write_text("{not valid json")

        # Must not raise.
        deleted = prune_expired_checkpoints("myproj", ttl_days=7)
        assert deleted == 0
        assert (proj_dir / "broken.json").exists()


# ---------------------------------------------------------------------------
# TestRealCollaborators — mandated regression guard (see module docstring)
#
# Constructs a REAL JSONGraphStore against tmp_path and a REAL EventStore —
# no MagicMock anywhere in this class. The checkpoint publish path chains
# three things a mock would let silently diverge from reality: (1) the
# CheckpointRecord must actually JSON-serialise (dataclass -> dict -> json),
# (2) EventStore._graph_write's real flock + upsert_entity + ACL-validation
# path must accept the checkpoint's mirrored Entity, and (3) the file that
# publish_checkpoint() writes must be byte-identical to what get_checkpoint()
# reads back. A MagicMock graph backend answers to any attribute/signature
# and would hide a real ACL or confidence-threshold rejection (see
# graph/store.py: _validate_graph_acl / _min_confidence) exactly the way the
# hygiene TestRealCollaborators class describes for FileBus/DecaySummary.
# ---------------------------------------------------------------------------


@pytest.fixture
def real_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("DEPTHFUSION_LOCK_DIR", str(tmp_path / "locks"))
    graph = JSONGraphStore(path=tmp_path / "graph.json")
    stream = InMemoryStreamBackend()
    store = EventStore(graph=graph, stream=stream)
    return store, graph


class TestRealCollaborators:
    """Regression guard: exercise the real JSONGraphStore + real filesystem.

    If either publish_checkpoint()'s file write or its graph mirror silently
    breaks (wrong attribute name, non-serialisable field, ACL/confidence
    rejection, wrong directory), these tests fail — a MagicMock-backed test
    would not catch any of the three.
    """

    @pytest.mark.asyncio
    async def test_publish_checkpoint_round_trips_via_real_filesystem(self, real_store):
        store, _graph = real_store

        record = await store.publish_checkpoint(
            session_id="sess-real-1",
            project_slug="depthfusion",
            plan_state="implementing S-250 checkpoint schema",
            files_modified=["src/depthfusion/core/event_store.py"],
            git_stash_ref="stash@{0}",
            context_pct_at_checkpoint=63.0,
        )

        fetched = store.get_checkpoint(record.checkpoint_id, "depthfusion")
        assert fetched is not None
        assert fetched.plan_state == "implementing S-250 checkpoint schema"
        assert fetched.files_modified == ["src/depthfusion/core/event_store.py"]
        assert fetched.git_stash_ref == "stash@{0}"
        assert fetched.context_pct_at_checkpoint == 63.0

    @pytest.mark.asyncio
    async def test_publish_checkpoint_mirrors_a_real_event_entity_in_the_graph(self, real_store):
        store, graph = real_store

        record = await store.publish_checkpoint(
            session_id="sess-real-2",
            project_slug="depthfusion",
            plan_state="graph mirror check",
            files_modified=[],
        )

        entities = graph.all_entities()
        checkpoint_entities = [
            e for e in entities
            if e.type == "event" and e.metadata.get("event_type") == "checkpoint"
        ]
        assert len(checkpoint_entities) == 1
        assert checkpoint_entities[0].metadata.get("session_id") == "sess-real-2"
        assert record.session_id == "sess-real-2"

    @pytest.mark.asyncio
    async def test_get_latest_checkpoint_returns_most_recent_of_several(self, real_store):
        store, _graph = real_store

        await store.publish_checkpoint(
            session_id="s1", project_slug="depthfusion",
            plan_state="first", files_modified=[],
        )
        await store.publish_checkpoint(
            session_id="s2", project_slug="depthfusion",
            plan_state="second", files_modified=[],
        )
        latest = await store.publish_checkpoint(
            session_id="s3", project_slug="depthfusion",
            plan_state="third (latest)", files_modified=[],
        )

        found = store.get_latest_checkpoint("depthfusion")
        assert found is not None
        assert found.checkpoint_id == latest.checkpoint_id
        assert found.plan_state == "third (latest)"

    @pytest.mark.asyncio
    async def test_get_latest_checkpoint_session_scoped(self, real_store):
        store, _graph = real_store

        await store.publish_checkpoint(
            session_id="target-session", project_slug="depthfusion",
            plan_state="target", files_modified=[],
        )
        await store.publish_checkpoint(
            session_id="other-session", project_slug="depthfusion",
            plan_state="other (published after target)", files_modified=[],
        )

        found = store.get_latest_checkpoint("depthfusion", session_id="target-session")
        assert found is not None
        assert found.session_id == "target-session"
        assert found.plan_state == "target"

    def test_get_checkpoint_returns_none_when_absent(self, real_store):
        store, _graph = real_store
        assert store.get_checkpoint("does-not-exist", "depthfusion") is None

    def test_get_latest_checkpoint_returns_none_when_project_has_none(self, real_store):
        store, _graph = real_store
        assert store.get_latest_checkpoint("never-published-project") is None

    @pytest.mark.asyncio
    async def test_publish_checkpoint_survives_graph_mirror_failure(self, real_store, monkeypatch):
        """A broken graph mirror must not lose the checkpoint file (best-effort mirror)."""
        store, _graph = real_store

        async def _boom(*args, **kwargs):
            raise RuntimeError("graph backend unavailable")

        monkeypatch.setattr(store, "publish", _boom)

        record = await store.publish_checkpoint(
            session_id="sess-resilient", project_slug="depthfusion",
            plan_state="mirror should fail, file must still land",
            files_modified=[],
        )

        fetched = store.get_checkpoint(record.checkpoint_id, "depthfusion")
        assert fetched is not None
        assert fetched.plan_state == "mirror should fail, file must still land"

    @pytest.mark.asyncio
    async def test_publish_ambient_trace_writes_real_event_entity_with_correct_type(
        self, real_store,
    ):
        """AC-2 publish side: ambient traces land as event_type='ambient_trace'."""
        store, graph = real_store

        event_id = await store.publish_ambient_trace(
            agent_id="agent-x", project_slug="depthfusion", session_id="sess-amb",
        )

        entity = graph.get_entity(event_id)
        assert entity is not None
        assert entity.type == "event"
        assert entity.metadata.get("event_type") == "ambient_trace"
        assert entity.metadata.get("session_id") == "sess-amb"

    @pytest.mark.asyncio
    async def test_prune_expired_checkpoints_removes_real_published_checkpoint(self, real_store):
        """End-to-end: publish via the real store, then TTL-prune it away."""
        store, _graph = real_store

        record = await store.publish_checkpoint(
            session_id="sess-old", project_slug="depthfusion",
            plan_state="will expire", files_modified=[],
        )
        # Backdate the persisted file's created_at directly (simulates time
        # having passed) rather than sleeping in the test.
        path = checkpoint_store_dir("depthfusion") / f"{record.checkpoint_id}.json"
        from datetime import datetime, timedelta, timezone
        backdated = json.loads(path.read_text())
        backdated["created_at"] = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        path.write_text(json.dumps(backdated))

        deleted = prune_expired_checkpoints("depthfusion", ttl_days=7)

        assert deleted == 1
        assert store.get_checkpoint(record.checkpoint_id, "depthfusion") is None
