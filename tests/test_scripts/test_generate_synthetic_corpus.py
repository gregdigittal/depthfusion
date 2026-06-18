"""Tests for scripts/generate_synthetic_corpus.py — T-677."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Import the script as a module without executing main()
_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "generate_synthetic_corpus.py"
_spec = importlib.util.spec_from_file_location("generate_synthetic_corpus", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["generate_synthetic_corpus"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[arg-type]

SyntheticCorpusGenerator = _mod.SyntheticCorpusGenerator
CorpusStats = _mod.CorpusStats


# ---------------------------------------------------------------------------
# Principal generation
# ---------------------------------------------------------------------------

class TestPrincipalGeneration:
    def test_correct_count(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=10, num_principals=50, seed=0)
        principals = gen.build_principals()
        assert len(principals) == 50

    def test_all_principals_have_groups(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=10, num_principals=50, seed=0)
        for p in gen.build_principals():
            assert len(p.groups) >= 1

    def test_admin_tier_exists(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=10, num_principals=100, seed=0)
        admins = [p for p in gen.build_principals() if p.tier == 0]
        assert len(admins) >= 1

    def test_tier_distribution(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=10, num_principals=100, seed=0)
        by_tier: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0}
        for p in gen.build_principals():
            by_tier[p.tier] += 1
        # Tier 2 (regular) should be the majority
        assert by_tier[2] > by_tier[0]
        assert by_tier[2] > by_tier[1]
        assert by_tier[2] > by_tier[3]


# ---------------------------------------------------------------------------
# ACL skew
# ---------------------------------------------------------------------------

class TestACLSkew:
    def test_admins_always_in_acl(self) -> None:
        """Admin principals must appear in every document's ACL."""
        gen = SyntheticCorpusGenerator(num_documents=20, num_principals=50, seed=1)
        gen.build_principals()
        admin_pids = {p.principal_id for p in gen._tier_groups[0]}  # type: ignore[index]
        for doc in gen.iter_documents():
            acl_set = set(doc.acl_allow)
            assert admin_pids.issubset(acl_set), (
                f"Doc {doc.doc_id} missing admin principals"
            )

    def test_restricted_docs_have_small_acl(self) -> None:
        """Restricted docs should grant access to far fewer principals than public docs."""
        gen = SyntheticCorpusGenerator(num_documents=200, num_principals=100, seed=2)
        gen.build_principals()
        restricted_acl_sizes = []
        public_acl_sizes = []
        for doc in gen.iter_documents():
            if doc.classification == "restricted":
                restricted_acl_sizes.append(len(doc.acl_allow))
            elif doc.classification == "public":
                public_acl_sizes.append(len(doc.acl_allow))
        if restricted_acl_sizes and public_acl_sizes:
            assert sum(restricted_acl_sizes) / len(restricted_acl_sizes) < sum(public_acl_sizes) / len(public_acl_sizes)

    def test_power_law_skew(self) -> None:
        """Top 10% of principals should access more docs than bottom 10%."""
        gen = SyntheticCorpusGenerator(num_documents=100, num_principals=100, seed=3)
        principals = gen.build_principals()

        # Count docs accessible per principal
        access_count: dict[str, int] = {p.principal_id: 0 for p in principals}
        for doc in gen.iter_documents():
            for pid in doc.acl_allow:
                if pid in access_count:
                    access_count[pid] += 1

        counts = sorted(access_count.values(), reverse=True)
        n = len(counts)
        top10_avg = sum(counts[:n // 10]) / max(1, n // 10)
        bot10_avg = sum(counts[-(n // 10):]) / max(1, n // 10)
        assert top10_avg > bot10_avg * 2, (
            "Expected top 10% principals to access at least 2× more docs than bottom 10%"
        )


# ---------------------------------------------------------------------------
# Document + chunk generation
# ---------------------------------------------------------------------------

class TestDocumentGeneration:
    def test_correct_doc_count(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=50, num_principals=20, seed=0)
        docs = list(gen.iter_documents())
        assert len(docs) == 50

    def test_all_docs_have_chunks(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=20, num_principals=20, seed=0)
        for doc in gen.iter_documents():
            assert len(doc.chunks) >= 1
            assert all(len(c) > 0 for c in doc.chunks)

    def test_chunk_count_distribution(self) -> None:
        """Chunk counts should be 1–20 (log-normal clamped)."""
        gen = SyntheticCorpusGenerator(num_documents=100, num_principals=20, seed=0)
        for doc in gen.iter_documents():
            assert 1 <= len(doc.chunks) <= 20

    def test_valid_classifications(self) -> None:
        valid = {"public", "internal", "confidential", "restricted"}
        gen = SyntheticCorpusGenerator(num_documents=50, num_principals=20, seed=0)
        for doc in gen.iter_documents():
            assert doc.classification in valid

    def test_doc_ids_unique(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=100, num_principals=20, seed=0)
        ids = [doc.doc_id for doc in gen.iter_documents()]
        assert len(ids) == len(set(ids))

    def test_deterministic_output(self) -> None:
        """Same seed must produce identical output."""
        gen1 = SyntheticCorpusGenerator(num_documents=20, num_principals=20, seed=7)
        gen2 = SyntheticCorpusGenerator(num_documents=20, num_principals=20, seed=7)
        docs1 = list(gen1.iter_documents())
        docs2 = list(gen2.iter_documents())
        assert [(d.doc_id, d.classification, len(d.chunks)) for d in docs1] == [
            (d.doc_id, d.classification, len(d.chunks)) for d in docs2
        ]


# ---------------------------------------------------------------------------
# JSONL chunks output
# ---------------------------------------------------------------------------

class TestChunksJSONL:
    def test_jsonl_schema(self) -> None:
        """Each JSONL line must have required fields."""
        gen = SyntheticCorpusGenerator(num_documents=5, num_principals=10, seed=0)
        required = {"doc_id", "chunk_idx", "text", "acl_allow", "classification"}
        for line in gen.iter_chunks_jsonl():
            record = json.loads(line)
            assert required.issubset(record.keys())
            assert isinstance(record["acl_allow"], list)
            assert isinstance(record["text"], str)
            assert len(record["text"]) > 0

    def test_chunk_count_matches_docs(self) -> None:
        """Total JSONL lines must equal sum of chunks across all documents."""
        gen = SyntheticCorpusGenerator(num_documents=20, num_principals=10, seed=0)
        docs = list(gen.iter_documents())
        total_expected = sum(len(d.chunks) for d in docs)

        # Reset and count from JSONL
        gen2 = SyntheticCorpusGenerator(num_documents=20, num_principals=10, seed=0)
        total_jsonl = sum(1 for _ in gen2.iter_chunks_jsonl())
        assert total_jsonl == total_expected


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_fields(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=50, num_principals=30, seed=0)
        stats = gen.compute_stats()
        assert stats.num_principals == 30
        assert stats.num_documents == 50
        assert stats.num_chunks > 50
        assert 0.0 <= stats.acl_skew_p10_coverage <= 1.0
        assert stats.avg_chunks_per_doc >= 1.0
        assert stats.avg_acl_size > 0

    def test_classification_dist_sums_to_docs(self) -> None:
        gen = SyntheticCorpusGenerator(num_documents=100, num_principals=30, seed=0)
        stats = gen.compute_stats()
        assert sum(stats.classification_dist.values()) == 100
