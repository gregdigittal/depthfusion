# S-184 AC-3 — sync/delta P95 round-trip latency — MEASUREMENT RESULT

AC-3: "P95 round-trip < 400ms online against VPS over Tailscale."

## Environment
- Host: DepthFusion VPS (public 176.9.147.206)
- Tailscale interface address: 100.112.109.51 (tailscale0)
- Test binds the real uvicorn server to the Tailscale interface so each
  round-trip genuinely traverses Tailscale (not loopback). Bind address is
  reported per run as `bind=http://100.112.109.51:<port>` and `path=Tailscale`.
- Test: tests/test_benchmarks/test_sync_roundtrip_latency.py
- Method: real /v2/sync/pull endpoint, real httpx client, N=100 measured
  samples (5 warmup discarded), 200 seeded records returned per request,
  nearest-rank P95 asserted < 400ms.

## Runs (UTC 2026-06-19)

Run 1: path=Tailscale  bind=http://100.112.109.51:35315
  p50=2.2ms  p95=2.8ms  p99=3.9ms  max=4.1ms   SLO p95<400ms -> PASS

Run 2: path=Tailscale  bind=http://100.112.109.51:55439
  p50=5.2ms  p95=6.0ms  p99=7.2ms  max=24.7ms  SLO p95<400ms -> PASS

pytest: 1 passed, exit code 0 (both runs).

## Verdict
S-184 AC-3 SATISFIED. P95 round-trip over the VPS Tailscale interface is
2.8ms / 6.0ms across two runs — far under the 400ms SLO.

Full raw run log: see s184-ac3-p95-roundtrip-*.log in this directory.
