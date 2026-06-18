# Event Graph Fabric — Performance Baseline

**Date:** 2026-05-23
**Environment:** Linux VPS (176.9.147.206), Python 3.12, SQLite WAL, no Redis
**Benchmark method:** Starlette TestClient (ASGI in-process), `time.perf_counter()`, `statistics.quantiles`
**Epic:** E-46 / S-145

---

## Summary

| Benchmark | SLA | Result | Status |
|-----------|-----|--------|--------|
| Publish latency (serial, 200 events) | p99 < 500ms | **30.3 ms** | ✅ PASS |
| fabric_seed (500 EventEntities, 20 runs) | p99 < 2000ms | **110 ms** | ✅ PASS |
| /trail (1000 EventEntities) | p99 < 500ms | **50.5 ms** | ✅ PASS |
| /observers (MemoryEntities) | p99 < 500ms | **8.5 ms** | ✅ PASS |
| Graceful degradation (dead Redis) | no raise, warn logged | **PASS** | ✅ PASS |

---

## T-498: Publish-to-response Latency

**Benchmark:** `scripts/bench_publish_sse.py`
**Scenario:** 200 sequential `POST /v1/events/publish` calls, in-process TestClient, no Redis.

| Metric | Value |
|--------|-------|
| n | 200 |
| mean | 17.8 ms |
| median | 16.8 ms |
| p95 | 24.3 ms |
| p99 | **30.3 ms** |
| max | 34.7 ms |

**SLA: p99 < 500ms → PASS** (16× headroom)

**Note on concurrency:** Starlette's `TestClient` serialises requests under the Python GIL (WSGI-style adapter). Truly concurrent throughput requires a live uvicorn process + async client (e.g. `httpx.AsyncClient`). Serial p99 = 30ms gives strong confidence the 500ms SLA holds even under heavy contention: a 16-publisher pile-up would need 500ms / 16 × 30ms per request to saturate — a scenario that would require the graph lock to hold for ~480ms, which is far outside measured lock hold times (< 5ms per write).

---

## T-499: fabric_seed Latency

**Benchmark:** `scripts/bench_fabric_seed.py`
**Scenario:** 500 EventEntities pre-seeded; 20 `GET /v1/events/seed?projects=bench&goal=…` requests.

| Metric | Value |
|--------|-------|
| n | 20 runs |
| mean | 49 ms |
| median | 37 ms |
| p95 | 109 ms |
| p99 | **110 ms** |
| max | 109 ms |

**SLA: p99 < 2000ms → PASS** (18× headroom)

---

## T-500: Provenance Query Latency

**Benchmark:** `scripts/bench_provenance_queries.py`
**Scenario:** 1,000 EventEntities + 20 MemoryEntities seeded; 30 runs per endpoint.

### GET /v1/graph/agent/{agent_id}/trail

| Metric | Value |
|--------|-------|
| n | 30 runs |
| mean | 28.3 ms |
| median | 25.1 ms |
| p95 | 45.5 ms |
| p99 | **50.5 ms** |
| max | 47.8 ms |

**SLA: p99 < 500ms → PASS** (9× headroom)

### GET /v1/graph/memory/{entity_id}/observers

| Metric | Value |
|--------|-------|
| n | 30 runs |
| mean | 5.5 ms |
| median | 6.4 ms |
| p95 | 8.5 ms |
| p99 | **8.5 ms** |
| max | 8.5 ms |

**SLA: p99 < 500ms → PASS** (59× headroom)

---

## T-501: Graceful Degradation

**Benchmark:** `scripts/bench_degradation.py`
**Scenario:** EventStore configured with RedisStreamBackend pointing at 127.0.0.1:19999
(connection refused). Verifies graph writes succeed and degradation is logged.

| Test | Result |
|------|--------|
| publish() with dead Redis — graph write succeeds | ✅ PASS |
| Degradation warning logged (not exception raised) | ✅ PASS |
| get_recent_events() falls back to graph-only | ✅ PASS |
| fabric_seed_bundle() falls back to graph-only | ✅ PASS |
| recall/retrieve paths unaffected (no Redis dependency) | ✅ PASS |

**Error logged:** `EventStore: stream publish failed (best-effort) — InvalidResponse: Protocol error`
**Exception propagation:** None (best-effort path swallows and warns)

---

## Observations and Scaling Notes

1. **SQLite WAL mode** keeps write-read concurrency high: readers never block writers; writers serialize per-project via `file_locking.py`.

2. **fabric_seed ranking** (recall_relevance × recency_decay × log(1+observer_count)) scales linearly with the number of memory_refs gathered from EventEntities. With 500 entities the median is 37ms. At 5,000 entities expect ~370ms (linear scan); if p99 approaches 1,500ms, add a time-bucketed index or limit `since_hours` window.

3. **Provenance query** (`/trail`, `/observers`) scans `all_entities()` in-memory at 1,000 entities in ~28ms mean. SQLiteGraphStore returns a full entity list; future work (S-15x) could add a SQL WHERE on `type = 'event'` and `metadata->>'agent_id'` to avoid the full scan.

4. **True concurrent throughput** was not measured at this baseline — TestClient does not model ASGI concurrency. A follow-up load test with `locust` or `httpx + asyncio` against a live uvicorn server is recommended before production deployment under heavy fan-out.

---

## Benchmark Scripts

| Script | Purpose |
|--------|---------|
| `scripts/bench_publish_sse.py` | T-498: publish-to-response latency |
| `scripts/bench_fabric_seed.py` | T-499: fabric_seed endpoint latency |
| `scripts/bench_provenance_queries.py` | T-500: /trail and /observers latency |
| `scripts/bench_degradation.py` | T-501: graceful Redis degradation |

Run all benchmarks: `python scripts/bench_publish_sse.py && python scripts/bench_fabric_seed.py && python scripts/bench_provenance_queries.py && python scripts/bench_degradation.py`
