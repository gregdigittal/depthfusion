# ACL Retrieval Benchmark — 2026-06-11

**Generated:** 2026-06-11T21:07:52+00:00 UTC  
**Git commit:** `e32bc4f`  
**Runs per condition:** 100  
**Corpus size:** 200 documents  

## Summary

| Condition | p50 | p95 | p99 | mean | avg results |
|-----------|-----|-----|-----|------|-------------|
| Without ACL trimming | 0.047 ms | 0.058 ms | 0.065 ms | 0.048 ms | 20.0 |
| With ACL trimming    | 0.218 ms | 0.441 ms | 0.495 ms | 0.235 ms | 10.0 |

## Overhead (ACL trimming vs raw retrieval)

| Metric | Absolute overhead |
|--------|-------------------|
| p50    | 0.171 ms |
| p95    | 0.382 ms |
| p99    | 0.430 ms |

## Notes

- **Without ACL trimming**: BM25 search only (top-20 candidates).
- **With ACL trimming**: BM25 search + `verify_acl()` post-rank pass.
- Principal `alice` owns 50% of corpus documents (alternating ownership).
- Corpus is synthetic (in-memory); no I/O latency is included.
- All timings are wall-clock via `time.perf_counter()`.

## Methodology

The benchmark builds a 200-document synthetic corpus where half the
documents belong to principal `alice` and half to `bob`.  Each run
executes a BM25 search followed (in the ACL condition) by the
`verify_acl()` post-rank verification pass from
`depthfusion.retrieval.acl_verifier`.  The two conditions are measured
sequentially within the same process to minimise JIT-warmup skew.
