---
**Document Control**

| Field | Value |
|---|---|
| Project | DepthFusion |
| Document | Embedding A/B Benchmark |
| Author | Greg Morris |
| Version | v1.0 |
| Date | 05-08-2026 |
| Status | Draft |
---

# DepthFusion — Embedding A/B Benchmark

## Overview

Standalone embedding model comparison using a deterministic synthetic corpus
of agent-memory-style items (code snippets, decisions, error traces, config facts).
Corpus: **191 documents**, Queries: **50**.
No API key required — sentence-transformers local models only. (S-249)

## Corpus & Query Design

- ~200 documents across 4 categories: code snippets, decisions, error traces, config facts
- ~50 queries with gold-relevance labels (category-level and item-specific)
- Deterministic RNG seed: 42 — fully reproducible, no external data dependency

## Results

| Metric | Model A | Model B | Delta (B−A) |
|---|---|---|---|
| **Model** | `all-MiniLM-L6-v2` | `all-mpnet-base-v2` | — |
| Recall@1 | 25.45% | 27.45% | +2.00pp |
| Recall@3 | 55.51% | 57.49% | +1.98pp |
| Recall@5 | 58.25% | 58.41% | +0.16pp |
| MRR | 92.70% | 95.03% | +2.33pp |
| NDCG@10 | 73.49% | 75.13% | +1.64pp |
| Embed time (corpus) | 0.26s | 0.31s | — |

## Interpretation

- **Recall@k**: fraction of relevant documents retrieved in top-k (higher is better)
- **MRR**: reciprocal rank of the first relevant hit (higher is better)
- **NDCG@10**: normalized discounted cumulative gain at depth 10 (higher is better)
- **pp** = percentage points absolute difference

## Reproduction

```bash
python scripts/embedding_ab_benchmark.py \
    --model-a all-MiniLM-L6-v2 \
    --model-b all-mpnet-base-v2
```

---

## Version History

| Version | Date | Author | Changes |
|---|---|---|---|
| v1.0 | 05-08-2026 | Greg Morris | Initial release |