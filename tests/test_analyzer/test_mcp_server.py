"""Tests for MCP server module — including v0.5.0 confirm_discovery (T-144/T-145)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from depthfusion.core.config import DepthFusionConfig
from depthfusion.mcp.server import TOOLS, _handle_tools_call, get_enabled_tools


def test_tools_dict_has_thirty_two_entries():
    """Total tool count: 32 (E-46 added event_publish, event_seed, agent_trail)."""
    assert len(TOOLS) == 32
    expected = {
        "depthfusion_status",
        "depthfusion_recall_relevant",
        "depthfusion_tag_session",
        "depthfusion_publish_context",
        "depthfusion_run_recursive",
        "depthfusion_tier_status",
        "depthfusion_auto_learn",
        "depthfusion_compress_session",
        "depthfusion_graph_traverse",
        "depthfusion_graph_status",
        "depthfusion_set_scope",
        "depthfusion_confirm_discovery",
        "depthfusion_prune_discoveries",          # v0.5.1 S-55
        "depthfusion_set_memory_score",           # E-27 / S-70
        "depthfusion_recall_feedback",            # E-27 / S-72
        "depthfusion_pin_discovery",              # E-27 / S-69
        "depthfusion_describe_capabilities",      # E-28 / S-76
        "depthfusion_inspect_discovery",          # E-28 / S-76
        "depthfusion_retrieve_context",           # E-31 / S-99
        "depthfusion_record_decision",            # E-31 / S-97
        "depthfusion_record_incident",            # E-31 / S-98
        "depthfusion_mark_superseded",            # E-31 / S-98
        "depthfusion_report_outcome",             # E-31 / S-98
        "depthfusion_get_cognitive_state",        # E-31 / S-99
        "depthfusion_record_telemetry",           # E-33 / S-106/S-107
        "depthfusion_query_telemetry",            # E-33 / S-106/S-107
        "depthfusion_surface_skill_candidates",   # E-34 / S-109
        "depthfusion_session_seed",               # E-35 / S-111
        "depthfusion_hnsw_capability",            # E-45 HNSW fused recall
        "depthfusion_event_publish",              # E-46 / S-143
        "depthfusion_event_seed",                 # E-46 / S-143
        "depthfusion_agent_trail",                # E-46 / S-143
    }
    assert set(TOOLS.keys()) == expected


def test_get_enabled_tools_all_flags_true():
    config = DepthFusionConfig(
        rlm_enabled=True, router_enabled=True, graph_enabled=True,
        cognitive_retrieval=True, decision_memory=True, operational_memory=True,
    )
    enabled = get_enabled_tools(config)
    assert set(enabled) == set(TOOLS.keys())
    assert len(enabled) == 32


def test_get_enabled_tools_rlm_disabled_excludes_recursive():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=True)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled
    # 22 always-on (E-46 +3 fabric tools) + 1 router = 23
    assert len(enabled) == 23


def test_get_enabled_tools_router_disabled_excludes_publish():
    config = DepthFusionConfig(rlm_enabled=True, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_publish_context" not in enabled
    # 22 always-on (E-46 +3 fabric tools) + 1 rlm = 23
    assert len(enabled) == 23


def test_get_enabled_tools_both_disabled():
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_run_recursive" not in enabled
    assert "depthfusion_publish_context" not in enabled
    # 22 always-on (E-46 +3 fabric tools) = 22
    assert len(enabled) == 22


def test_core_tools_always_enabled():
    """Status, recall, tag, and v0.3.0 tools are never gated by feature flags."""
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_status" in enabled
    assert "depthfusion_recall_relevant" in enabled
    assert "depthfusion_tag_session" in enabled
    assert "depthfusion_tier_status" in enabled
    assert "depthfusion_auto_learn" in enabled
    assert "depthfusion_compress_session" in enabled


def test_confirm_discovery_always_enabled():
    """depthfusion_confirm_discovery is always enabled — no feature flag."""
    config = DepthFusionConfig(rlm_enabled=False, router_enabled=False, graph_enabled=False)
    enabled = get_enabled_tools(config)
    assert "depthfusion_confirm_discovery" in enabled


def test_server_module_importable():
    """MCP server module must be importable without side effects."""
    import depthfusion.mcp.server  # noqa: F401

    assert True  # If we get here, import succeeded


def test_graph_tools_registered_when_flag_enabled():
    """Graph tools appear in enabled list when graph_enabled=True."""
    config = MagicMock()
    config.router_enabled = False
    config.rlm_enabled = False
    config.graph_enabled = True
    enabled = get_enabled_tools(config)
    assert "depthfusion_graph_traverse" in enabled
    assert "depthfusion_graph_status" in enabled
    assert "depthfusion_set_scope" in enabled


def test_graph_tools_absent_when_flag_disabled():
    config = MagicMock()
    config.router_enabled = False
    config.rlm_enabled = False
    config.graph_enabled = False
    enabled = get_enabled_tools(config)
    assert "depthfusion_graph_traverse" not in enabled
    assert "depthfusion_graph_status" not in enabled
    assert "depthfusion_set_scope" not in enabled


# ---------------------------------------------------------------------------
# depthfusion_confirm_discovery tool (T-144 / T-145 / CM-5)
# ---------------------------------------------------------------------------

class TestConfirmDiscovery:
    def _cfg(self):
        return DepthFusionConfig(rlm_enabled=False, router_enabled=False, graph_enabled=False)

    def test_missing_text_returns_error(self):
        config = self._cfg()
        result = _handle_tools_call("depthfusion_confirm_discovery", {}, config)
        assert result["isError"] is False  # protocol success
        body = json.loads(result["content"][0]["text"])
        assert body["ok"] is False
        assert "text" in body["error"].lower()

    def test_valid_text_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "test-proj")
        # Patch write_decisions to avoid real filesystem writes in ~/.claude

        written_path = tmp_path / "2026-04-20-test-proj-decisions.md"
        written_path.write_text("---\ntype: decisions\n---\n")

        import unittest.mock as mock
        with mock.patch(
            "depthfusion.capture.decision_extractor.write_decisions",
            return_value=written_path,
        ):
            result = _handle_tools_call(
                "depthfusion_confirm_discovery",
                {"text": "Use asyncpg over psycopg2 for async support", "project": "test-proj"},
                self._cfg(),
            )

        assert result["isError"] is False
        body = json.loads(result["content"][0]["text"])
        assert body["ok"] is True
        assert body["project"] == "test-proj"

    def test_text_truncated_at_300_chars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "proj")
        long_text = "A" * 400
        import unittest.mock as mock
        with mock.patch(
            "depthfusion.capture.decision_extractor.write_decisions",
            return_value=None,
        ):
            result = _handle_tools_call(
                "depthfusion_confirm_discovery",
                {"text": long_text, "project": "proj"},
                self._cfg(),
            )
        assert result["isError"] is False

    def test_invalid_category_defaults_to_decision(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "proj")
        import unittest.mock as mock

        captured_entries = []

        def fake_write(entries, **kwargs):
            captured_entries.extend(entries)
            return None

        with mock.patch(
            "depthfusion.capture.decision_extractor.write_decisions",
            side_effect=fake_write,
        ):
            _handle_tools_call(
                "depthfusion_confirm_discovery",
                {"text": "Always validate at boundaries", "project": "proj",
                 "category": "invalid_category"},
                self._cfg(),
            )
        assert captured_entries[0].category == "decision"

    def test_confidence_clamped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_PROJECT", "proj")
        import unittest.mock as mock

        captured_entries = []

        def fake_write(entries, **kwargs):
            captured_entries.extend(entries)
            return None

        with mock.patch(
            "depthfusion.capture.decision_extractor.write_decisions",
            side_effect=fake_write,
        ):
            _handle_tools_call(
                "depthfusion_confirm_discovery",
                {"text": "Deploy via kubernetes", "project": "proj", "confidence": 99.9},
                self._cfg(),
            )
        assert captured_entries[0].confidence <= 1.0

    def test_unknown_tool_returns_error(self):
        config = self._cfg()
        result = _handle_tools_call("depthfusion_nonexistent", {}, config)
        assert result["isError"] is True


# ---------------------------------------------------------------------------
# E-46 S-143 T-493 — Event Graph Fabric MCP tool registration
# ---------------------------------------------------------------------------

class TestFabricToolRegistration:
    """Tool registration and schema tests for the 3 new E-46 fabric tools."""

    def _cfg(self):
        return DepthFusionConfig(rlm_enabled=False, router_enabled=False, graph_enabled=False)

    def test_event_publish_always_enabled(self):
        enabled = get_enabled_tools(self._cfg())
        assert "depthfusion_event_publish" in enabled

    def test_event_seed_always_enabled(self):
        enabled = get_enabled_tools(self._cfg())
        assert "depthfusion_event_seed" in enabled

    def test_agent_trail_always_enabled(self):
        enabled = get_enabled_tools(self._cfg())
        assert "depthfusion_agent_trail" in enabled

    def test_event_publish_missing_content_returns_error(self):
        result = _handle_tools_call(
            "depthfusion_event_publish",
            {"agent_id": "agent-a", "project_slug": "proj"},
            self._cfg(),
        )
        assert result["isError"] is False
        body = json.loads(result["content"][0]["text"])
        assert "error" in body

    def test_event_publish_missing_agent_returns_error(self):
        result = _handle_tools_call(
            "depthfusion_event_publish",
            {"content": "hello", "project_slug": "proj"},
            self._cfg(),
        )
        assert result["isError"] is False
        body = json.loads(result["content"][0]["text"])
        assert "error" in body

    def test_event_seed_missing_projects_returns_error(self):
        result = _handle_tools_call(
            "depthfusion_event_seed",
            {},
            self._cfg(),
        )
        assert result["isError"] is False
        body = json.loads(result["content"][0]["text"])
        assert "error" in body

    def test_agent_trail_missing_agent_id_returns_error(self):
        result = _handle_tools_call(
            "depthfusion_agent_trail",
            {},
            self._cfg(),
        )
        assert result["isError"] is False
        body = json.loads(result["content"][0]["text"])
        assert "error" in body


# ---------------------------------------------------------------------------
# E-46 S-143 T-493 — content-hash dedup and fabric_seed ranking
# ---------------------------------------------------------------------------

class _FakeGraph:
    """Minimal in-memory GraphBackend stub (mirrors test_event_store.py pattern)."""

    def __init__(self) -> None:
        self._entities: dict = {}
        self._edges: list = []

    def upsert_entity(self, entity) -> None:
        self._entities[entity.entity_id] = entity

    def get_entity(self, entity_id: str):
        return self._entities.get(entity_id)

    def upsert_edge(self, edge) -> None:
        self._edges.append(edge)

    def get_edges(self, entity_id, relationship_filter=None, as_of=None):
        edges = [e for e in self._edges if e.source_id == entity_id or e.target_id == entity_id]
        if relationship_filter:
            edges = [e for e in edges if e.relationship in relationship_filter]
        return edges

    def all_entities(self) -> list:
        return list(self._entities.values())

    def node_count(self) -> int:
        return len(self._entities)

    def edge_count(self) -> int:
        return len(self._edges)


def _make_fabric_store():
    from depthfusion.core.event_store import EventStore, InMemoryStreamBackend
    return EventStore(graph=_FakeGraph(), stream=InMemoryStreamBackend())


class TestEventStoreDedup:
    """Verify content-hash dedup: N concurrent publishes of identical content → 1 MemoryEntity."""

    def test_duplicate_publish_returns_deduped_flag(self):
        import asyncio
        store = _make_fabric_store()
        r1 = asyncio.run(store.publish_memory("same content", "agent-a", "proj"))
        r2 = asyncio.run(store.publish_memory("same content", "agent-b", "proj"))
        assert r1["memory_id"] == r2["memory_id"]
        assert r1["deduped"] is False
        assert r2["deduped"] is True

    def test_unique_content_not_deduped(self):
        import asyncio
        store = _make_fabric_store()
        r1 = asyncio.run(store.publish_memory("content alpha", "agent-a", "proj"))
        r2 = asyncio.run(store.publish_memory("content beta", "agent-a", "proj"))
        assert r1["memory_id"] != r2["memory_id"]
        assert r1["deduped"] is False
        assert r2["deduped"] is False

    def test_one_memory_entity_n_event_entities(self):
        import asyncio

        from depthfusion.core.event_store import EventStore, InMemoryStreamBackend

        graph = _FakeGraph()
        store = EventStore(graph=graph, stream=InMemoryStreamBackend())

        n = 10
        results = [
            asyncio.run(store.publish_memory("shared content", f"agent-{i}", "proj"))
            for i in range(n)
        ]
        memory_ids = {r["memory_id"] for r in results}
        assert len(memory_ids) == 1, "All publishes must produce exactly 1 MemoryEntity"

        all_entities = graph.all_entities()
        event_entities = [e for e in all_entities if e.type == "event"]
        memory_entities = [e for e in all_entities if e.type == "memory"]
        assert len(memory_entities) == 1
        assert len(event_entities) == n


class TestFabricSeedBundle:
    """Verify fabric_seed_bundle ranking: observer_count boosts score."""

    def test_empty_projects_returns_empty_bundle(self):
        import asyncio
        store = _make_fabric_store()
        result = asyncio.run(store.fabric_seed_bundle(projects=["no-such-proj"]))
        assert result["bundle"] == []
        assert "degraded" in result

    def test_bundle_respects_top_k(self):
        import asyncio
        store = _make_fabric_store()
        for i in range(5):
            asyncio.run(store.publish_memory(f"content {i}", "agent-a", "proj"))
        result = asyncio.run(store.fabric_seed_bundle(projects=["proj"], top_k=3))
        assert len(result["bundle"]) <= 3

    def test_observer_count_boosts_score(self):
        """Memory seen by 3 agents ranks higher than memory seen by 1 agent."""
        import asyncio

        from depthfusion.core.event_store import EventStore, InMemoryStreamBackend
        from depthfusion.graph.types import Edge

        graph = _FakeGraph()
        store = EventStore(graph=graph, stream=InMemoryStreamBackend())

        # Publish "popular" content from 3 agents (dedup → 1 MemoryEntity)
        r_popular = asyncio.run(store.publish_memory("popular memory", "agent-a", "proj"))
        memory_id = r_popular["memory_id"]
        asyncio.run(store.publish_memory("popular memory", "agent-b", "proj"))
        asyncio.run(store.publish_memory("popular memory", "agent-c", "proj"))

        # Add AGENT_RECEIVED edges to simulate agents receiving this memory
        graph.upsert_edge(Edge(
            edge_id=f"{memory_id}-recv-b", source_id=memory_id, target_id="agent-b",
            relationship="AGENT_RECEIVED", weight=1.0, signals=["fabric"],
            metadata={"agent_id": "agent-b"},
        ))
        graph.upsert_edge(Edge(
            edge_id=f"{memory_id}-recv-c", source_id=memory_id, target_id="agent-c",
            relationship="AGENT_RECEIVED", weight=1.0, signals=["fabric"],
            metadata={"agent_id": "agent-c"},
        ))

        # Publish "solo" content from 1 agent (no AGENT_RECEIVED edges)
        asyncio.run(store.publish_memory("solo memory", "agent-x", "proj"))

        result = asyncio.run(store.fabric_seed_bundle(projects=["proj"], top_k=10))
        bundle = result["bundle"]
        assert len(bundle) >= 2

        popular_item = next((b for b in bundle if "popular" in b["name"]), None)
        solo_item = next((b for b in bundle if "solo" in b["name"]), None)
        assert popular_item is not None
        assert solo_item is not None
        assert popular_item["score"] >= solo_item["score"]
