# Build Notes — E-30 Implementation

## Baseline (S-85, 2026-05-11)

Branch: `feature/depthfusion-buildplan-improvements`

### Test results before any changes

```
pytest -q --tb=no
1430 passed, 3 warnings in 167.41s (0:02:47)
```

### Ruff

```
ruff check src tests
All checks passed!
```

### Pre-existing warnings (not introduced by E-30)

1. `DeprecationWarning: This process (pid=...) is multi-threaded, use of fork() may lead to deadlocks` — in `test_router/test_bus_idempotency.py::TestFileBusCrossProcess::test_cross_process_concurrent_publish_no_double_insert` (3 occurrences, same test). Pre-existing concurrency warning, not related to E-30 changes.

### Known pre-existing failures

None. All 1430 tests pass on baseline.

---

## Implementation log

| Story | Status | Notes |
|-------|--------|-------|
| S-85 | ✅ done | Baseline recorded above |
| S-86 | ✅ done | `utils/mode.py` normalise_mode(); hybrid.py from_env() updated |
| S-87 | ✅ done | `anthropic>=0.40` added to vps-cpu/vps-gpu extras in pyproject.toml |
| S-88 | ✅ done | TOOL_SCHEMAS dict in mcp/server.py; all 18 tools with typed properties |
| S-89 | ✅ done | vector_store.py uses get_backend("embedding") for upsert + query |
| S-90 | ✅ done | scripts/benchmark.py + tests/fixtures/recall_goldset.jsonl (8 entries) |
| S-91 | ✅ done | storage/file_index.py FileMetadataIndex; WAL SQLite, thread-safe |
| S-92 | ✅ done | explain=true on depthfusion_recall_relevant; bm25/rrf/vector/reranker_rank |

### Final test count after E-30

```
pytest -q --tb=no
1511 passed, 3 warnings
```

(baseline 1430 + 81 new tests from E-30 implementation)
