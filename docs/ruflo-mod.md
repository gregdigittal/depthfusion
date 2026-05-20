# DepthFusion HNSW Implementation Handoff

> Audience: DepthFusion maintainers
> Last updated: 2026-05-20
> Contract version: 1.0.0

This document tells you exactly what to implement in DepthFusion to satisfy the
`@agent-ops/df-contract` package. Read this before touching the DepthFusion
embedding or recall code.

---

## What this is

The agent-ops bridge and DepthFusion share a typed contract via
`packages/df-contract/`. The contract defines:

- What agent-ops sends when publishing context (`PublishContextPayload`)
- What DepthFusion must return after indexing (`PublishContextResult`)
- What agent-ops sends on recall (`RecallRequest`)
- What DepthFusion must return on recall (`RecallResult`, `RecallHit`)
- The HNSW capability shape the bridge reads at startup (`HNSWCapability`, `HNSWState`)

If DepthFusion deviates from these shapes, the bridge will fail at compile time (TypeScript) or
at runtime (shape mismatch on the MCP tool boundary).

---

## Linking the contract package locally

DepthFusion lives on the same VPS as agent-ops. Use a local path reference rather than
publishing to npm during development.

In DepthFusion's `package.json`:

```json
{
  "dependencies": {
    "@agent-ops/df-contract": "file:../../agent-ops/packages/df-contract"
  },
  "peerDependencies": {
    "@agent-ops/df-contract": "1.0.0"
  }
}
```

Then run `npm install` (or `pnpm install`) inside the DepthFusion repo.

The version check script at `agent-ops/scripts/check-df-contract-version.js` reads
DepthFusion's `peerDependencies` entry and compares it to `DF_CONTRACT_VERSION` in
`packages/df-contract/src/version.ts`. Run it in CI:

```bash
node scripts/check-df-contract-version.js
# exit 0 = match or DF not installed; exit 1 = confirmed mismatch
```

---

## Required environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DEPTHFUSION_HNSW_ENABLED` | No | `false` | Feature flag — set to `true` to activate HNSW indexing and fusion recall |
| `DEPTHFUSION_HNSW_INDEX_PATH` | When enabled | `~/.agent-mc/depthfusion/hnsw.bin` | Filesystem path where the serialised HNSW index is stored and loaded |
| `DEPTHFUSION_EMBEDDING_MODEL` | When enabled | `Xenova/all-MiniLM-L6-v2` | HuggingFace model identifier used by `@xenova/transformers` or `onnxruntime-node` |

When `DEPTHFUSION_HNSW_ENABLED=false` (the default), DepthFusion must still respond to all
MCP tool calls — just fall back to BM25-only behaviour and return `hnsw_available: false`
in every `RecallResult`.

---

## Contract types you must implement against

These are reproduced from `packages/df-contract/src/` for reference. The TypeScript source
is authoritative — this prose is explanatory only.

### HNSWState (persisted to disk)

```ts
interface HNSWState {
  schema_version: 1;          // bump this when the serialisation format changes
  index_path: string;         // absolute path to the .bin file
  embedding_model: string;    // model string used when the index was built
  dimension: number;          // must match the model's output dimension
  entry_count: number;        // number of vectors currently indexed
  last_updated: string;       // ISO-8601 timestamp of last upsert
}
```

Store this as a sidecar JSON file at `${HNSW_INDEX_PATH}.meta.json` alongside the binary
index. The bridge reads it at startup via `depthfusion_hnsw_capability` to populate
`HNSWCapability`.

### HNSWCapability (returned to the bridge at startup)

```ts
interface HNSWCapability {
  enabled: boolean;
  backend: 'local' | 'openai' | 'none';
  model: string;
  dimension: number;
  index_path: string;
  entry_count: number;
}
```

The bridge calls a `depthfusion_hnsw_capability` MCP tool at startup. Return this shape.
If `DEPTHFUSION_HNSW_ENABLED=false`, return `{ enabled: false, backend: 'none', model: '', dimension: 0, index_path: '', entry_count: 0 }`.

---

## API surface you must implement

### 1. `depthfusion_publish_context` — embed and index on publish

When the bridge calls `depthfusion_publish_context` with a `PublishContextPayload`:

1. Persist the content to BM25 store as you do today.
2. If `DEPTHFUSION_HNSW_ENABLED=true`:
   a. Embed `payload.content` using the configured model.
   b. Upsert the vector into the HNSW index keyed by `discovery_id` (the Supabase row ID returned after BM25 insert).
   c. Update `HNSWState.entry_count` and `HNSWState.last_updated`.
3. Return `PublishContextResult`:
   ```ts
   { discovery_id: string; indexed_in_hnsw: boolean; }
   ```
   Set `indexed_in_hnsw: true` only when the HNSW upsert succeeded.

If the HNSW upsert fails for any reason, log the error, set `indexed_in_hnsw: false`, and
return normally. Never let an HNSW failure block the BM25 publish path.

### 2. `depthfusion_recall_relevant` — fuse BM25 + HNSW on recall

When the bridge calls `depthfusion_recall_relevant` with a `RecallRequest`:

If `DEPTHFUSION_HNSW_ENABLED=false` or the index is empty:
- Run BM25 only.
- Return `RecallResult` with `strategy: 'bm25-only'` and `hnsw_available: false`.

If HNSW is available:
1. Embed `request.query`.
2. Run BM25 search: fetch top `(limit ?? 10) * 2` candidates.
3. Run HNSW cosine search: fetch top `(limit ?? 10) * 2` candidates.
4. Fuse scores: `final_score = 0.6 * bm25_score + 0.4 * hnsw_cosine_score`.
5. Deduplicate by `discovery_id`, keep highest fused score.
6. Sort descending, slice to `limit ?? 10`.
7. Apply `min_score` filter if provided.
8. For each hit, set `source` to:
   - `'bm25'` if only BM25 found it
   - `'hnsw'` if only HNSW found it
   - `'fused'` if both found it
9. Return `RecallResult` with `strategy: 'fused'` and `hnsw_available: true`.

The `tags` filter in `RecallRequest` applies to the BM25 path (SQL WHERE). For HNSW,
post-filter by tags after retrieval.

### 3. `depthfusion_hnsw_capability` — report current HNSW state

New MCP tool. Return the `HNSWCapability` shape described above. Called once by the bridge at
startup; the bridge caches the result and does not poll.

---

## Recommended library: hnswlib-node

Use `hnswlib-node` for the HNSW index. It is pure JavaScript with no native compilation
dependency, which avoids platform-specific build failures on the VPS.

```bash
npm install hnswlib-node
```

Basic usage pattern:

```ts
import { HierarchicalNSW } from 'hnswlib-node';

// Initialize or load
const index = new HierarchicalNSW('cosine', dimension);
if (existsSync(indexPath)) {
  index.readIndex(indexPath);
} else {
  index.initIndex(maxElements);
}

// Upsert (add or replace by label)
index.addPoint(vector, labelAsNumber);

// Query
const result = index.searchKnn(queryVector, k);
// result.neighbors = number[] (labels), result.distances = number[]

// Persist
index.writeIndex(indexPath);
```

Label mapping: since HNSW labels are integers, maintain a sidecar map
`discovery_id (string) → label (number)` in a JSON file at
`${HNSW_INDEX_PATH}.labels.json`. On load, restore the map; on upsert, assign
`label = current_entry_count` for new entries, or reuse the existing label for updates.

Set `maxElements` to a generous initial capacity (e.g. 50000). `hnswlib-node` supports
`resizeIndex(newMax)` if the index fills up.

---

## Embedding model: all-MiniLM-L6-v2

Use `@xenova/transformers` (preferred, pure JS) or `onnxruntime-node` with the
`all-MiniLM-L6-v2` model. Output dimension is 384.

```bash
npm install @xenova/transformers
```

```ts
import { pipeline } from '@xenova/transformers';

const embedder = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2');

async function embed(text: string): Promise<number[]> {
  const output = await embedder(text, { pooling: 'mean', normalize: true });
  return Array.from(output.data as Float32Array);
}
```

The first call downloads the model to `~/.cache/huggingface/`. On a VPS without egress,
pre-download with `npx @xenova/transformers download Xenova/all-MiniLM-L6-v2` or bundle
the ONNX weights manually.

If you prefer OpenAI embeddings (`text-embedding-3-small`, 1536 dims), set
`DEPTHFUSION_EMBEDDING_MODEL=openai/text-embedding-3-small` and handle in a model-router
branch. Update `HNSWCapability.backend` to `'openai'` and `dimension` to 1536. The HNSW
index must be rebuilt if the model changes.

---

## Startup sequence

```
1. Read DEPTHFUSION_HNSW_ENABLED
   └─ false → skip HNSW init entirely; proceed with BM25-only mode

2. Resolve DEPTHFUSION_HNSW_INDEX_PATH (expand ~ if present)

3. If index file exists at path:
   a. Call index.readIndex(path)
   b. Load label map from path + '.labels.json'
   c. Load HNSWState from path + '.meta.json'
   d. Log: "[hnsw] loaded index: ${entry_count} entries, model=${model}"

4. If index file does not exist:
   a. Call index.initIndex(maxElements)
   b. Initialise empty label map
   c. Create fresh HNSWState with entry_count=0
   d. Log: "[hnsw] fresh index initialised"

5. Warm the embedder (embed a dummy string to force model download/load):
   await embed('warmup');

6. Set hnsw_ready = true
```

If any step throws, log the error, set `hnsw_ready = false`, and continue in BM25-only
mode. Never crash the DepthFusion process over an HNSW init failure.

---

## Graceful shutdown

On `SIGTERM` or `SIGINT`:

```ts
process.on('SIGTERM', async () => {
  if (hnsw_ready) {
    index.writeIndex(indexPath);
    writeFileSync(indexPath + '.labels.json', JSON.stringify(labelMap));
    writeFileSync(indexPath + '.meta.json', JSON.stringify(currentState));
    console.log('[hnsw] index persisted on shutdown');
  }
  process.exit(0);
});
```

Also persist after every N upserts (recommend N=100) to limit data loss on unclean shutdown.
The `last_updated` field in `HNSWState` tells the bridge how stale the persisted index is.

---

## Feature flag migration path

1. Deploy DepthFusion with `DEPTHFUSION_HNSW_ENABLED=false` (default).
   All recall uses BM25. Bridge sees `hnsw_available: false`. No behaviour change.

2. Enable on the VPS: `DEPTHFUSION_HNSW_ENABLED=true`.
   DepthFusion starts indexing new publishes into HNSW.
   Old content is BM25-only until a backfill runs.

3. Optionally run a backfill script that reads all existing Supabase discoveries and
   upserts their embeddings into the index. Write this as a one-off script, not part
   of the startup path.

4. Once the index has enough entries to be useful (~500+), switch recall to `'fused'`.
   Monitor quality: if fused recall scores are worse than BM25-only, flip back via env var.

---

## Fallback contract (mandatory)

Regardless of internal state, DepthFusion's MCP tool responses must always match the
contract types. The key fallback invariants:

- `depthfusion_recall_relevant` ALWAYS returns a valid `RecallResult`.
  Never return `null`, `undefined`, or an incomplete shape.
- When HNSW is unavailable: `{ ..., strategy: 'bm25-only', hnsw_available: false }`.
- When BM25 has no results and HNSW is unavailable: `{ hits: [], query, strategy: 'bm25-only', hnsw_available: false }`.
- `RecallHit.source` must always be one of `'bm25' | 'hnsw' | 'fused'`. Never omit it.
- `PublishContextResult.indexed_in_hnsw` must be a boolean, never undefined.

The bridge's TypeScript types will enforce these shapes at compile time. If DepthFusion
returns a malformed shape, the bridge surfaces a typed error, not a silent miss.

---

## Version bump protocol

When a breaking change to the contract is required (new required field, removed field,
changed type):

1. In `packages/df-contract/src/version.ts`, increment `DF_CONTRACT_VERSION`.
   Use semver: `1.0.0` → `1.1.0` for additive changes, `2.0.0` for breaking ones.

2. Update the types in the affected `.ts` files under `packages/df-contract/src/`.

3. In DepthFusion's `package.json`, update `peerDependencies['@agent-ops/df-contract']`
   to the new version.

4. Run the version gate on both repos:
   ```bash
   # in agent-ops:
   node scripts/check-df-contract-version.js
   ```

5. Update DepthFusion's implementation to satisfy the new shape.

6. Commit agent-ops and DepthFusion in the same logical change (they can be separate
   commits but should land together before any CI runs the version gate).

Never increment the contract version without updating DepthFusion's peer dependency
declaration — the CI gate will block the agent-ops build.

---

## How agent-ops validates the contract at bridge startup

The bridge executes the following at startup (pseudocode):

```ts
import { DF_CONTRACT_VERSION } from '@agent-ops/df-contract';

const capability = await mcpClient.call('depthfusion_hnsw_capability', {});
// capability is typed as HNSWCapability — TypeScript enforces shape at compile time

if (capability.enabled && capability.entry_count === 0) {
  console.warn('[bridge] HNSW enabled but index is empty — fused recall will degrade to BM25');
}

console.log(`[bridge] df-contract ${DF_CONTRACT_VERSION} — hnsw=${capability.enabled}, entries=${capability.entry_count}`);
```

The version check script (`scripts/check-df-contract-version.js`) is a separate CI gate
that runs before the bridge starts. It exits 1 on mismatch and 0 on match or when
DepthFusion is not installed (non-blocking for local dev without DepthFusion).

---

## Summary checklist for DepthFusion implementers

- [ ] Add `@agent-ops/df-contract` as a local path dependency in `package.json`
- [ ] Declare `peerDependencies['@agent-ops/df-contract']: '1.0.0'`
- [ ] Implement `depthfusion_hnsw_capability` MCP tool returning `HNSWCapability`
- [ ] Implement HNSW upsert in `depthfusion_publish_context` (behind feature flag)
- [ ] Implement fused recall in `depthfusion_recall_relevant` (behind feature flag)
- [ ] Return `strategy` and `hnsw_available` fields in all `RecallResult` responses
- [ ] Return `indexed_in_hnsw` boolean in all `PublishContextResult` responses
- [ ] Persist index on graceful shutdown
- [ ] Load index on startup if file exists
- [ ] Gracefully degrade to BM25-only on any HNSW failure
- [ ] Run `node scripts/check-df-contract-version.js` in DepthFusion's CI
