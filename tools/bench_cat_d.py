#!/usr/bin/env python3
"""CIQS Category D benchmark harness — PRECEDED_BY temporal edge recall quality.

Loads "recent work" Q/A fixtures and scores each query in two configurations:
  edges=off  BM25 recall only (no graph traversal)
  edges=on   BM25 recall + PRECEDED_BY traversal boost

Emits a JSON report to docs/benchmarks/YYYY-MM-DD-ciqs-cat-d.json.

Usage:
    python tools/bench_cat_d.py
    python tools/bench_cat_d.py --output path/to/report.json
    python tools/bench_cat_d.py --fixture-dir tests/fixtures/ciqs_cat_d/
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project root on sys.path so we can import depthfusion directly
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from depthfusion.graph.traverser import traverse  # noqa: E402
from depthfusion.graph.types import Edge, Entity  # noqa: E402
from depthfusion.retrieval.bm25 import BM25, tokenize  # noqa: E402

# ---------------------------------------------------------------------------
# Boost factor for PRECEDED_BY traversal neighbours.
# Set to 1.1× the max positive BM25 score in the corpus so that a temporally
# linked session always ranks above non-boosted content, regardless of BM25
# vocabulary overlap.  A multiplier >1 means the traversal signal overrides
# lexical rank when the temporal chain is explicit — exactly the S-50 AC-3
# hypothesis.  Falls back to 1.0 when all BM25 scores are 0.
_PRECEDED_BY_BOOST_MULTIPLIER: float = 1.1

# Traverse from top-N BM25 results (not only rank-1) so that a session at
# rank 2 or 3 can still rescue its PRECEDED_BY neighbour.
_TRAVERSAL_SOURCE_TOP_K: int = 5

# Default fixture glob relative to project root
_DEFAULT_FIXTURE_DIR = _PROJECT_ROOT / "tests" / "fixtures" / "ciqs_cat_d"
_DEFAULT_REPORT_DIR = _PROJECT_ROOT / "docs" / "benchmarks"


# ---------------------------------------------------------------------------
# In-memory graph store (no disk I/O — keeps the harness offline and fast)
# ---------------------------------------------------------------------------

class _InMemoryGraphStore:
    """Minimal graph backend for the benchmark harness.

    Implements the depthfusion.graph.store.GraphBackend protocol without
    writing to disk — suitable for short-lived synthetic graphs per fixture.
    """

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._edges: dict[str, Edge] = {}

    def upsert_entity(self, entity: Entity) -> None:
        self._entities[entity.entity_id] = entity

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def upsert_edge(self, edge: Edge) -> None:
        self._edges[edge.edge_id] = edge

    def get_edges(
        self,
        entity_id: str,
        relationship_filter: list[str] | None = None,
        as_of: Any = None,
    ) -> list[Edge]:
        result = []
        for edge in self._edges.values():
            if edge.source_id == entity_id or edge.target_id == entity_id:
                if relationship_filter is None or edge.relationship in relationship_filter:
                    result.append(edge)
        return result

    def invalidate_edge(self, edge_id: str, valid_until: Any) -> bool:
        return edge_id in self._edges

    def all_entities(self) -> list[Entity]:
        return list(self._entities.values())

    def node_count(self) -> int:
        return len(self._entities)

    def edge_count(self) -> int:
        return len(self._edges)


# ---------------------------------------------------------------------------
# Graph construction from fixture data
# ---------------------------------------------------------------------------

def _build_graph(session_graph: dict, *, include_edges: bool) -> _InMemoryGraphStore:
    """Build an in-memory graph store from the fixture's session_graph block.

    edges=off  entities only, no PRECEDED_BY edges
    edges=on   entities + PRECEDED_BY edges fully populated
    """
    store = _InMemoryGraphStore()
    for entity_def in session_graph.get("entities", []):
        entity = Entity(
            entity_id=entity_def["session_id"],
            name=entity_def["session_id"],
            type="session",
            project=entity_def.get("project", ""),
            source_files=[],
            confidence=1.0,
            first_seen="",
            metadata={"summary": entity_def.get("summary", "")},
        )
        store.upsert_entity(entity)

    if include_edges:
        for edge_def in session_graph.get("edges", []):
            if edge_def.get("relationship") == "PRECEDED_BY":
                edge_id = (
                    f"{edge_def['source']}-{edge_def['target']}-PRECEDED_BY"
                )
                edge = Edge(
                    edge_id=edge_id,
                    source_id=edge_def["source"],
                    target_id=edge_def["target"],
                    relationship="PRECEDED_BY",
                    weight=1.0,
                    signals=["temporal"],
                    metadata={"delta_hours": edge_def.get("delta_hours", 0.0)},
                )
                store.upsert_edge(edge)
    return store


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def recall(
    query: str,
    corpus: list[dict],
    store: _InMemoryGraphStore,
    *,
    use_graph: bool,
    top_k: int = 10,
) -> list[dict]:
    """BM25 recall with optional PRECEDED_BY traversal boost.

    Args:
        query:      Natural language query string.
        corpus:     List of block dicts, each with at least 'chunk_id' and 'content'.
        store:      In-memory graph store for the current configuration.
        use_graph:  If True and graph has nodes, apply PRECEDED_BY traversal boost.
        top_k:      Maximum results to return.

    Returns:
        List of block dicts with added 'score' key, sorted descending.
    """
    if not corpus:
        return []

    texts = [block["content"] for block in corpus]
    corpus_tokens = [tokenize(t) for t in texts]
    bm25 = BM25(corpus_tokens)
    query_terms = tokenize(query)

    ranked_pairs = bm25.rank_all(query_terms)
    results: list[dict] = []
    for doc_idx, score in ranked_pairs:
        results.append({**corpus[doc_idx], "score": score})

    if not use_graph or store.node_count() == 0:
        return results[:top_k]

    # Compute boost magnitude: 110% of the highest positive BM25 score so
    # that a traversal-linked session always ranks above non-boosted content.
    positive_scores = [r["score"] for r in results if r["score"] > 0.0]
    boost = (max(positive_scores) * _PRECEDED_BY_BOOST_MULTIPLIER) if positive_scores else 1.0

    # Traverse from the top-N BM25 results to find PRECEDED_BY neighbours.
    extra: dict[str, float] = {}  # chunk_id → additional score
    for result in results[:_TRAVERSAL_SOURCE_TOP_K]:
        session_id = result["chunk_id"].split("#")[0]
        traversal = traverse(
            session_id,
            store,
            depth=1,
            relationship_filter=["PRECEDED_BY"],
        )
        if traversal:
            for neighbor, _edge in traversal.connected:
                # Map the session entity to its corpus chunk (convention: f"{session_id}#0")
                neighbor_chunk = f"{neighbor.entity_id}#0"
                # Take the maximum boost across all traversal paths to this chunk
                extra[neighbor_chunk] = max(extra.get(neighbor_chunk, 0.0), boost)

    if not extra:
        return results[:top_k]

    boosted = [{**r, "score": r["score"] + extra.get(r["chunk_id"], 0.0)} for r in results]
    boosted.sort(key=lambda b: -b["score"])
    return boosted[:top_k]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class FixtureScore:
    mrr: float
    hit_at_1: int
    hit_at_3: int
    hit_at_5: int


def score_results(ranked: list[dict], relevant_ids: list[str]) -> FixtureScore:
    """Compute MRR and hit@k for one query result set."""
    relevant_set = set(relevant_ids)
    mrr = 0.0
    hit = {1: 0, 3: 0, 5: 0}

    for rank_1based, block in enumerate(ranked, start=1):
        if block["chunk_id"] in relevant_set:
            if mrr == 0.0:
                mrr = 1.0 / rank_1based
            for k in (1, 3, 5):
                if rank_1based <= k:
                    hit[k] = 1

    return FixtureScore(mrr=mrr, hit_at_1=hit[1], hit_at_3=hit[3], hit_at_5=hit[5])


def aggregate(scores: list[FixtureScore]) -> dict:
    """Average a list of FixtureScore into an aggregate metrics dict."""
    n = len(scores)
    if n == 0:
        return {"mrr": 0.0, "hit_at_1": 0.0, "hit_at_3": 0.0, "hit_at_5": 0.0}
    return {
        "mrr": sum(s.mrr for s in scores) / n,
        "hit_at_1": sum(s.hit_at_1 for s in scores) / n,
        "hit_at_3": sum(s.hit_at_3 for s in scores) / n,
        "hit_at_5": sum(s.hit_at_5 for s in scores) / n,
    }


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

def load_fixtures(fixture_dir: Path) -> list[dict]:
    """Load all *.jsonl fixture files from fixture_dir."""
    fixtures: list[dict] = []
    for path in sorted(fixture_dir.glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    fixtures.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    print(f"  WARNING: skipped malformed line in {path}: {exc}", file=sys.stderr)
    return fixtures


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(fixtures: list[dict]) -> dict:
    """Run all fixtures in edges=off and edges=on configurations.

    Returns the full report dict (not yet serialised to disk).
    """
    off_scores: list[FixtureScore] = []
    on_scores: list[FixtureScore] = []
    per_fixture: list[dict] = []

    for i, fixture in enumerate(fixtures):
        query: str = fixture["query"]
        corpus: list[dict] = fixture.get("corpus", [])
        relevant_ids: list[str] = fixture.get("relevant_chunk_ids", [])
        session_graph: dict = fixture.get("session_graph", {"entities": [], "edges": []})

        store_off = _build_graph(session_graph, include_edges=False)
        store_on = _build_graph(session_graph, include_edges=True)

        ranked_off = recall(query, corpus, store_off, use_graph=True)
        ranked_on = recall(query, corpus, store_on, use_graph=True)

        score_off = score_results(ranked_off, relevant_ids)
        score_on = score_results(ranked_on, relevant_ids)

        off_scores.append(score_off)
        on_scores.append(score_on)

        per_fixture.append({
            "fixture_index": i,
            "query": query,
            "description": fixture.get("description", ""),
            "corpus_size": len(corpus),
            "relevant_count": len(relevant_ids),
            "edges_off": {
                "mrr": score_off.mrr,
                "hit_at_1": score_off.hit_at_1,
                "hit_at_3": score_off.hit_at_3,
                "hit_at_5": score_off.hit_at_5,
            },
            "edges_on": {
                "mrr": score_on.mrr,
                "hit_at_1": score_on.hit_at_1,
                "hit_at_3": score_on.hit_at_3,
                "hit_at_5": score_on.hit_at_5,
            },
            "delta_mrr_pp": (score_on.mrr - score_off.mrr) * 100.0,
        })

    agg_off = aggregate(off_scores)
    agg_on = aggregate(on_scores)
    delta_mrr_pp = (agg_on["mrr"] - agg_off["mrr"]) * 100.0

    return {
        "run_date": date.today().isoformat(),
        "fixture_count": len(fixtures),
        "edges_off": agg_off,
        "edges_on": agg_on,
        "delta_mrr_pp": round(delta_mrr_pp, 4),
        "s50_ac3_met": delta_mrr_pp >= 2.0,
        "per_fixture": per_fixture,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=_DEFAULT_FIXTURE_DIR,
        help="Directory containing *.jsonl fixture files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: docs/benchmarks/YYYY-MM-DD-ciqs-cat-d.json)",
    )
    args = parser.parse_args(argv)

    fixture_dir: Path = args.fixture_dir
    if not fixture_dir.is_dir():
        print(f"ERROR: fixture directory not found: {fixture_dir}", file=sys.stderr)
        return 1

    fixtures = load_fixtures(fixture_dir)
    if not fixtures:
        print(f"ERROR: no fixtures found in {fixture_dir}", file=sys.stderr)
        return 1

    print(f"Loaded {len(fixtures)} fixtures from {fixture_dir}")
    report = run_benchmark(fixtures)

    output_path: Path = args.output or (
        _DEFAULT_REPORT_DIR / f"{report['run_date']}-ciqs-cat-d.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nResults:")
    print(f"  edges=off  MRR={report['edges_off']['mrr']:.4f}  "
          f"hit@1={report['edges_off']['hit_at_1']:.3f}  "
          f"hit@3={report['edges_off']['hit_at_3']:.3f}  "
          f"hit@5={report['edges_off']['hit_at_5']:.3f}")
    print(f"  edges=on   MRR={report['edges_on']['mrr']:.4f}  "
          f"hit@1={report['edges_on']['hit_at_1']:.3f}  "
          f"hit@3={report['edges_on']['hit_at_3']:.3f}  "
          f"hit@5={report['edges_on']['hit_at_5']:.3f}")
    print(f"  delta_mrr_pp = {report['delta_mrr_pp']:.2f}pp")
    print(f"  S-50 AC-3 met: {report['s50_ac3_met']}")
    print(f"\nReport written to: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
