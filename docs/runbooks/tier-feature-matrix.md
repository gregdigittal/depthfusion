# DepthFusion Tier Feature Matrix

> **Reference for E-28 audit findings (2026-05-01).**
> Explains which subsystems engage on each deployment tier and what flags are required.

## Tiers

| Tier | `DEPTHFUSION_MODE` | Detection |
|------|--------------------|-----------|
| `local` | `local` (default) | No TierManager needed |
| `vps-tier1` | `vps` | TierManager reports Tier.VPS_TIER1 or fallback |
| `vps-tier2` | `vps` | TierManager reports Tier.VPS_TIER2 |

## Feature Engagement by Tier

### Recall pipeline (`recall_relevant`)

| Layer | local | vps-tier1 | vps-tier2 | Required flag(s) |
|-------|-------|-----------|-----------|------------------|
| BM25 + RRF | ✅ always | ✅ always | ✅ always | — |
| Haiku reranker | ❌ | ✅ | ✅ | `DEPTHFUSION_HAIKU_ENABLED=true` + `DEPTHFUSION_API_KEY` |
| Graph query expansion | opt-in | opt-in | opt-in | `DEPTHFUSION_GRAPH_ENABLED=true` **and graph non-empty** |
| Vector/embedding search | opt-in | opt-in | opt-in | `DEPTHFUSION_VECTOR_SEARCH_ENABLED=true` **and** `DEPTHFUSION_EMBEDDING_BACKEND=local` |
| Fusion gates (Mamba B/C/Δ) | opt-in | opt-in | opt-in | `DEPTHFUSION_FUSION_GATES_ENABLED=true` |

### Capture pipeline (`auto_learn`)

| Mechanism | local | vps-tier1 | vps-tier2 | Required flag(s) |
|-----------|-------|-----------|-----------|------------------|
| Heuristic extractor | ✅ always | ✅ always | ✅ always | — |
| Haiku summarizer | ❌ | opt-in | opt-in | `DEPTHFUSION_HAIKU_ENABLED=true` |
| Decision extractor | ❌ | opt-in | opt-in | `DEPTHFUSION_DECISION_EXTRACTOR_ENABLED=true` |
| Graph entity extraction | ❌ | opt-in | opt-in | `DEPTHFUSION_GRAPH_ENABLED=true` **and** `DEPTHFUSION_HAIKU_ENABLED=true` |

## S-74 Finding: Graph always empty on vps-tier1 (2026-04-29 audit)

**Root cause:** `summarize_and_extract_graph()` existed in `capture/auto_learn.py` but was
never called from `_tool_auto_learn` in `mcp/server.py`. The compressor ran, but graph
extraction never fired regardless of env flags.

**Fix (S-74, shipped 2026-05-01):** `_tool_auto_learn` now calls `summarize_and_extract_graph`
after each successful compression when `DEPTHFUSION_GRAPH_ENABLED=true`.

**Additional gate discovered:** Graph extraction also requires `DEPTHFUSION_HAIKU_ENABLED=true`
because the entity extractor uses `HaikuExtractor`. On vps-tier1 without Haiku,
`graph_status` returns `extraction_active: false` to surface this clearly.

## S-75 Finding: Vector search silent no-op on vps-tier1 (2026-04-29 audit)

**Root cause:** This is **by design**. Two flags are required:

1. `DEPTHFUSION_EMBEDDING_BACKEND=local` — loads the embedding model
2. `DEPTHFUSION_VECTOR_SEARCH_ENABLED=true` — activates the search path in recall

Setting only `DEPTHFUSION_EMBEDDING_BACKEND=local` loads the model but does not activate
vector search. This two-flag design allows operators to pre-load the model
(warmup) without enabling search — useful during staged rollouts.

**Diagnosis:** The live vps-tier1 deployment had `DEPTHFUSION_EMBEDDING_BACKEND=local` set
but `DEPTHFUSION_VECTOR_SEARCH_ENABLED` was absent (default `false`).

**Resolution:** No code change — by design. To enable vector search:
```bash
export DEPTHFUSION_EMBEDDING_BACKEND=local
export DEPTHFUSION_VECTOR_SEARCH_ENABLED=true
```

**Introspection:** Use `depthfusion_describe_capabilities` to check which layers are engaged
without reading source code. Use `recall_relevant` and inspect the `engaged_layers` field
in the response to confirm what ran.
