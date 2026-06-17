# Offline Cache Hit-Rate Dogfood Report

**Generated:** 2026-06-17 (T-656 implementation baseline)
**Window:** All time (inaugural report — no live telemetry yet)
**Privacy:** on-device telemetry only — data is never uploaded

## Summary

| Metric | Value |
|--------|-------|
| Total lookups | 0 |
| Cache hits | 0 |
| Cache misses | 0 |
| Hit rate | — |
| Target (≥ 80%) | PENDING |

## Status

This is the inaugural dogfood report for T-656 (Offline hit-rate telemetry).
The `HitRateStore` is now instrumented and ready to collect hit/miss events
on-device.  Live data will appear here once the app is exercised in offline mode.

## Implementation Notes (T-652, T-655, T-656)

### T-652 — Local Activity Signal Store

- `ActivitySignalStore` persists signals (queries, opened docs, projects, entities)
  in a local SQLite database under `src/depthfusion/cache/activity_signals.py`.
- Privacy guard: `upload_disabled = True` class-level sentinel; `upload()` and
  `sync_to_remote()` raise `NotImplementedError` so no accidental upload path can
  be added silently.
- Unit tests assert no network library (requests, httpx, aiohttp) is imported.
- Max-signal pruning removes oldest entries when the store exceeds `max_signals`
  (default 10 000), keeping recency bias in the retained set.

### T-655 — Idle-Time Prefetch Scheduler

- `PrefetchScheduler` (under `src/depthfusion/cache/prefetch_scheduler.py`)
  builds a `PrefetchPlan` from scored candidates.
- **Pinned items first** — paths in `pinned_paths` or projects in
  `pinned_projects` are force-included before scored candidates, regardless of
  their relevance score.
- **Budget enforcement** — candidates are admitted in score-descending order
  until `budget_bytes` is exhausted; oversized single items are skipped.
- `is_idle()` delegates to a user-supplied `idle_fn` (defaults to always-True),
  so callers gate plan building on an appropriate system-idle signal.

### T-656 — Offline Hit-Rate Telemetry

- `HitRateStore` records hit/miss events in a local SQLite database under
  `src/depthfusion/cache/hit_rate.py`.
- `HitRateStore.compute(window_seconds=...)` computes statistics over a rolling
  window or all-time.
- `generate_report(store)` returns a Markdown string suitable for this directory.
- **Privacy**: `upload_disabled = True`; no network imports anywhere in the module.
- Target: ≥ 80% offline hit rate (S-189 AC-3).

## How to Record Events

```python
from depthfusion.cache.hit_rate import HitRateStore, generate_report

store = HitRateStore(db_path="~/.depthfusion/hit_rate.db")

# On cache hit:
store.record_hit(path="/docs/architecture.md")

# On cache miss:
store.record_miss(path="/docs/api-spec.md")

# Generate report:
md = generate_report(store, window_seconds=7 * 86400)  # last 7 days
print(md)
```

## How to Record Activity Signals

```python
from depthfusion.cache.activity_signals import ActivitySignalStore, SignalKind

signals = ActivitySignalStore(db_path="~/.depthfusion/signals.db")

# When user runs a query:
signals.record(SignalKind.QUERY, "async python patterns", project="depthfusion")

# When user opens a document:
signals.record(SignalKind.OPEN_DOC, "/docs/readme.md", project="depthfusion")
```

## Next Steps

1. Wire `HitRateStore.record_hit/miss` calls into the cache `get()` path in
   `CacheManager` (or at the offline query engine layer in T-659).
2. Wire `ActivitySignalStore.record()` calls into the UI layer (search, file open).
3. Feed signal store output to the relevance scorer (T-653) as the activity
   centroid for score-ordered prefetch candidates.
4. Run the scheduler during OS idle events (display sleep, focus loss).
5. Re-run this report after 1 week of dogfood usage.
