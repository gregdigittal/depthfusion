# Benchmark: standard vs research — goldset v2

**Date:** 2026-07-11  
**Goldset:** `tests/fixtures/recall_goldset_v2.jsonl` (200 entries)  
**Tool:** `scripts/benchmark.py --goldset ... --quiet`

## Results

| Metric | standard | research | delta |
|--------|----------|----------|-------|
| MRR@10 | **1.0000** | **1.0000** | +0.0000 |
| nDCG@5 | **0.9934** | **0.9934** | +0.0000 |
| precision@1 | **1.0000** | **1.0000** | +0.0000 |
| hit_rate@5 | **1.0000** | **1.0000** | +0.0000 |
| fallback_rate | 0.0000 | 0.0000 | +0.0000 |
| p50_latency_ms | measured | measured | — |
| cost_estimate_usd | 0.0000 | 0.0000 | — |

## Notes

- Both profiles run BM25-local retrieval in this environment (no GPU/OpenAI key).
- The `--mode` flag is informational; cognitive scoring differences manifest at runtime
  on a server with `DEPTHFUSION_EMBEDDING_BACKEND=openai` or `=cohere`.
- nDCG@5 < 1.0 (0.9934) reflects entries where the secondary (grade-1) chunk was not
  retrieved in the top-5 — primary (grade-2) chunks are always retrieved.
- Detailed per-query results: `docs/benchmarks/2026-07-11-standard-goldset-v2.json`
  and `docs/benchmarks/2026-07-11-research-goldset-v2.json`.
