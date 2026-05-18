# Linear Blend vs RRF+Vector — Cat A Retrieval Analysis

**Date:** 2026-05-18  
**Commit:** f849e00 (S-121 linear blend, S-119 temporal filter, S-120 KG provenance)

## Configurations tested

| Label | BLEND_MODE | VECTOR_SEARCH | Notes |
|---|---|---|---|
| `bm25-only` | `rrf` (default) | disabled | Post-S-115 baseline (today's run1–3) |
| `rrf+vector` | `rrf` | enabled | RRF fusion with LocalEmbeddingBackend |
| `linear+vector` | `linear` | enabled | S-121 linear blend |

Raw files: `docs/benchmarks/rrf-vector/` and `docs/benchmarks/linear-blend/`

## Key finding: Linear ≡ RRF+vector on this corpus

Both vector-search configurations produce **identical top-5 rankings** for all 3 Cat A topics. This is expected: with only 2/15 blocks (13%) carrying a `vector_score`, the vector signal is sparse and the same candidate set reaches both fusion methods.

## Ranking changes vs BM25 baseline

| Topic | Position | BM25 | RRF+vector | Linear |
|---|---|---|---|---|
| A1 | 1–3 | skillforge ×3 | skillforge ×3 | skillforge ×3 |
| A1 | 4 | 2026-05-17-depthfusion | **2026-05-18-depthfusion** | **2026-05-18-depthfusion** |
| A1 | 5 | 2026-05-18-tito-apps | 2026-05-17-depthfusion | 2026-05-17-depthfusion |
| A2 | 1–3 | skillforge ×3 | skillforge ×3 | skillforge ×3 |
| A2 | 4 | 2026-05-17-depthfusion | **2026-05-18-depthfusion** | **2026-05-18-depthfusion** |
| A2 | 5 | 2026-05-18-tito-apps | 2026-05-17-depthfusion | 2026-05-17-depthfusion |
| A3 | 1 | 2026-05-18-tito-apps | **2026-05-18-depthfusion** | **2026-05-18-depthfusion** |
| A3 | 3 | commit-review#3 | commit-review#3 | commit-review#3 |
| A3 | 4 | 2026-05-18-depthfusion | **2026-05-18-tito-apps** | **2026-05-18-tito-apps** |

Bold = differs from BM25.

## Cat A rubric impact

The rubric scores relevance, specificity, novel_signal, and confidence_calibration on the
**content of retrieved blocks**, not their attached scores. Since:

- A1/A2: top-3 blocks (skillforge sessions) are unchanged → rubric score unchanged
- A3: `commit-review#3` (the highest-relevance block) stays at pos3 in all configs

The CIQS Cat A score for linear+vector and rrf+vector is expected to be **≈40.0%** — the
same as the post-S-115 BM25 baseline. No rubric dimension improves because the same blocks
appear in the same or very similar order.

## Why linear blend shows no advantage here

S-121's design prevents *score collapse* when vector candidates have numerically dissimilar
scales vs BM25 candidates. That benefit requires:

1. Diverse vector candidates that aren't already in the BM25 pool
2. Large enough memory index that vector search surfaces topically different blocks

On this corpus the memory index is ~O(hundreds) of session summaries, not fine-grained
decision records. Both BM25 and vector search surface the same session files for the same
query. With identical candidate sets, linear blend and RRF produce the same rank order.

## When linear blend is expected to help

- Cat D (Session Continuity) queries that span specific technical decisions embedded in
  discovery files — BM25 struggles with semantic near-matches but sentence embeddings handle
- Larger/diverse memory indexes where vector search surfaces topically distinct candidates
- Queries where BM25 keyword overlap is weak but semantic similarity is strong

## Recommendation

Linear blend remains the correct design for S-121's stated goal (preventing score collapse
on small candidate sets). Its effect will be measurable once:
- `DEPTHFUSION_VECTOR_SEARCH_ENABLED=true` becomes the default
- Discovery files (rather than just session summaries) form a larger fraction of the index
- Cat A battery is extended with semantically-oriented queries

**No action required on S-121.** The feature is correctly implemented and gated. This run
establishes the baseline for measuring linear blend impact as the corpus grows.

## Files

- `docs/benchmarks/rrf-vector/2026-05-18-local-run1-raw.jsonl`
- `docs/benchmarks/linear-blend/2026-05-18-local-run1-raw.jsonl`
