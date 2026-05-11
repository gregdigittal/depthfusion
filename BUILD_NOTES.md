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
| S-86 | pending | Mode resolution fix |
| S-87 | pending | Package extras |
| S-88 | pending | MCP tool schemas |
| S-89 | pending | Vector embedding consistency |
| S-90 | pending | Benchmark harness |
| S-91 | pending | SQLite metadata cache |
| S-92 | pending | Recall explainability |
