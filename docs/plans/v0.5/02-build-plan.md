# DepthFusion v0.5 — Phase 2 Build Plan

> **Status:** Draft for Greg's review. Every task group has acceptance criteria; every flag has a default.
> **Scope:** executable by a single Claude Code worker against `~/depthfusion/` over ~3–4 weeks of focused work. No task group here breaks the "FLAG=false byte-identical" rule.
> **Depends on:** `01-assessment.md` (15-feature ranked list + 5 caveats)
> **Generated:** 2026-04-17

---

## 2.1 Guardrails (verbatim, non-negotiable)

- `FLAG=false` must produce byte-identical output to v0.4.x for every new flag. `[depthfusion-handoff-context.md §16]`
- C1–C11 compatibility must stay GREEN. `[depthfusion-handoff-context.md §16]`
- Never use `ANTHROPIC_API_KEY`; use `DEPTHFUSION_API_KEY` only. `[depthfusion-handoff-context.md §16]`
- structlog only, no `print()` in production code (except installer CLI output). `[depthfusion-handoff-context.md §16]`
- Type hints on all public functions. `[depthfusion-handoff-context.md §16]`
- All 439 existing tests must still pass. `[depthfusion-handoff-context.md §12]` No test is allowed to be deleted or `@pytest.skip`-ed without an explicit migration note linking to the task group that justifies it.
- No hook script may exceed 4s timeout. `[depthfusion-handoff-context.md §16]`
- Every new feature ships with a CIQS benchmark run `[handoff §13]` showing no regression >2 points on any category vs v0.4.x baseline.

Caveats rolled forward from Phase 1:
- **C1:** backend interface covers all 4 LLM call-sites; not reranker-only.
- **C2:** `graph/linker.py:112` `anthropic.Anthropic()` (bare-client latent issue) must be migrated through the new backend interface.
- **C3:** SAIHAI invariant gap (9 of 15 visible) — handed off to Phase 3 for resolution.
- **C4:** Gemma variant pinned at provisioning, not in this plan; Phase 2 proposes Gemma 3 12B Q4-AWQ on vLLM as the default.
- **C5:** every TG below includes a CIQS-benchmark acceptance criterion.

---

## 2.2 Foundational refactor — TG-01: Backend Provider Interface

This is the first task group because TG-02, TG-04, TG-05, TG-06, TG-08, TG-10, TG-11, TG-12 all depend on it.

### 2.2.1 Protocol

New module `src/depthfusion/backends/base.py`:

```python
# SIGNATURE — not implementation
class LLMBackend(Protocol):
    name: str                       # "haiku" | "gemma" | "null"
    def complete(self, prompt: str, *, max_tokens: int, system: str | None = None) -> str: ...
    def embed(self, texts: list[str]) -> list[list[float]] | None: ...
    def rerank(self, query: str, docs: list[str], top_k: int) -> list[tuple[int, float]]: ...
    def extract_structured(self, prompt: str, schema: dict) -> dict | None: ...
    def healthy(self) -> bool: ...
```

Concrete implementations (each in its own file):

- `backends/haiku.py::HaikuBackend` — reads `DEPTHFUSION_API_KEY`; SDK wrapper; classifies 429 / 529 / timeout as typed errors; **fixes C2** by forcing explicit `api_key=` in every client constructor.
- `backends/gemma.py::GemmaBackend` — vLLM HTTP client; config: `DEPTHFUSION_GEMMA_URL` (default `http://127.0.0.1:8000/v1`), `DEPTHFUSION_GEMMA_MODEL` (default `google/gemma-3-12b-it-AWQ`).
- `backends/null.py::NullBackend` — returns empty results, always `healthy()=True`. Used on `local` mode without an API key.
- `backends/local_embedding.py::LocalEmbeddingBackend` — sentence-transformers wrapper (only implements `embed()`; other methods raise `NotImplementedError`). Used as a sub-backend by `HaikuBackend` / `GemmaBackend` composition for the embedding capability.

### 2.2.2 Factory

`backends/factory.py::get_backend(capability: str, config: DepthFusionConfig) -> LLMBackend`

Dispatch table per capability, per mode:

| Capability | `local` | `vps-cpu` | `vps-gpu` |
|---|---|---|---|
| `reranker` | Null (or Haiku if DEPTHFUSION_API_KEY) | Haiku | Gemma → Haiku fallback |
| `extractor` | Null → heuristic | Haiku → heuristic | Gemma → Haiku → heuristic |
| `linker` | Null (disabled) | Haiku | Gemma → Haiku |
| `summariser` | Null → heuristic | Haiku → heuristic | Gemma → Haiku → heuristic |
| `embedding` | Null (disabled) | Null (disabled unless opt-in) | Local sentence-transformers |
| `decision_extractor` (CM-1) | Null → heuristic | Haiku | Gemma → Haiku |

Per-capability override env vars (override the mode default):
- `DEPTHFUSION_RERANKER_BACKEND` ∈ `{null,haiku,gemma}`
- `DEPTHFUSION_EXTRACTOR_BACKEND`
- `DEPTHFUSION_LINKER_BACKEND`
- `DEPTHFUSION_SUMMARISER_BACKEND`
- `DEPTHFUSION_EMBEDDING_BACKEND`
- `DEPTHFUSION_DECISION_EXTRACTOR_BACKEND`

### 2.2.3 Call-site migration

4 sites to rewire `[from Phase 1 §1.2]`:

1. `retrieval/reranker.py:40` — `HaikuReranker.__init__` → accept `backend: LLMBackend` parameter; default `get_backend("reranker", config)`.
2. `graph/extractor.py:116` — `HaikuExtractor.__init__` → same pattern.
3. `graph/linker.py:112` — **C2 fix** — `HaikuLinker.__init__` → same pattern. Bare `anthropic.Anthropic()` call is deleted.
4. `capture/auto_learn.py` `HaikuSummarizer` (`[auto_learn.py:L77]`) → same pattern.

### 2.2.4 Acceptance criteria (TG-01)

- [ ] AC-01-1: `backends/base.py`, `backends/factory.py`, `backends/haiku.py`, `backends/null.py` exist with type hints on every public function.
- [ ] AC-01-2: All 4 LLM call-sites use `get_backend(...)`; no direct `anthropic.Anthropic(...)` call remains in `src/depthfusion/` (grep-verified).
- [ ] AC-01-3: With no new flags set, output of `_tool_recall` on a fixed corpus is byte-identical to v0.4.x (captured via a new regression test `tests/test_regression/test_v04_output_identity.py`).
- [ ] AC-01-4: A 429 rate-limit from Haiku surfaces as `RateLimitError` and triggers the fallback chain; not silently swallowed.
- [ ] AC-01-5: All 439 pre-existing tests pass.
- [ ] AC-01-6: At least 25 new tests in `tests/test_backends/` covering protocol contract, factory dispatch, fallback chain, and C2 fix.
- [ ] AC-01-7: CIQS benchmark run shows no category regression > 2 points vs v0.4.x baseline.
- [ ] AC-01-8: Fallback chain is **quality-ranked**, not cost/latency-ranked — per DR-018 §4 ratification of legacy #5 (OPL unavailable → MAX_QUALITY default) as canonical **I-18**. Specifically: `get_backend(capability, config)` returns a fallback chain in quality-descending order within the set of backends at or above the current tier's `min_quality_score`; cost/latency optimisation may reorder *within* a quality tier but may not move a lower-quality backend ahead of a higher-quality one. Verified by a dedicated test suite in `tests/test_backends/test_fallback_order.py`.

### 2.2.5 New flags (TG-01)

| Env var | Default | Purpose |
|---|---|---|
| `DEPTHFUSION_RERANKER_BACKEND` | (empty = mode default) | Override reranker backend |
| `DEPTHFUSION_EXTRACTOR_BACKEND` | (empty) | Override extractor backend |
| `DEPTHFUSION_LINKER_BACKEND` | (empty) | Override linker backend |
| `DEPTHFUSION_SUMMARISER_BACKEND` | (empty) | Override summariser backend |
| `DEPTHFUSION_EMBEDDING_BACKEND` | (empty) | Override embedding backend |
| `DEPTHFUSION_DECISION_EXTRACTOR_BACKEND` | (empty) | Override decision-extractor backend |
| `DEPTHFUSION_GEMMA_URL` | `http://127.0.0.1:8000/v1` | vLLM endpoint |
| `DEPTHFUSION_GEMMA_MODEL` | `google/gemma-3-12b-it-AWQ` | Model identifier |
| `DEPTHFUSION_BACKEND_FALLBACK_LOG` | `true` | Emit JSONL record to metrics/ whenever a fallback triggers |

### 2.2.6 Kill-criterion

If AC-01-3 (byte-identical) cannot be satisfied despite best effort — i.e. the interface introduces tiny numerical differences via the anthropic-client re-wrap — **TG-01 does not ship**. The entire v0.5 release is blocked; no amount of capture-mechanism work compensates for regressing v0.4.x output.

---

## 2.3 Installer refactor — TG-02: Three-mode installer

### 2.3.1 CLI change

`install/install.py` `argparse` update: `--mode={local,vps-cpu,vps-gpu}` (was `{local,vps}`). `vps` becomes an alias for `vps-cpu` with a deprecation warning, removed in v0.6.

### 2.3.2 GPU probe

New module `install/gpu_probe.py`:

```python
def detect_gpu() -> GPUInfo | None:
    # Try nvidia-smi; parse CUDA capability + VRAM
    # Return None if no GPU or nvidia-smi unavailable
```

On `--mode=vps-gpu`: if `detect_gpu()` returns `None`, **refuse to install** with remediation text pointing to `docs/plans/v0.5/04-rollout-runbook.md §4.2 step 5`.

On `--mode=vps-gpu`: if VRAM < 16 GB, **warn** but proceed — Gemma 3 12B Q4 requires ~7 GB at inference, so 16 GB leaves headroom for concurrency. Below 16 GB the installer suggests falling back to `vps-cpu`.

### 2.3.3 Optional-dependency extras

`pyproject.toml` extras:

| Extra | Pulls in |
|---|---|
| `depthfusion[local]` | (base only) numpy, pyyaml, structlog |
| `depthfusion[vps-cpu]` | base + anthropic + chromadb |
| `depthfusion[vps-gpu]` | base + anthropic + chromadb + torch (CUDA) + sentence-transformers + vllm |

The `vps-gpu` extra has the biggest dep footprint (~5 GB). Phase 4 rollout will verify this does not explode install time past a reasonable threshold.

### 2.3.4 Post-install smoke test

Every `--mode` invocation ends with a smoke test that **runs an actual recall query** (synthetic corpus of 5 memory files) and verifies the configured backends respond. On failure, the installer prints the exact command to debug (`depthfusion.install.install --mode=<mode> --smoke-test-only`).

### 2.3.5 Acceptance criteria (TG-02)

- [ ] AC-02-1: `python -m depthfusion.install.install --mode=vps-gpu` refuses cleanly on a no-GPU host with remediation text.
- [ ] AC-02-2: On a GPU host, `--mode=vps-gpu` writes `~/.claude/depthfusion.env` containing the correct per-capability backend flags.
- [ ] AC-02-3: `--mode=vps` still works (as alias for `vps-cpu`) with a deprecation warning.
- [ ] AC-02-4: Smoke test passes on all three modes (caveat: vps-gpu smoke test skipped in CI unless `DEPTHFUSION_CI_HAS_GPU=true`).
- [ ] AC-02-5: pyproject extras install correctly: `pip install 'depthfusion[vps-gpu]'` resolves with no conflict warnings.
- [ ] AC-02-6: Existing `--mode=local` install produces byte-identical `depthfusion.env` to v0.4.x (regression).

### 2.3.6 Out of scope

**Cross-mode corpus migration** (`--from=local --to=vps-gpu`) is **deferred to v0.6**. v0.5 users installing a new mode start with an empty corpus; the existing `install/migrate.py` handles Tier 1 → Tier 2 within `vps-cpu` only.

---

## 2.4 Feature task groups (TG-03 to TG-15)

The following task groups depend on TG-01 + TG-02 landing first. Each is presented in the schema from the prompt's §2.4.

### TG-03: Local embedding backend

**Source:** new (supports CM-2, enabler for Mamba gates TG-11).
**Environments:** vps-gpu (primary), vps-cpu (opt-in via `DEPTHFUSION_EMBEDDING_BACKEND=local`).
**Depends on:** TG-01.

**Files touched:**
- `src/depthfusion/backends/local_embedding.py` — sentence-transformers wrapper
- `src/depthfusion/retrieval/hybrid.py` — wire embedding into RRF fusion alongside BM25/ChromaDB
- `tests/test_backends/test_local_embedding.py` — new
- `tests/test_retrieval/test_hybrid_with_embeddings.py` — new

**New flags:** `DEPTHFUSION_EMBEDDING_MODEL` (default `sentence-transformers/all-MiniLM-L6-v2` — 80 MB, 384-dim), `DEPTHFUSION_EMBEDDING_TOP_K` (default `20` — candidate set from embedding step fed into reranker).

**Acceptance criteria:**
- [ ] Byte-identical output when `DEPTHFUSION_EMBEDDING_BACKEND` unset
- [ ] CIQS Category A delta ≥ +3 points vs TG-01 baseline on vps-gpu
- [ ] p95 recall latency ≤ 1500ms on vps-gpu with 100-file corpus
- [ ] ≥ 10 new tests

**Kill-criterion:** if embedding retrieval does not beat BM25 alone on the CIQS Category A score, cut from v0.5 and revisit with a different embedding model.

---

### TG-04: Gemma backend for all LLM capabilities

**Source:** new.
**Environments:** vps-gpu only.
**Depends on:** TG-01, TG-02.

**Files touched:**
- `src/depthfusion/backends/gemma.py` — new
- `src/depthfusion/backends/factory.py` — register gemma
- `scripts/vllm-serve-gemma.sh` — new (systemd-friendly launcher)
- `tests/test_backends/test_gemma.py` — new (uses a mock vLLM server)

**New flags:** `DEPTHFUSION_GEMMA_TIMEOUT_SECONDS` (default `30`), `DEPTHFUSION_GEMMA_MAX_CONCURRENT` (default `4`).

**Acceptance criteria:**
- [ ] Backend factory routes all 6 capabilities to Gemma on vps-gpu mode
- [ ] p95 latency per capability measured and recorded in Phase 4 runbook
- [ ] Fallback to Haiku triggers on OOM / 5xx / timeout (integration test with fault-injected mock server)
- [ ] Fallback to Null triggers when Haiku is also unavailable (integration test)
- [ ] ≥ 15 new tests

**Kill-criterion:** p95 rerank latency > 2× Haiku on same corpus — cut TG-04 from v0.5 and leave vps-gpu routing reranker to Haiku (GPU is still used for embeddings).

---

### TG-05: LLM decision extractor (CM-1)

**Source:** CM-1.
**Environments:** all (degrades to heuristic on `local` without API key).
**Depends on:** TG-01.

**Files touched:**
- `src/depthfusion/capture/decision_extractor.py` — new
- `src/depthfusion/capture/auto_learn.py` — wire decision-extractor into `summarize_and_extract_graph()` at `[auto_learn.py:L140]`
- `src/depthfusion/hooks/depthfusion-stop.sh` — new Stop hook (fires on session end) to trigger decision extraction
- `tests/test_capture/test_decision_extractor.py` — new

**New flags:** `DEPTHFUSION_DECISION_EXTRACTION_ENABLED` (default `false` on local, `true` on vps-cpu/vps-gpu via installer).

**Acceptance criteria:**
- [ ] Precision on a labelled eval set of 50 historical sessions ≥ 0.80 (baseline heuristic is ~0.60 per auto_learn extract_key_decisions)
- [ ] Each extracted decision written to `~/.claude/shared/discoveries/{date}-{project}-decisions.md` with frontmatter (`project:`, `session_id:`, `confidence:`) — enables TG-15
- [ ] Idempotent: running twice on same session produces no duplicate entries
- [ ] ≥ 8 new tests

**Kill-criterion:** precision < heuristic baseline on the labelled eval set.

---

### TG-06: Git post-commit hook for capture (CM-3)

**Source:** CM-3.
**Environments:** all.
**Depends on:** none.

**Files touched:**
- `src/depthfusion/hooks/git_post_commit.py` — new (Python script)
- `scripts/install-git-hook.sh` — new (opt-in per project; installs a `.git/hooks/post-commit` that calls the Python script)
- `src/depthfusion/analyzer/installer.py` — extend to document git-hook install as an opt-in step
- `tests/test_hooks/test_git_post_commit.py` — new

**New flags:** `DEPTHFUSION_GIT_HOOK_ENABLED` (default `false` — must be explicitly enabled per project to avoid surprising existing repos).

**Acceptance criteria:**
- [ ] Hook fires on `git commit` and writes `~/.claude/shared/discoveries/{date}-{project}-commit-{sha7}.md` with commit message + file diff summary
- [ ] Idempotent with existing project post-commit hooks (appends, does not overwrite; detects existing DepthFusion block)
- [ ] Hook completes in < 500ms on commits touching ≤ 50 files
- [ ] ≥ 5 new tests

**Kill-criterion:** hook interferes with the user's existing post-commit workflow in any of Greg's projects (social-media-agent, virtual_analyst, agreement_automation, kitabu, skillforge).

---

### TG-07: Active confirmation MCP tool (CM-5)

**Source:** CM-5.
**Environments:** all.
**Depends on:** none.

**Files touched:**
- `src/depthfusion/mcp/server.py` — new tool `depthfusion_confirm_discovery(content: str, suggested_title: str, confidence: float) -> ConfirmationResult`
- `tests/test_mcp/test_confirm_discovery.py` — new

**New flags:** `DEPTHFUSION_CONFIRM_THRESHOLD` (default `0.75` — only call the tool when decision-extractor confidence falls in the band 0.50–0.75; below 0.50 discard, above 0.75 write automatically).

**Acceptance criteria:**
- [ ] Tool returns a structured result the caller can act on (save / discard / edit)
- [ ] Does not block the session on response (async)
- [ ] ≥ 4 new tests

**Kill-criterion:** user dismisses the confirmation prompt > 50% of the time over a 2-week observation period (signal: the user finds the prompt annoying rather than helpful).

---

### TG-08: Negative-signal extractor (CM-6)

**Source:** CM-6.
**Environments:** all.
**Depends on:** TG-05.

**Files touched:**
- `src/depthfusion/capture/negative_extractor.py` — new (sibling to decision_extractor.py)
- `src/depthfusion/capture/auto_learn.py` — wire into `summarize_and_extract_graph()`
- `tests/test_capture/test_negative_extractor.py` — new

**New flags:** `DEPTHFUSION_NEGATIVE_EXTRACTION_ENABLED` (default matches `DEPTHFUSION_DECISION_EXTRACTION_ENABLED`).

**Acceptance criteria:**
- [ ] Extracted negative signals ("X did not work because Y") written with `type: negative` frontmatter, so BM25 can weight them differently in a future release
- [ ] False-negative rate (flagging positives as negatives) ≤ 10% on labelled set
- [ ] ≥ 6 new tests

---

### TG-09: Cross-session dependency edges (CM-4)

**Source:** CM-4 + Phase 1 Gap 4.
**Environments:** all.
**Depends on:** none (graph already present; this is additive).

**Files touched:**
- `src/depthfusion/graph/types.py` — add `PRECEDED_BY` to the EdgeKind literal
- `src/depthfusion/graph/linker.py` — new `TemporalSessionLinker` class that emits `PRECEDED_BY` when two session-derived entities appeared in sessions ≤ 48h apart with overlapping vocabulary
- `src/depthfusion/graph/traverser.py` — time-bucketed traversal respecting the new edge
- `tests/test_graph/test_temporal_session_linker.py` — new

**Acceptance criteria:**
- [ ] New edge type documented in types.py (now 8 edges, up from 7 `[depthfusion-handoff-context.md §7]`)
- [ ] `traverse()` can filter by edge kind
- [ ] CIQS Category D delta ≥ +2 points on the "what did we do recently" family of questions
- [ ] ≥ 8 new tests

**Kill-criterion:** `PRECEDED_BY` edges outnumber other edge types 10:1 (indicates the linker is over-matching).

---

### TG-10: Embedding-based discovery dedup (CM-2)

**Source:** CM-2.
**Environments:** vps-cpu (optional), vps-gpu.
**Depends on:** TG-03, TG-05.

**Files touched:**
- `src/depthfusion/capture/dedup.py` — new
- `src/depthfusion/capture/auto_learn.py` — call dedup before writing
- `tests/test_capture/test_dedup.py` — new

**New flags:** `DEPTHFUSION_DEDUP_THRESHOLD` (default `0.92` cosine sim).

**Acceptance criteria:**
- [ ] When two discoveries have cos-sim ≥ 0.92, newer supersedes older (older file renamed with `.superseded` suffix, kept for audit)
- [ ] False-dedup rate ≤ 5% on a manually labelled set of 30 near-duplicate pairs
- [ ] ≥ 6 new tests

---

### TG-11: Selective fusion gates (Mamba B/C/Δ port)

**Source:** TS-1 from Phase 1.
**Environments:** vps-cpu, vps-gpu.
**Depends on:** TG-01, TG-03.

**Files touched:**
- `src/depthfusion/fusion/gates.py` — new (port of TS gate logic per `[DEPTHFUSION_ARCHITECTURE.md §5]`)
- `src/depthfusion/retrieval/hybrid.py` — integrate into RecallPipeline; emit gate log per D-3 invariant `[DEPTHFUSION_ARCHITECTURE.md §13]`
- `src/depthfusion/metrics/collector.py` — accept gate log entries
- `tests/test_fusion/test_gates.py` — new

**New flags:** `DEPTHFUSION_FUSION_GATES_ENABLED` (default `false`), `DEPTHFUSION_FUSION_GATES_ALPHA` (default `0.3` — AttnRes α per `[DEPTHFUSION_ARCHITECTURE.md §5]`).

**Acceptance criteria:**
- [ ] CIQS Category A delta ≥ +2 points on vps-cpu; ≥ +3 points on vps-gpu
- [ ] Gate log emitted per query (D-3 compliance)
- [ ] Parity test: same input → same gate decision as TS reference implementation on 20 test cases
- [ ] ≥ 12 new tests

**Kill-criterion:** gates do not beat source-weight baseline on CIQS Category A on either environment.

---

### TG-12: Observability extensions

**Source:** Phase 1 Gap 1.
**Environments:** all.
**Depends on:** TG-01.

**Files touched:**
- `src/depthfusion/metrics/collector.py` — add fields: `backend_used`, `backend_fallback_chain`, `latency_ms_per_capability`, `capture_mechanism`, `capture_write_rate`
- `src/depthfusion/metrics/aggregator.py` — summary tables for the new fields
- `tests/test_metrics/test_collector_v05.py` — new

**Acceptance criteria:**
- [ ] Every recall query writes a JSONL record with the new fields
- [ ] Aggregator produces per-backend latency + error-rate summary
- [ ] ≥ 4 new tests

---

### TG-13: Opus 4.7 task budgets for RLM

**Source:** OP-2 from Phase 1.
**Environments:** all (where `DEPTHFUSION_RLM_ENABLED=true`).
**Depends on:** none.

**Files touched:**
- `src/depthfusion/recursive/client.py` — translate `DEPTHFUSION_RLM_COST_CEILING` (USD) into API-side token budget using the task-budgets beta
- `src/depthfusion/router/cost_estimator.py` — reconcile post-hoc estimation with API-side budget
- `tests/test_recursive/test_task_budget.py` — new (with a mock Anthropic API that supports the task-budget header)

**Acceptance criteria:**
- [ ] When task-budgets beta is available in the SDK, `RLMClient` passes the budget header
- [ ] When SDK doesn't support it yet (version check), falls back to post-hoc estimation with a warning
- [ ] ≥ 4 new tests

**Kill-criterion:** task-budgets beta API changes before v0.5 ships — scope to "best effort wrapper" without CIQS claim.

---

### TG-14: `depthfusion_prune_discoveries` MCP tool

**Source:** Phase 1 Gap 2.
**Environments:** all.
**Depends on:** none.

**Files touched:**
- `src/depthfusion/mcp/server.py` — new tool
- `src/depthfusion/capture/pruner.py` — new
- `tests/test_mcp/test_prune_discoveries.py` — new

**New flags:** `DEPTHFUSION_PRUNE_AGE_DAYS` (default `90`), `DEPTHFUSION_PRUNE_MIN_RECALL_SCORE` (default `0.05` — never recalled above this threshold in last 30 days → prune candidate).

**Acceptance criteria:**
- [ ] Tool returns a list of candidate files to prune with reasons; does NOT delete without explicit `confirm=true`
- [ ] When confirmed, moves (not deletes) to `~/.claude/shared/discoveries/.archive/`
- [ ] ≥ 3 new tests

---

### TG-15: Project-filter for discoveries

**Source:** Phase 1 Gap 3.
**Environments:** all.
**Depends on:** TG-05 (needs the `project:` frontmatter field added by decision-extractor).

**Files touched:**
- `src/depthfusion/retrieval/hybrid.py` — parse frontmatter at load time; filter on current project unless `cross_project=true`
- `src/depthfusion/mcp/server.py` — add `cross_project: bool = false` parameter to `depthfusion_recall_relevant`
- `tests/test_retrieval/test_project_filter.py` — new

**Acceptance criteria:**
- [ ] Default recall in project A does not return discoveries tagged `project: B`
- [ ] `cross_project=true` returns everything (v0.4.x behaviour)
- [ ] Discoveries without frontmatter are treated as `cross_project` (backward-compat)
- [ ] ≥ 5 new tests

---

## 2.5 Observability additions (aggregate of TG-12)

The JSONL schema extension for `metrics/collector.py` is the single concrete contract all other TGs target. Shape:

```json
{
  "ts": "2026-05-10T12:34:56Z",
  "event": "recall" | "capture" | "backend_call" | "gate_decision",
  "event_subtype": "ok" | "user_deny" | "acs_reject" | "sla_expiry_deny" | null,
  "query": "...",
  "backend_used": {"reranker": "gemma", "extractor": "haiku", "summariser": "gemma"},
  "backend_fallback_chain": {"reranker": ["gemma", "haiku"]},
  "latency_ms": {"total": 823, "bm25": 45, "embed": 120, "rerank": 612},
  "capture_mechanism": "decision_extractor" | "negative_extractor" | "git_hook" | null,
  "capture_write_rate": 0.3,
  "result_count": 5,
  "config_version_id": "cfg-v0.5.0-abc123",
  "error": null
}
```

Schema notes (per DR-018 ratification 2026-04-18):
- `event_subtype` distinguishes `sla_expiry_deny` from other DENY causes — satisfies I-19 compliance (DR-018 ratified legacy #6 → I-19 "approval state machines fail-closed on SLA expiry").
- `config_version_id` references the immutable config snapshot active at call time — satisfies amended I-11 compliance (DR-018 ratified legacy #7 absorbed into I-11). Same field is emitted on both `recall`/`capture`/`backend_call` entries AND on `gate_decision` entries (I-8 joint scope per DR-018 §3.5 scope note).

Every TG that adds a new capability must write to this schema; the aggregator's `depthfusion.metrics.aggregator` output is the primary source for CIQS regression detection.

---

## 2.6 CIQS benchmark gate

Per Phase 1 caveat C5:

- **When:** CIQS benchmark runs at the end of each TG merge (GitHub Actions workflow, not yet present — to be authored as part of TG-02 or TG-12).
- **Threshold:** any category regression > 2 points vs v0.4.x blocks merge. A regression ≤ 2 points emits a warning but passes.
- **Who:** CI runs the benchmark on vps-cpu with a fixed corpus snapshot; vps-gpu benchmarks are run manually by Greg at the end of each TG on the GEX44.
- **Baseline freeze:** v0.4.x CIQS numbers per category are frozen at the commit that merges TG-01 and stored in `docs/benchmarks/v0.4.x-baseline.json`.

Epic E-14 (CIQS Data-Gap) in `[depthfusion-handoff-context.md §14]` is already `[active]` — this ties v0.5 execution directly to it.

---

## 2.7 Release merge order

Dependency-respecting order:

```
TG-12 (obs schema — lightweight prep)
   ↓
TG-01 (backend interface — foundational)
   ↓
TG-02 (installer three modes)
   ↓
   ├── TG-03 (local embedding)  ──┐
   ├── TG-04 (Gemma backend)  ───┐│
   ├── TG-13 (task budgets)     ─┘│
   ├── TG-06 (git hook)         ─┐│
   ├── TG-09 (graph PRECEDED_BY)─┼┘
   ├── TG-14 (prune tool)       ─┤
   └── TG-05 (decision extractor)┤
                                 ↓
                     TG-15 (project filter) — needs TG-05's frontmatter
                     TG-07 (confirm tool)  — independent, any time after TG-05
                     TG-08 (negative extractor) — needs TG-05
                     TG-10 (dedup)         — needs TG-03 + TG-05
                     TG-11 (Mamba gates)   — needs TG-01 + TG-03
```

All features can land **disabled** (flag default `false`) before being enabled. Enabling happens in a second PR per TG, gated by the CIQS benchmark.

---

## 2.8 Out-of-scope (v0.5 explicitly does NOT ship)

- **Cross-mode corpus migration** (`local → vps-gpu`) — deferred to v0.6. v0.5 users installing a new mode start with an empty corpus.
- **ChromaDB graph backend** (S-39 in E-17 tech debt `[handoff §14]`) — stays on the backlog.
- **TS-3 Chunk state compression** — no attachment point in the current Python corpus-size regime.
- **TS-4 Materialisation policy** — same reason as TS-3.
- **TS-7 12-strategy set + HorizonTuner** — 4 strategies in Python today are adequate; expansion earns its own release.
- **OP-1 xhigh Opus mode** — no natural attachment point in DepthFusion.
- **OP-3 Opus 4.7 tokenizer change** — unverified claim; skipped.
- **Real-time event bus streaming to SkillForge (SSE / WebSocket)** — Phase 3 integration uses existing JSONL metrics + request/response; streaming events are a v0.6 concern.
- **Multi-tenant isolation** (separate users sharing one DepthFusion instance) — single-user for v0.5.

---

## 2.9 Expected delivery shape

- 15 task groups, ~3–4 weeks of focused work by a single Claude Code worker.
- Expected LOC delta: +2500 to +3500 across `src/depthfusion/`.
- Expected new tests: ≥ 110 (target: > 20% of current 439-test suite).
- Expected new env flags: 13 (listed per TG) — brings total from 20 to 33.
- Expected CIQS ceiling after full v0.5 rollout: **90–94** (vs v0.4.x projected ceiling 88–90).
