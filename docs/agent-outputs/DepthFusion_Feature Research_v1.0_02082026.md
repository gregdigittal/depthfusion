---
**Document Control**

| Field | Value |
|---|---|
| Project | DepthFusion |
| Document | Feature Research — State Preservation & Next Features |
| Author | Greg Morris |
| Version | v1.0 |
| Date | 02-08-2026 |
| Status | Draft |
---

# DepthFusion Feature Research v1.0

## Executive Summary

This document captures deep research on two areas:
1. Real-time state preservation and session failure recovery for uncommitted work loss
2. Feature idea evaluation and prioritisation for E-73+

---

## Area 1: Real-Time State Preservation & Session Failure Recovery

### What State Is Actually at Risk

Five categories, ordered by recoverability:

| Category | Risk | Status |
|---|---|---|
| Items published via `depthfusion_publish_context` | None | Already safe — SQLite WAL + fsync |
| git-committed files | None | Safe in history |
| Dirty working tree (uncommitted edits) | **HIGH** | Primary value gap |
| In-flight plan state (multi-step goal, wave position) | **HIGH** | Compact protocol helps; doesn't cover SIGKILL |
| Working memory / reasoning chain | Total loss | Unrecoverable, but re-derivable from artifacts |

The practical recovery target: (1) which files were modified since last commit, (2) where the plan was in its execution, (3) inject that state into the next session via `depthfusion_session_seed`.

### Key Design Decision

**Hybrid checkpoint strategy:**
- **Primary:** Hook into `DELETE /mcp` (graceful shutdown) — fires on every clean Claude exit, zero cost on happy path
- **Secondary:** Ambient trace in `_process_request` dispatcher — lightweight JSONL append per tool call, crash coverage
- **Manual:** New MCP tool `depthfusion_session_checkpoint` for agents at plan boundaries

This mirrors SQLite WAL: low-frequency full checkpoints (DELETE) + continuous append-only log (ambient).

### Git Integration

`git stash create` (non-destructive, returns SHA without popping) is the right primitive. It stores the exact byte-for-byte working tree state in the checkpoint record. Recovery: `git stash apply <sha>`. Degrades gracefully if git is absent (falls back to files_modified list only).

### MCP Hook Points

| Hook | When | Purpose |
|---|---|---|
| `initialize` (POST /mcp) | Session open | Load prior checkpoint, inject into context |
| Per-tool-call (`_process_request`) | Every tool call | Ambient trace for crash coverage |
| `DELETE /mcp` | Clean shutdown | Full-fidelity checkpoint before session closes |
| SIGTERM (uvicorn lifespan) | Service stop | Checkpoint all open sessions |

### Recovery UX

`depthfusion_session_seed` gets a new `mode="resume"` parameter:
- Returns `{"resume": false, "seed": [...]}` — no prior checkpoint
- Returns `{"resume": true, "checkpoint": {...}, "seed": [...]}` — includes plan_state, git_stash_ref, files_modified

Agent surfaces to user: *"Found a prior incomplete session — files modified: X, Y, Z. Last plan step: 'wire the auth middleware'. Continue from here?"*

### Checkpoint Data Schema

```python
{
    "item_id": "ckpt-{session_id}-{timestamp}",
    "content": "<human-readable session state summary>",
    "tags": ["checkpoint", project_slug, "session-recovery"],
    "priority": "high",
    "ttl_seconds": 604800,   # 7 days
    "metadata": {
        "record_type": "checkpoint",
        "project_slug": project_slug,
        "plan_state": {
            "current_task": "...",
            "tasks_remaining": [...],
            "tasks_completed": [...],
        },
        "git_stash_ref": "abc123...",   # null if not in a git repo
        "tool_call_count": 38,
        "context_pct_at_checkpoint": 0.72,
        "agent_model": "claude-sonnet-4-6"
    },
    "files_modified": ["src/auth/middleware.py", "tests/test_auth.py"],
}
```

---

## Area 2: Feature Ideas — Evaluated

### 1. MCP Client Documentation (Cursor/Windsurf/Cline)

**Zero code changes needed.** The HTTP MCP server already speaks the Streamable HTTP transport that all three clients support natively. Just need config snippets + a tutorial page.

- **Feasibility:** High | **Effort:** XS | **Value:** High
- Every Cursor/Windsurf user becomes a potential DepthFusion user immediately.

### 2. Automated Memory Hygiene Scheduler

70% built. `capture/pruner.py`, `capture/dedup.py`, `capture/decay.py` all exist. **No scheduler exists.** An APScheduler job calling decay + dedup + prune nightly prevents recall quality degradation.

- **Feasibility:** High | **Effort:** S | **Value:** High
- Without this, every user's experience degrades as the memory store grows.

### 3. Real Activity Feed in Tauri UI

**The current dashboard has hardcoded placeholder data.** The "Recent Activity" tile in `DashboardPage.tsx` shows four static strings that never change. StorageUsage hardcodes `4.2 GB / 20 GB`. The REST API endpoints exist; the UI just doesn't call them.

- **Feasibility:** High | **Effort:** S | **Value:** High
- Trust/credibility issue: new users see obviously fake data.

### 4. Session Checkpoint & Recovery

The feature described in Area 1 above. Novel differentiator — no other AI memory layer offers automatic crash recovery with git-level state fidelity.

- **Feasibility:** High | **Effort:** M | **Value:** High
- Makes DepthFusion a safety net, not just a performance enhancement.

### 5. Rate Limiting + Team Server Hardening

Rate limiting is **completely absent** from the codebase (confirmed by grep — no `slowapi`, no `fastapi-limiter`, no custom middleware). Concurrent session safety (`_MCP_SESSIONS` dicts) breaks in multi-worker mode.

- **Feasibility:** High | **Effort:** M | **Value:** Medium-High
- Prerequisite for any team deployment or hosted tier.

### 6. Embedding A/B Validation

No `OPENAI_API_KEY` needed for the fast path. Compare `all-MiniLM-L6-v2` vs `all-mpnet-base-v2` or `bge-small-en-v1.5` using the existing benchmark script. The `DEPTHFUSION_EMBEDDING_MODEL` env var already controls this.

- **Feasibility:** High | **Effort:** S | **Value:** High
- Quantifies whether HNSW actually improves recall over pure BM25.

### 7. Streaming Recall

SSE infrastructure already present. Add `stream=true` parameter to `depthfusion_recall_relevant` — return results as they clear BM25 minimum score (~20ms for first result vs ~500ms for full rerank).

- **Feasibility:** High | **Effort:** M | **Value:** Medium

### 8. VS Code Extension

Backend complete. But better near-term alternative: Cursor/Windsurf/Cline config documentation (XS effort, same audience expansion, no maintenance burden).

- **Feasibility:** High | **Effort:** L | **Value:** Medium
- **Defer** — document MCP config for Cursor etc. first, build extension if validated demand exists.

### 9. Cross-Session Diff Analysis

Checkpoint must ship first (diffs stored in checkpoint metadata). Then `/query/aggregate` gets `?type=file_diffs&file=<path>&since=<iso>`.

- **Feasibility:** Medium | **Effort:** M | **Value:** High
- **Depends on:** Session checkpointing.

### 10. Public Hosted Tier / Memory Federation

Requires org_id isolation, billing integration, rate limiting, cross-server trust protocol. Sequenced last.

- **Effort:** L–XL | **Defer**

---

## Suggested E-73 Sequencing

### Now (Sprint 1)
1. MCP client docs — Cursor/Windsurf/Cline (XS, no code)
2. Real activity feed — replace hardcoded data in Tauri UI (S)
3. Automated memory hygiene scheduler (S)

### Sprint 2
4. Embedding A/B validation — local models only (S)
5. Session checkpoint & recovery (M)
6. Rate limiting + team server hardening (M)

### Sprint 3
7. Streaming recall (M)
8. Checkpoint timeline in Tauri UI (M)
9. Cross-session diff analysis (M, depends on checkpointing)

### Later
- Public hosted tier, VS Code extension, memory federation

---

## Version History

| Version | Date | Author | Changes |
|---|---|---|---|
| v1.0 | 02-08-2026 | Greg Morris | Initial research output |
