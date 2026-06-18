#!/usr/bin/env python3
"""T-499: fabric_seed latency benchmark.

Seeds 500 EventEntities into the graph, then measures GET /v1/events/seed
response time. p99 < 2s is the acceptance criterion.

Usage:
    python scripts/bench_fabric_seed.py
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

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

        print("Seeding 500 EventEntities ...")
        t_seed_start = time.perf_counter()
        for i in range(500):
            resp = client.post("/v1/events/publish", json={
                "agent_id": f"seed-agent-{i % 10}",
                "project_slug": "bench",
                "memory_refs": [f"seed-mem-{i}"],
                "session_id": f"seed-session-{i % 20}",
            })
            assert resp.status_code == 200, f"seed failed at {i}: {resp.text}"
        t_seed = time.perf_counter() - t_seed_start
        print(f"  Seed complete in {t_seed:.2f}s")

        # Warm up
        client.get("/v1/events/seed?projects=bench&goal=test+query")

        n_runs = 20
        print(f"\nMeasuring GET /v1/events/seed × {n_runs} runs ...")
        latencies: list[float] = []

        for i in range(n_runs):
            t0 = time.perf_counter()
            resp = client.get(f"/v1/events/seed?projects=bench&goal=query+{i}")
            elapsed = (time.perf_counter() - t0) * 1000
            assert resp.status_code == 200, f"seed request {i} failed: {resp.text}"
            latencies.append(elapsed)

        mean_ms = statistics.mean(latencies)
        median_ms = statistics.median(latencies)
        p95_ms = statistics.quantiles(latencies, n=100)[94]
        p99_ms = statistics.quantiles(latencies, n=100)[98]
        max_ms = max(latencies)

        print(f"\nfabric_seed latency (500 EventEntities, {n_runs} runs):")
        print(f"  mean   {mean_ms:.0f} ms")
        print(f"  median {median_ms:.0f} ms")
        print(f"  p95    {p95_ms:.0f} ms")
        print(f"  p99    {p99_ms:.0f} ms")
        print(f"  max    {max_ms:.0f} ms")

        passed = p99_ms < 2000.0
        print(f"\n  SLA: p99 < 2000ms → {'PASS' if passed else 'FAIL'} ({p99_ms:.0f} ms)")

        return {
            "benchmark": "fabric_seed_latency",
            "n_entities_seeded": 500,
            "n_runs": n_runs,
            "mean_ms": round(mean_ms, 1),
            "median_ms": round(median_ms, 1),
            "p95_ms": round(p95_ms, 1),
            "p99_ms": round(p99_ms, 1),
            "max_ms": round(max_ms, 1),
            "sla_pass": passed,
        }


if __name__ == "__main__":
    result = run_benchmark()
    sys.exit(0 if result["sla_pass"] else 1)
