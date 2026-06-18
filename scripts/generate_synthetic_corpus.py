#!/usr/bin/env python3
"""T-677 — Synthetic corpus + principal/ACL generator.

Produces a large synthetic corpus matching E-61 S-197 AC-1:
  - 250 000 chunks (default)
  - 50 000 documents
  - 500 principals
  - Realistic ACL skew (power-law: 10% of principals access 60% of docs)

Output formats
--------------
  --mode chunks     Print JSONL records for direct BM25 seeding (default)
  --mode docs       Write discovery-style .md files with ACL frontmatter
  --mode stats      Print corpus statistics then exit

Usage
-----
  # Benchmark seeding (in-memory, write stats):
  python scripts/generate_synthetic_corpus.py --num-docs 50000 --chunks-per-doc 5 --mode stats

  # Write 1000 .md files to /tmp/corpus/:
  python scripts/generate_synthetic_corpus.py --num-docs 1000 --output-dir /tmp/corpus --mode docs

  # Emit 10000 JSONL chunks to stdout (pipe to load harness):
  python scripts/generate_synthetic_corpus.py --num-docs 2000 --mode chunks | head -100

Designed to be importable as a library — see SyntheticCorpusGenerator class.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

_TOPIC_WORDS: dict[str, list[str]] = {
    "engineering": [
        "architecture", "pipeline", "service", "endpoint", "router",
        "backend", "frontend", "schema", "migration", "cache", "queue",
        "worker", "deploy", "rollback", "feature", "branch", "commit",
        "dependency", "library", "module", "interface", "contract",
    ],
    "security": [
        "authz", "authentication", "token", "principal", "identity",
        "acl", "role", "permission", "secret", "credential", "audit",
        "policy", "encryption", "certificate", "scope", "claim", "jwt",
        "revocation", "replay", "forgery", "injection", "bypass",
    ],
    "data": [
        "corpus", "chunk", "document", "embedding", "vector", "index",
        "retrieval", "query", "recall", "fusion", "score", "rank",
        "context", "memory", "session", "ingestion", "transform", "parse",
        "classification", "label", "entity", "relation", "graph",
    ],
    "operations": [
        "latency", "throughput", "p95", "p99", "slo", "metric", "alert",
        "dashboard", "grafana", "prometheus", "log", "trace", "span",
        "incident", "oncall", "runbook", "capacity", "scale", "shard",
    ],
    "project": [
        "sprint", "backlog", "story", "task", "milestone", "release",
        "review", "gate", "decision", "adr", "stakeholder", "roadmap",
        "objective", "outcome", "metric", "kpi", "priority", "risk",
    ],
}

_ALL_WORDS: list[str] = [w for words in _TOPIC_WORDS.values() for w in words]

_CLASSIFICATIONS = ["public", "internal", "confidential", "restricted"]
# Weight: most docs are internal or public; restricted is rare
_CLASSIFICATION_WEIGHTS = [0.20, 0.50, 0.22, 0.08]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Principal:
    principal_id: str
    groups: list[str]
    # 'tier' drives ACL skew: 0=admin (broad), 1=senior, 2=regular, 3=restricted
    tier: int = 2


@dataclass
class SyntheticDocument:
    doc_id: str
    title: str
    classification: str
    acl_allow: list[str]  # list of principal_ids
    chunks: list[str]


@dataclass
class CorpusStats:
    num_principals: int
    num_documents: int
    num_chunks: int
    classification_dist: dict[str, int] = field(default_factory=dict)
    acl_skew_p10_coverage: float = 0.0  # fraction of docs accessible to top 10% of principals
    avg_chunks_per_doc: float = 0.0
    avg_acl_size: float = 0.0


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------


class SyntheticCorpusGenerator:
    """Generates a reproducible synthetic corpus for DepthFusion load testing.

    Parameters
    ----------
    num_documents:
        Total document count (default 50 000).
    chunks_per_doc_mean:
        Mean chunks per document (default 5, log-normal distribution).
    num_principals:
        Number of synthetic principals (default 500).
    seed:
        RNG seed for reproducibility.
    """

    def __init__(
        self,
        num_documents: int = 50_000,
        chunks_per_doc_mean: float = 5.0,
        num_principals: int = 500,
        seed: int = 42,
    ) -> None:
        self.num_documents = num_documents
        self.chunks_per_doc_mean = chunks_per_doc_mean
        self.num_principals = num_principals
        self._rng = random.Random(seed)

        self._principals: list[Principal] | None = None
        self._tier_groups: dict[int, list[Principal]] | None = None

    # ------------------------------------------------------------------
    # Principal generation
    # ------------------------------------------------------------------

    def build_principals(self) -> list[Principal]:
        """Build 500 principals across 4 tiers with realistic group distribution."""
        if self._principals is not None:
            return self._principals

        principals: list[Principal] = []
        n = self.num_principals

        # Tier distribution: 2% admin, 10% senior, 68% regular, 20% restricted
        tier_counts = {
            0: max(1, int(n * 0.02)),
            1: max(1, int(n * 0.10)),
            3: max(1, int(n * 0.20)),
        }
        tier_counts[2] = n - sum(tier_counts.values())

        idx = 0
        for tier, count in sorted(tier_counts.items()):
            for i in range(count):
                pid = f"p{idx:04d}"
                groups: list[str] = []
                if tier == 0:
                    groups = ["admin", "security", "engineering", "data"]
                elif tier == 1:
                    groups = [
                        self._rng.choice(["engineering", "security", "data"]),
                        "senior",
                    ]
                elif tier == 2:
                    groups = [self._rng.choice(["engineering", "data", "operations"])]
                else:
                    groups = ["external"]
                principals.append(Principal(pid, groups, tier))
                idx += 1

        self._principals = principals
        self._tier_groups = {0: [], 1: [], 2: [], 3: []}
        for p in principals:
            self._tier_groups[p.tier].append(p)

        return principals

    # ------------------------------------------------------------------
    # ACL generation — power-law skew
    # ------------------------------------------------------------------

    def _generate_acl(self, classification: str) -> list[str]:
        """Return a list of principal_ids that can access a document.

        Skew rules:
          - restricted: only admin + 1–3 senior principals
          - confidential: admin + ~15% of senior + ~5% of regular
          - internal: admin + all senior + ~40% of regular (no external)
          - public: admin + all senior + all regular (no external by default)
        """
        principals = self.build_principals()
        tier_groups = self._tier_groups
        assert tier_groups is not None

        acl: list[str] = []

        # Admins always have access
        acl.extend(p.principal_id for p in tier_groups[0])

        if classification == "restricted":
            # 1–3 senior principals get explicit grants
            n_senior = self._rng.randint(1, min(3, len(tier_groups[1])))
            acl.extend(p.principal_id for p in self._rng.sample(tier_groups[1], n_senior))

        elif classification == "confidential":
            # ~15% of senior, ~5% of regular
            n_senior = max(1, int(len(tier_groups[1]) * 0.15))
            n_regular = max(0, int(len(tier_groups[2]) * 0.05))
            acl.extend(p.principal_id for p in self._rng.sample(tier_groups[1], n_senior))
            if n_regular > 0:
                acl.extend(p.principal_id for p in self._rng.sample(tier_groups[2], n_regular))

        elif classification == "internal":
            acl.extend(p.principal_id for p in tier_groups[1])
            n_regular = max(1, int(len(tier_groups[2]) * 0.40))
            acl.extend(p.principal_id for p in self._rng.sample(tier_groups[2], n_regular))

        else:  # public
            acl.extend(p.principal_id for p in tier_groups[1])
            acl.extend(p.principal_id for p in tier_groups[2])

        return sorted(set(acl))

    # ------------------------------------------------------------------
    # Content generation
    # ------------------------------------------------------------------

    def _generate_chunk(self, doc_idx: int, chunk_idx: int, topic: str) -> str:
        """Generate a synthetic text chunk of ~80 words."""
        rng = self._rng
        topic_words = _TOPIC_WORDS.get(topic, _ALL_WORDS)

        # Build sentences from topic words + generic connectives
        connectives = ["the", "a", "an", "this", "each", "every", "all", "some", "for"]
        verbs = ["enables", "ensures", "requires", "validates", "provides", "manages"]
        prepositions = ["in", "for", "with", "by", "across", "through", "via", "over"]

        words: list[str] = [f"doc{doc_idx}", f"chunk{chunk_idx}"]
        for _ in range(10):  # ~10 sentences of ~8 words
            sentence = [
                rng.choice(topic_words),
                rng.choice(verbs),
                rng.choice(connectives),
                rng.choice(topic_words),
                rng.choice(prepositions),
                rng.choice(topic_words),
                rng.choice(topic_words),
                ".",
            ]
            words.extend(sentence)

        return " ".join(words)

    def _make_doc_id(self, idx: int) -> str:
        return f"doc-{idx:06d}"

    def _chunks_for_doc(self) -> int:
        """Log-normal chunk count per document (mean=5, min=1, max=20)."""
        # log-normal: mean ≈ chunks_per_doc_mean
        mu = math.log(self.chunks_per_doc_mean)
        sigma = 0.8
        raw = math.exp(self._rng.gauss(mu, sigma))
        return max(1, min(20, round(raw)))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_document(self, idx: int) -> SyntheticDocument:
        """Generate one synthetic document."""
        rng = self._rng
        topic = rng.choice(list(_TOPIC_WORDS.keys()))
        classification = rng.choices(
            _CLASSIFICATIONS, weights=_CLASSIFICATION_WEIGHTS
        )[0]
        acl = self._generate_acl(classification)
        n_chunks = self._chunks_for_doc()
        doc_id = self._make_doc_id(idx)
        title_words = rng.sample(_TOPIC_WORDS[topic], min(4, len(_TOPIC_WORDS[topic])))
        title = " ".join(title_words).title()
        chunks = [self._generate_chunk(idx, c, topic) for c in range(n_chunks)]
        return SyntheticDocument(
            doc_id=doc_id,
            title=title,
            classification=classification,
            acl_allow=acl,
            chunks=chunks,
        )

    def iter_documents(self) -> Iterator[SyntheticDocument]:
        """Yield all documents in order."""
        self.build_principals()
        for i in range(self.num_documents):
            yield self.generate_document(i)

    def iter_chunks_jsonl(self) -> Iterator[str]:
        """Yield JSONL lines, one per chunk, for BM25 seeding.

        Each record:
          {"doc_id": str, "chunk_idx": int, "text": str,
           "acl_allow": [str], "classification": str}
        """
        for doc in self.iter_documents():
            for chunk_idx, text in enumerate(doc.chunks):
                record = {
                    "doc_id": doc.doc_id,
                    "chunk_idx": chunk_idx,
                    "text": text,
                    "acl_allow": doc.acl_allow,
                    "classification": doc.classification,
                }
                yield json.dumps(record)

    def compute_stats(self) -> CorpusStats:
        """Compute corpus statistics (iterates all documents)."""
        self.build_principals()
        classification_dist: dict[str, int] = {c: 0 for c in _CLASSIFICATIONS}
        total_chunks = 0
        total_acl_size = 0
        num_docs = 0

        # For skew measurement: count docs accessible to top 10% principals
        assert self._tier_groups is not None
        top_10pct_pids = set(
            p.principal_id
            for tier in [0, 1]
            for p in self._tier_groups[tier]
        )
        docs_accessible_to_top10 = 0

        for doc in self.iter_documents():
            classification_dist[doc.classification] += 1
            total_chunks += len(doc.chunks)
            total_acl_size += len(doc.acl_allow)
            num_docs += 1
            if any(pid in top_10pct_pids for pid in doc.acl_allow):
                docs_accessible_to_top10 += 1

        return CorpusStats(
            num_principals=len(self._principals or []),
            num_documents=num_docs,
            num_chunks=total_chunks,
            classification_dist=classification_dist,
            acl_skew_p10_coverage=docs_accessible_to_top10 / max(1, num_docs),
            avg_chunks_per_doc=total_chunks / max(1, num_docs),
            avg_acl_size=total_acl_size / max(1, num_docs),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_docs_mode(gen: SyntheticCorpusGenerator, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for doc in gen.iter_documents():
        acl_yaml = "\n".join(f"  - {pid}" for pid in doc.acl_allow)
        frontmatter = (
            f"---\ntitle: {doc.title}\n"
            f"acl_allow:\n{acl_yaml}\n"
            f"classification: {doc.classification}\n---\n\n"
        )
        body = "\n\n".join(f"## Chunk {i}\n\n{chunk}" for i, chunk in enumerate(doc.chunks))
        path = output_dir / f"{doc.doc_id}.md"
        path.write_text(frontmatter + body)
    print(f"Wrote {gen.num_documents} documents to {output_dir}", file=sys.stderr)


def _chunks_mode(gen: SyntheticCorpusGenerator) -> None:
    for line in gen.iter_chunks_jsonl():
        print(line)


def _stats_mode(gen: SyntheticCorpusGenerator) -> None:
    print("Computing corpus statistics…", file=sys.stderr)
    stats = gen.compute_stats()
    print(f"Principals:          {stats.num_principals}")
    print(f"Documents:           {stats.num_documents}")
    print(f"Chunks:              {stats.num_chunks}")
    print(f"Avg chunks/doc:      {stats.avg_chunks_per_doc:.2f}")
    print(f"Avg ACL size:        {stats.avg_acl_size:.1f}")
    print(f"ACL skew (top-10%):  {stats.acl_skew_p10_coverage:.1%} of docs accessible to top 10% of principals")
    print("Classification distribution:")
    for cls, count in sorted(stats.classification_dist.items(), key=lambda x: -x[1]):
        pct = count / max(1, stats.num_documents) * 100
        print(f"  {cls:15s}  {count:6d}  ({pct:.1f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic DepthFusion corpus for E-61 load testing."
    )
    parser.add_argument("--num-docs", type=int, default=50_000,
                        help="Number of documents to generate (default: 50000)")
    parser.add_argument("--num-principals", type=int, default=500,
                        help="Number of principals (default: 500)")
    parser.add_argument("--chunks-per-doc", type=float, default=5.0,
                        help="Mean chunks per document, log-normal (default: 5)")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed (default: 42)")
    parser.add_argument(
        "--mode", choices=["chunks", "docs", "stats"], default="chunks",
        help="Output mode: 'chunks' (JSONL to stdout), 'docs' (.md files), 'stats' (print summary)"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/depthfusion-corpus"),
                        help="Output directory for --mode docs (default: /tmp/depthfusion-corpus)")
    args = parser.parse_args()

    gen = SyntheticCorpusGenerator(
        num_documents=args.num_docs,
        chunks_per_doc_mean=args.chunks_per_doc,
        num_principals=args.num_principals,
        seed=args.seed,
    )

    if args.mode == "docs":
        _write_docs_mode(gen, args.output_dir)
    elif args.mode == "stats":
        _stats_mode(gen)
    else:
        _chunks_mode(gen)


if __name__ == "__main__":
    main()
