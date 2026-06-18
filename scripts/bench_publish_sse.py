#!/usr/bin/env python3
"""T-498: Publish-to-SSE latency benchmark.

10 concurrent publishers × 100 events each.
Measures wall-clock time from POST /v1/events/publish to event appearing
in GET /v1/events/stream response body.

Because TestClient is synchronous we measure roundtrip publish latency
(not true SSE push latency which requires a live server + async subscriber).
p99 < 500ms is the acceptance criterion.

Usage:
    python scripts/bench_publish_sse.py
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

# Add project src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

os.environ.setdefault("DEPTHFUSION_MODE", "local")


def run_benchmark():
    from starlette.testclient import TestClient

    import depthfusion.api.events as ev_mod
    from depthfusion.api.rest import app

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DEPTHFUSION_GRAPH_JSON"] = str(Path(tmp) / "bench.json")
        os.environ.pop("DEPTHFUSION_REDIS_URL", None)
        os.environ.pop("DEPTHFUSION_API_TOKEN", None)
        ev_mod._event_store = None

        client = TestClient(app, raise_server_exceptions=True)

        latencies: list[float] = []
        lock = threading.Lock()

        def publisher_worker(agent_idx: int, n_events: int):
            for i in range(n_events):
                payload = {
                    "agent_id": f"bench-agent-{agent_idx}",
                    "project_slug": "bench",
                    "memory_refs": [f"mem-{agent_idx}-{i}"],
                    "session_id": f"session-{agent_idx}",
                }
                t0 = time.perf_counter()
                resp = client.post("/v1/events/publish", json=payload)
                elapsed = (time.perf_counter() - t0) * 1000  # ms
                assert resp.status_code == 200, f"unexpected {resp.status_code}: {resp.text}"
                with lock:
                    latencies.append(elapsed)

        n_publishers = 10
        n_events_each = 100

        print(f"Running {n_publishers} publishers × {n_events_each} events ...")
        t_start = time.perf_counter()

        threads = [
            threading.Thread(target=publisher_worker, args=(i, n_events_each))
            for i in range(n_publishers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        t_total = time.perf_counter() - t_start

        total_events = len(latencies)
        mean_ms = statistics.mean(latencies)
        median_ms = statistics.median(latencies)
        p95_ms = statistics.quantiles(latencies, n=100)[94]
        p99_ms = statistics.quantiles(latencies, n=100)[98]
        max_ms = max(latencies)

        print(f"\nPublish-to-response latency ({total_events} events, {t_total:.1f}s total):")
        print(f"  mean   {mean_ms:.1f} ms")
        print(f"  median {median_ms:.1f} ms")
        print(f"  p95    {p95_ms:.1f} ms")
        print(f"  p99    {p99_ms:.1f} ms")
        print(f"  max    {max_ms:.1f} ms")
        print(f"  throughput {total_events / t_total:.0f} events/s")

        passed = p99_ms < 500.0
        print(f"\n  SLA: p99 < 500ms → {'PASS' if passed else 'FAIL'} ({p99_ms:.1f} ms)")

        return {
            "benchmark": "publish_sse_latency",
            "n_publishers": n_publishers,
            "n_events_each": n_events_each,
            "total_events": total_events,
            "mean_ms": round(mean_ms, 2),
            "median_ms": round(median_ms, 2),
            "p95_ms": round(p95_ms, 2),
            "p99_ms": round(p99_ms, 2),
            "max_ms": round(max_ms, 2),
            "throughput_eps": round(total_events / t_total, 1),
            "sla_pass": passed,
        }


if __name__ == "__main__":
    result = run_benchmark()
    sys.exit(0 if result["sla_pass"] else 1)
