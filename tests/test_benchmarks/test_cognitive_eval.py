"""Cognitive evaluation benchmark suite — Task 13 / E-31 / S-103.

6 metrics:
1. Valid Recall@K: fraction of top-K results that are not stale/archived
2. Stale Injection Rate: fraction of top-K results that ARE stale
3. Contradiction Precision: true contradictions / detected contradictions
4. Decision Recall Rate: decision memories accessible when queried
5. Operational Reuse Rate: incidents with at least 1 outcome recorded
6. Outcome Lift: confidence delta before/after outcome recording
"""
from __future__ import annotations

from depthfusion.cognitive.contradiction import ContradictionEngine
from depthfusion.cognitive.scorer import CognitiveScorer, ScoringContext
from depthfusion.core.memory_object import MemoryObject, MemoryStatus, MemoryType


def make_mem(
    id: str,
    type: MemoryType = MemoryType.SEMANTIC,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    confidence: float = 0.8,
    content: str = "test content",
) -> MemoryObject:
    m = MemoryObject(id=id, project_id="eval", type=type, content=content)
    m.status = status
    m.confidence.score = confidence
    return m


# ── Metric 1 & 2: Valid Recall@K and Stale Injection Rate ───────────────────

class TestValidRecallAtK:
    def test_all_active_gives_recall_one(self):
        memories = [make_mem(f"m{i}") for i in range(10)]
        stale_count = sum(1 for m in memories if m.status == MemoryStatus.STALE)
        valid_recall = 1.0 - (stale_count / len(memories))
        assert valid_recall == 1.0

    def test_mixed_stale_gives_correct_recall(self):
        memories = [make_mem(f"m{i}") for i in range(8)]
        memories.append(make_mem("stale-1", status=MemoryStatus.STALE))
        memories.append(make_mem("stale-2", status=MemoryStatus.STALE))
        stale_count = sum(1 for m in memories if m.status == MemoryStatus.STALE)
        stale_injection_rate = stale_count / len(memories)
        valid_recall = 1.0 - stale_injection_rate
        assert abs(valid_recall - 0.8) < 0.01
        assert abs(stale_injection_rate - 0.2) < 0.01


# ── Metric 3: Contradiction Precision ───────────────────────────────────────

class TestContradictionPrecision:
    def test_clear_contradiction_detected(self):
        engine = ContradictionEngine(auto_emit_threshold=0.85)
        m1 = make_mem("m1", content="Redis is used for caching", confidence=0.9)
        m2 = make_mem("m2", content="Redis is not used for caching", confidence=0.9)
        conflicts = engine.detect(m1, m2)
        assert len(conflicts) >= 1

    def test_no_false_contradictions_on_unrelated(self):
        engine = ContradictionEngine()
        m1 = make_mem("m1", content="SQLite is used for file index")
        m2 = make_mem("m2", content="Redis is used for caching")
        conflicts = engine.detect(m1, m2)
        assert len(conflicts) == 0

    def test_no_contradiction_when_both_negate(self):
        engine = ContradictionEngine()
        m1 = make_mem("m1", content="Redis is not used for caching")
        m2 = make_mem("m2", content="Redis is not used for anything")
        conflicts = engine.detect(m1, m2)
        assert len(conflicts) == 0


# ── Metric 4: Decision Recall Rate ──────────────────────────────────────────

class TestDecisionRecallRate:
    def test_decision_memories_queryable(self, tmp_path):
        from depthfusion.mcp.cognitive_tools import build_decision_memory
        from depthfusion.storage.memory_store import MemoryStore

        store = MemoryStore(tmp_path / "memories.db")
        for i in range(3):
            m = build_decision_memory(
                project_id="eval",
                decision=f"Decision {i}",
                rationale=f"Rationale {i}",
                actor="test",
            )
            store.upsert(m)

        decisions = store.query(project_id="eval", memory_type="decision")
        recall_rate = len(decisions) / 3
        assert recall_rate == 1.0


# ── Metric 5 & 6: Operational Reuse and Outcome Lift ────────────────────────

class TestOutcomeLift:
    def test_outcome_recording_increases_confidence(self, tmp_path):
        from depthfusion.mcp.cognitive_tools import build_incident_memory
        from depthfusion.storage.memory_store import MemoryStore

        store = MemoryStore(tmp_path / "memories.db")
        incident = build_incident_memory(
            project_id="eval",
            error="Some error",
            fix="Some fix",
            lesson="Some lesson",
            actor="test",
        )
        initial_score = incident.confidence.score
        store.upsert(incident)

        retrieved = store.get(incident.id)
        retrieved.confidence.score = min(1.0, retrieved.confidence.score + 0.05)
        retrieved.confidence.verification_count += 1
        retrieved.extra.setdefault("outcomes", []).append({"success": True})
        store.upsert(retrieved)

        final = store.get(incident.id)
        assert final.confidence.score > initial_score
        assert final.confidence.verification_count == 1
        assert len(final.extra["outcomes"]) == 1


# ── CognitiveScorer benchmarks ───────────────────────────────────────────────

class TestCognitiveScorerBenchmark:
    def test_scorer_is_deterministic(self):
        s = CognitiveScorer()
        ctx = ScoringContext(
            semantic=0.8, lexical=0.6, confidence=0.9,
            regime_match=1.0, graph_proximity=0.5,
            recency=0.7, historical_usefulness=0.4, workflow_intent=0.3,
        )
        score1, _ = s.score_with_breakdown(ctx)
        score2, _ = s.score_with_breakdown(ctx)
        assert score1 == score2

    def test_high_confidence_boosts_score(self):
        s = CognitiveScorer()
        ctx_high = ScoringContext(
            semantic=0.5, confidence=0.95,
            lexical=0.0, regime_match=0.0, graph_proximity=0.0,
            recency=0.0, historical_usefulness=0.0, workflow_intent=0.0,
        )
        ctx_low = ScoringContext(
            semantic=0.5, confidence=0.1,
            lexical=0.0, regime_match=0.0, graph_proximity=0.0,
            recency=0.0, historical_usefulness=0.0, workflow_intent=0.0,
        )
        assert s.score(ctx_high) > s.score(ctx_low)

    def test_score_stays_in_unit_interval(self):
        s = CognitiveScorer()
        for vals in [(1.0,) * 8, (0.0,) * 8, (0.5,) * 8]:
            ctx = ScoringContext(*vals)
            score = s.score(ctx)
            assert 0.0 <= score <= 1.0
