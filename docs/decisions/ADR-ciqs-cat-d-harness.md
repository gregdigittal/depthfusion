# ADR: CIQS Category D Benchmark Harness (E-40 / S-125)

**Status:** Accepted  
**Date:** 2026-05-19  
**Deciders:** depthfusion team  

---

## Context

S-50 AC-3 requires proof that PRECEDED_BY temporal graph edges raise CIQS Category D
("recent work" questions) by ≥ +2 points. S-63 AC-4 executed a real-session benchmark
(2026-05-17) and found the quality graph had 0 nodes/0 edges, so PRECEDED_BY edges
contributed nothing. The criterion remains unmet. A reproducible harness is needed that
(a) does not depend on a live populated graph and (b) isolates the PRECEDED_BY mechanism.

---

## Decision

### 1. Invocation method: direct import, not subprocess

The harness imports `depthfusion.graph.traverser.traverse`,
`depthfusion.graph.store.JSONGraphStore`, and `depthfusion.retrieval.hybrid` directly.
No subprocess to the MCP server.

**Why:** Server startup requires `~/.claude/depthfusion-metrics/` to be writable and loads
many optional dependencies (haiku, vector, etc.). Direct import avoids all of that and keeps
the harness deterministic and offline.

**Trade-off:** Tests the retrieval components in isolation, not end-to-end. End-to-end
tests already exist in `tests/test_mcp/test_tool_recall.py`. The harness measures the
*component* delta of adding graph traversal to BM25 recall for temporal queries.

### 2. Graph toggle: two configurations, not an env-var flip

The two configurations are:
- **`edges=off`**: graph store contains session entities but NO PRECEDED_BY edges
- **`edges=on`**: same entities with PRECEDED_BY edges fully populated

The harness constructs both graphs explicitly per fixture rather than toggling
`DEPTHFUSION_TEMPORAL_SESSION_LINKER_ENABLED`. 

**Why:** The env-var controls whether `auto_learn` *creates* edges during capture, not
whether edges are *used* during recall. The mechanism under test is traversal, not capture.
Toggling the var would measure the capture pipeline, not the retrieval quality effect.

### 3. Fixture format

File: `tests/fixtures/ciqs_cat_d/*.jsonl` — one file per fixture set.

Each line:

```json
{
  "query": "what was recently implemented in the auth module",
  "description": "temporal context Q — requires PRECEDED_BY traversal",
  "session_graph": {
    "entities": [
      {"session_id": "sess-2026-04-10", "project": "myapp", "summary": "implemented JWT refresh ..."},
      {"session_id": "sess-2026-04-11", "project": "myapp", "summary": "added auth middleware ..."}
    ],
    "edges": [
      {"source": "sess-2026-04-11", "target": "sess-2026-04-10", "relationship": "PRECEDED_BY", "delta_hours": 18.0}
    ]
  },
  "corpus": [
    {"chunk_id": "sess-2026-04-10#0", "source": "session", "content": "## Auth Refresh\n\nImplemented JWT refresh token rotation ..."},
    {"chunk_id": "sess-2026-04-11#0", "source": "session", "content": "## Auth Middleware\n\nAdded Express auth middleware with token validation ..."},
    {"chunk_id": "unrelated-docs#0", "source": "memory", "content": "## General documentation ..."}
  ],
  "relevant_chunk_ids": ["sess-2026-04-10#0", "sess-2026-04-11#0"]
}
```

The `session_graph` field defines the graph topology for the `edges=on` configuration.
The `corpus` is the retrieval corpus. The `relevant_chunk_ids` are the ground-truth answers.

### 4. Scoring: MRR + hit@k (k=1,3,5)

- **MRR (Mean Reciprocal Rank)**: primary metric, directly comparable to the CIQS framework
- **hit@1, hit@3, hit@5**: secondary metrics for top-k reporting

Aggregate score = MRR × 100 (maps to 0–100 CIQS scale).

The delta gate for AC-3: aggregate MRR score with `edges=on` must exceed `edges=off` by ≥ +2pp.

**Why MRR:** The existing CIQS framework uses normalized score percentages. MRR×100 provides
a directly comparable scale. Hit@k answers "does the harness surface the relevant block at all."

### 5. Report format

`docs/benchmarks/YYYY-MM-DD-ciqs-cat-d.json`:

```json
{
  "run_date": "2026-05-19",
  "fixture_count": 12,
  "edges_off": {"mrr": 0.42, "hit_at_1": 0.33, "hit_at_3": 0.58, "hit_at_5": 0.67},
  "edges_on":  {"mrr": 0.61, "hit_at_1": 0.50, "hit_at_3": 0.75, "hit_at_5": 0.83},
  "delta_mrr_pp": 19.0,
  "s50_ac3_met": true,
  "per_fixture": [...]
}
```

---

## Consequences

- The harness runs offline (no API keys, no server process) — suitable for CI
- The quality delta is a lower bound on the real improvement: BM25 recall on synthetic
  content is simpler than real production recall with embeddings and reranking
- A ≥ +2pp delta on the harness provides strong evidence for S-50 AC-3 even if the
  real production run requires a populated graph
- When the graph is populated in production, the harness delta should be reproducible

---

## Related

- S-50 AC-3: original criterion (PRECEDED_BY edges → +2pp Cat D)
- S-63 AC-4: benchmark execution that found 0 graph nodes
- `tests/test_graph/test_temporal_session_linker.py`: unit tests for PRECEDED_BY creation
- `src/depthfusion/graph/traverser.py`: traversal engine under test
- `src/depthfusion/retrieval/hybrid.py`: BM25 retrieval under test
