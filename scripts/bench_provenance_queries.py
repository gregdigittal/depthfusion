#!/usr/bin/env python3
"""T-500: Provenance query latency benchmark.

Seeds 1,000 EventEntities + 20 MemoryEntities, then measures:
  - GET /v1/graph/agent/{agent_id}/trail
  - GET /v1/graph/memory/{entity_id}/observers

p99 < 500ms is the acceptance criterion for both endpoints.

Usage:
    python scripts/bench_provenance_queries.py
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

os.environ.setdefault("DEPTHFUSION_MODE", "local")


async def seed_memory_entities(tmp: str, n: int = 20) -> list[str]:
    """Create real MemoryEntities via EventStore.publish_memory and return their IDs."""
    from depthfusion.core.event_store import EventStore, InMemoryStreamBackend
    from depthfusion.graph.store import get_store

    graph = get_store(graph_json_path=Path(tmp) / "bench.json")
    store = EventStore(graph=graph, stream=InMemoryStreamBackend())
    ids = []
    for i in range(n):
        result = await store.publish_memory(
            content=f"benchmark memory entity number {i} with unique words like xqz{i}",
            agent_id="bench-mem-agent",
            project_slug="bench",
            session_id="bench-session",
        )
        ids.append(result["memory_id"])
    return ids


def run_benchmark():
    import depthfusion.api.events as ev_mod
    from depthfusion.api.rest import app
    from starlette.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DEPTHFUSION_GRAPH_JSON"] = str(Path(tmp) / "bench.json")
        os.environ.pop("DEPTHFUSION_REDIS_URL", None)
        os.environ.pop("DEPTHFUSION_API_TOKEN", None)
        ev_mod._event_store = None

        # Seed 20 MemoryEntities via direct async call before starting TestClient
        print("Seeding 20 MemoryEntities via EventStore.publish_memory ...")
        mem_ids = asyncio.run(seed_memory_entities(tmp))
        print(f"  Created {len(mem_ids)} memory entities: {mem_ids[:3]}...")

        # Now reset event store — TestClient will pick up the same SQLite graph
        ev_mod._event_store = None

        client = TestClient(app, raise_server_exceptions=True)

        print("\nSeeding 1,000 EventEntities via REST ...")
        t_seed_start = time.perf_counter()
        for i in range(1000):
            resp = client.post("/v1/events/publish", json={
                "agent_id": f"bench-agent-{i % 5}",
                "project_slug": "bench",
                "memory_refs": [mem_ids[i % len(mem_ids)]],
                "session_id": f"session-{i % 10}",
            })
            assert resp.status_code == 200, f"seed failed at {i}: {resp.text}"
        t_seed = time.perf_counter() - t_seed_start
        print(f"  Seed complete in {t_seed:.2f}s")

        n_runs = 30

        # /trail benchmark
        trail_latencies: list[float] = []
        print(f"\nMeasuring /trail × {n_runs} runs ...")
        for i in range(n_runs):
            agent_id = f"bench-agent-{i % 5}"
            t0 = time.perf_counter()
            resp = client.get(f"/v1/graph/agent/{agent_id}/trail?project=bench")
            elapsed = (time.perf_counter() - t0) * 1000
            assert resp.status_code == 200, f"/trail failed: {resp.text}"
            trail_latencies.append(elapsed)

        # /observers benchmark on memory entities (which have AGENT_RECEIVED edges from publish calls)
        obs_latencies: list[float] = []
        print(f"Measuring /observers × {n_runs} runs (memory entities) ...")
        for i in range(n_runs):
            mem_id = mem_ids[i % len(mem_ids)]
            t0 = time.perf_counter()
            resp = client.get(f"/v1/graph/memory/{mem_id}/observers")
            elapsed = (time.perf_counter() - t0) * 1000
            assert resp.status_code == 200, f"/observers failed: {resp.text}"
            obs_latencies.append(elapsed)

        # Verify observer data looks correct
        sample_resp = client.get(f"/v1/graph/memory/{mem_ids[0]}/observers")
        sample_count = sample_resp.json()["count"]
        print(f"  Sample: memory {mem_ids[0]} has {sample_count} observer(s)")

        def stats(name: str, data: list[float]) -> dict:
            mean = statistics.mean(data)
            median = statistics.median(data)
            p95 = statistics.quantiles(data, n=100)[94]
            p99 = statistics.quantiles(data, n=100)[98]
            mx = max(data)
            passed = p99 < 500.0
            print(f"\n{name}:")
            print(f"  mean {mean:.1f} ms  median {median:.1f} ms  p95 {p95:.1f} ms  p99 {p99:.1f} ms  max {mx:.1f} ms")
            print(f"  SLA: p99 < 500ms → {'PASS' if passed else 'FAIL'} ({p99:.1f} ms)")
            return {
                "mean_ms": round(mean, 1),
                "median_ms": round(median, 1),
                "p95_ms": round(p95, 1),
                "p99_ms": round(p99, 1),
                "max_ms": round(mx, 1),
                "sla_pass": passed,
            }

        trail_stats = stats("/trail (1000 entities, 5 agents)", trail_latencies)
        obs_stats = stats("/observers (memory entities with AGENT_RECEIVED edges)", obs_latencies)

        overall_pass = trail_stats["sla_pass"] and obs_stats["sla_pass"]
        print(f"\nOverall: {'PASS' if overall_pass else 'FAIL'}")

        return {
            "benchmark": "provenance_query_latency",
            "n_event_entities_seeded": 1000,
            "n_memory_entities_seeded": len(mem_ids),
            "n_runs": n_runs,
            "trail": trail_stats,
            "observers": obs_stats,
            "sla_pass": overall_pass,
        }


if __name__ == "__main__":
    result = run_benchmark()
    sys.exit(0 if result["sla_pass"] else 1)
