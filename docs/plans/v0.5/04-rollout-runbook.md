# DepthFusion v0.5 — Phase 4 Rollout Runbook

> **Status:** Draft for Greg's review. Every step is a concrete command or check; every failure mode has a recovery path.
> **Assumes:** Phases 2 + 3 complete and merged. Adapter B implemented.
> **Generated:** 2026-04-17

---

## 4.1 Pre-rollout gates (all must be GREEN)

Commands to run, in order, on the main branch of `/home/gregmorris/projects/depthfusion/`:

```bash
cd /home/gregmorris/projects/depthfusion

# 1. Merge sanity
git status                                              # expect: clean working tree
git log --oneline -5                                    # expect: v0.5.0 tag commit present

# 2. Test suite
.venv/bin/pytest -q                                     # expect: 549+ passed (439 baseline + ≥110 new)
.venv/bin/mypy src/ --ignore-missing-imports           # expect: ≤ 30 pre-existing SDK warnings, no new
.venv/bin/ruff check src/ tests/                       # expect: clean
.venv/bin/python -m depthfusion.analyzer.compatibility  # expect: all C1-C11 GREEN

# 3. CIQS benchmark — v0.4.x baseline comparison
.venv/bin/python -m depthfusion.benchmark.ciqs \
    --corpus docs/benchmarks/ciqs-fixture-corpus \
    --baseline docs/benchmarks/v0.4.x-baseline.json \
    --mode vps-cpu
# expect: no category regression > 2 points

# 4. Adapter B tests (in skillforge repo)
cd /home/gregmorris/projects/skillforge
pnpm test --filter @skillforge/depthfusion-mcp-adapter  # expect: all green
pnpm test --filter runtime                              # expect: existing tests still pass

# 5. Decision records
ls docs/plans/v0.5/decision-records/                    # expect: one ADR per NEED FROM GREG item from Phase 3 §3.9
```

**Any RED** on steps 1–5 blocks the rollout. Do not proceed.

---

## 4.2 Rollout sequence

Per user instruction: DepthFusion integrated into SkillForge first, then tested, then SF-with-DF deployed to local, vps-cpu, vps-gpu.

### Step 1 — DepthFusion v0.5 standalone release

**Commands:**
```bash
cd /home/gregmorris/projects/depthfusion
git tag -a v0.5.0 -m "v0.5.0 — backend interface + three-mode installer + capture mechanisms"
git push origin v0.5.0

# Publish
# If PyPI: uv publish --index testpypi  (verify on test first)
#         uv publish                    (production)
# If private index: per your private-index convention (confirm with Greg — not specified in inputs)
```

**Smoke test on Greg's Mac (local mode):**
```bash
# In a temp venv to avoid contaminating the dev env
python -m venv /tmp/df-v05-smoketest && source /tmp/df-v05-smoketest/bin/activate
pip install depthfusion==0.5.0
python -m depthfusion.install.install --mode=local
# expect: exit 0, prints "Local install complete"

# Smoke query
claude mcp list                                         # expect: depthfusion registered
claude mcp call depthfusion depthfusion_status          # expect: JSON with mode=local
```

**Acceptance for step 1:** all three modes pass their mode's smoke test.

| Mode | Host | Acceptance |
|---|---|---|
| local | Greg's Mac | Smoke test returns recall result; no API calls made; latency < 500ms |
| vps-cpu | current Hetzner VPS | Smoke test returns recall result; Haiku call succeeds if `DEPTHFUSION_HAIKU_ENABLED=true`; latency ≤ 2s |
| vps-gpu | GEX44 (once provisioned) | Smoke test returns recall result; Gemma call succeeds; latency ≤ 3s at p95 |

**Recovery:** if any mode fails, `pip install depthfusion==0.4.0` reverts; DF continues to work at v0.4.x while the issue is fixed. DF standalone is independent of SF — no SF rollback needed.

---

### Step 2 — SkillForge adapter implementation + tests

**Implementation check-in (at end of Adapter B work, before merge):**
```bash
cd /home/gregmorris/projects/skillforge
pnpm install                                            # expect: no peer warnings for @skillforge/depthfusion-mcp-adapter
pnpm build --filter @skillforge/depthfusion-mcp-adapter # expect: clean TypeScript compile
pnpm test --filter @skillforge/depthfusion-mcp-adapter  # expect: unit tests green
```

**Integration test (requires DF v0.5 installed):**
```bash
pip install depthfusion==0.5.0                          # must be available in CI image
pnpm test:integration --filter @skillforge/depthfusion-mcp-adapter
# expect: all integration tests green; real MCP subprocess spawned successfully
```

**E2E test (requires SF api app + DF v0.5):**
```bash
pnpm dev --filter api &                                 # SF api app in background
API_PID=$!
pnpm test:e2e --filter api -- --testPathPattern=depthfusion-recall
kill $API_PID
# expect: skill with depthfusion.recall step completes; InvocationLog entry present; ACS QualityReport attached
```

**Demo flow (documented artefact):**
- A 2-minute recorded walkthrough saved to `packages/skillforge-depthfusion-mcp-adapter/docs/demo.md`
- Shows: skill invocation → Adapter B → DF → result flow through ACS → InvocationLog
- Written by Greg or claude-code worker with screenshots

**Acceptance for step 2:** all test suites green + demo artefact checked in.

**Recovery:** Adapter B lives entirely in SF monorepo; rollback is `git revert` of the adapter's merge commit. DF standalone unaffected.

---

### Step 3 — SF-with-DF on local (Greg's Mac)

**Commands:**
```bash
# Assumes DF v0.5 is installed (step 1 smoke test passed)
cd /home/gregmorris/projects/skillforge
git pull && pnpm install && pnpm build
pnpm start --filter api &
API_PID=$!

# Invoke the demo skill manually
curl -X POST http://localhost:3000/skills/invoke \
  -H 'Content-Type: application/json' \
  -d '{"skill_id": "demo.depthfusion-recall", "args": {"query": "recent virtual analyst work"}}'
# expect: JSON response with "chunks" array, "quality_report" object, "invocation_id"

# Verify InvocationLog
curl http://localhost:3000/admin/invocation-log?invocation_id=<id>
# expect: hash-chain unbroken, DepthFusion call recorded

# Verify ACS floor enforcement
# Re-run with a skill that sets min_quality_score=0.99 (artificially impossible)
# expect: 422 Unprocessable Entity with FloorViolationError

kill $API_PID
```

**Acceptance for step 3:**
- [ ] Demo skill completes with DF-sourced chunks
- [ ] ACS intercepts a sub-floor result correctly
- [ ] InvocationLog hash chain unbroken across 10 consecutive invocations
- [ ] Capability Router logs show DepthFusion registered as a provider

**Recovery:** local-only, no production impact. Revert SF to the commit before Adapter B landed; DF standalone keeps working.

---

### Step 4 — SF-with-DF on vps-cpu (current Hetzner)

**Commands (SSH into the current VPS):**
```bash
ssh gregmorris@<vps-cpu-host>

# Prereq
which pnpm || (echo "install pnpm first" && exit 1)
pip3 install depthfusion==0.5.0
python3 -m depthfusion.install.install --mode=vps-cpu
# expect: "VPS install complete" + guidance on DEPTHFUSION_HAIKU_ENABLED + DEPTHFUSION_API_KEY

# Set Haiku flags (if desired)
echo "DEPTHFUSION_HAIKU_ENABLED=true" >> ~/.claude/depthfusion.env
echo "DEPTHFUSION_API_KEY=sk-ant-..." >> ~/.claude/depthfusion.env

# Pull SF and install
cd ~/projects/skillforge
git fetch && git checkout v0.5-release     # assuming release branch/tag naming
pnpm install && pnpm build

# Start and smoke-test the api app
pnpm start --filter api &
API_PID=$!

# Re-run the demo flow from step 3, hitting the VPS
# expect: same outcome as step 3
# + Haiku reranker visible in the backend_used field of the metrics

# Small-scale load test — 10 parallel recalls
seq 1 10 | xargs -P 10 -I {} curl -s -X POST http://localhost:3000/skills/invoke \
  -H 'Content-Type: application/json' \
  -d '{"skill_id": "demo.depthfusion-recall", "args": {"query": "test query '{}'"}}'
# expect: all 10 succeed; p95 latency < 3s (Haiku adds ~1-2s per call)

kill $API_PID
```

**Acceptance for step 4:**
- [ ] All 10 parallel recalls succeed
- [ ] `backend_used.reranker == "haiku"` in the metrics JSONL
- [ ] p95 latency ≤ 3s
- [ ] Rate-limit test (force-triggered via throttled Haiku mock endpoint) falls back to null reranker cleanly

**Recovery:** SSH back, `git checkout <previous-tag>` SF and `pip install depthfusion==0.4.0`. Restart the api app. Rollback time: ≤ 5 minutes.

---

### Step 5 — SF-with-DF on vps-gpu (GEX44 once provisioned)

**Provisioning prerequisites** (separate from v0.5 rollout but gating it):

1. Hetzner GEX44 provisioned, Ubuntu 24.04 LTS, NVIDIA drivers 550+ installed.
2. Gemma 3 12B Q4-AWQ pulled to local cache: `hf download google/gemma-3-12b-it-AWQ` (or equivalent variant confirmed at provisioning benchmark).
3. vLLM installed and verified: `vllm --version` returns ≥ 0.6.0.

**GPU readiness check:**
```bash
ssh gregmorris@<gex44-host>
nvidia-smi                                              # expect: RTX 4000 SFF Ada, 20 GB, driver 550+
vllm serve google/gemma-3-12b-it-AWQ --port 8000 &
VLLM_PID=$!
sleep 30                                                # vLLM warm-up

curl http://127.0.0.1:8000/v1/models                    # expect: JSON listing gemma-3-12b
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "google/gemma-3-12b-it-AWQ", "messages": [{"role":"user","content":"Say hi"}]}'
# expect: completion response

# DepthFusion install
pip install depthfusion==0.5.0
python3 -m depthfusion.install.install --mode=vps-gpu
# expect: GPU detected; env written with gemma backend flags

# Smoke test
claude mcp call depthfusion depthfusion_status
# expect: JSON with mode=vps-gpu, backends.reranker=gemma

# Install SF
cd ~/projects/skillforge
git checkout v0.5-release
pnpm install && pnpm build
pnpm start --filter api &
API_PID=$!

# Re-run demo flow
curl -X POST http://localhost:3000/skills/invoke \
  -H 'Content-Type: application/json' \
  -d '{"skill_id": "demo.depthfusion-recall", "args": {"query": "recent work on agent mission control"}}'
# expect: chunks returned; backend_used.reranker == "gemma"

# GEX44-scale load test — 50 parallel recalls
seq 1 50 | xargs -P 50 -I {} curl -s -X POST ...
# expect: all 50 succeed; p95 latency < 5s
#         Gemma OOM does NOT occur (DEPTHFUSION_GEMMA_MAX_CONCURRENT defaults to 4 — queue the rest)

kill $API_PID $VLLM_PID
```

**Acceptance for step 5:**
- [ ] GPU detected by `install --mode=vps-gpu` probe
- [ ] vLLM serving Gemma responds to health + completion calls
- [ ] 50-parallel load test: all succeed; p95 latency ≤ 5s
- [ ] OOM fault injection: Adapter B falls back to Haiku cleanly (requires `DEPTHFUSION_API_KEY` set)
- [ ] Cost comparison report: Gemma-primary vs Haiku-only — cost per 1000 recalls is ≥ 3× lower with Gemma (this is the economic rationale for vps-gpu)

**Recovery:**
- **vLLM crash:** systemd unit (installed in provisioning) auto-restarts; DF falls back to Haiku for the affected calls; resume full routing after restart.
- **GPU hardware fault:** `DEPTHFUSION_MODE=vps-cpu` (edit env), restart SF api app; reverts to step-4 behaviour with no code change.
- **Full rollback:** `pip install depthfusion==0.4.0`, SF `git checkout <previous-tag>`; rollback time ≤ 10 minutes.

---

## 4.3 Per-step validation summary

| Step | Primary metric | Threshold | Action on breach |
|---|---|---|---|
| 1 (standalone, 3 modes) | Smoke test exit code | 0 | Revert to v0.4.x per-mode |
| 2 (adapter impl) | Test suites (unit, integration, E2E) | all pass | Block merge of Adapter B |
| 3 (local SF+DF) | Demo skill latency | < 1s on local | Investigate adapter JSON-RPC roundtrip |
| 4 (vps-cpu) | 10-parallel p95 latency | < 3s | Reduce concurrency ceiling; confirm Haiku quota |
| 5 (vps-gpu) | 50-parallel p95 latency | < 5s | Check vLLM queue depth; increase `DEPTHFUSION_GEMMA_MAX_CONCURRENT` |

---

## 4.4 Observability during rollout

Metrics to watch, by source:

**DepthFusion metrics** (`~/.claude/depthfusion-metrics.jsonl` on each host):
- `latency_ms.total` p50 / p95 / p99 per host
- `backend_used.reranker` histogram — should flip from Haiku (pre-step-5) to Gemma (post-step-5) on the GEX44
- `error` rate — any non-null error is investigated
- `capture_write_rate` — confirm new discoveries being written by the capture mechanisms

**SkillForge metrics** (InvocationLog stream):
- `depthfusion.recall` step success rate
- ACS `FloorViolationError` count — non-zero is expected occasionally; a sudden spike is investigated
- Adapter B `healthCheck()` failure count — 0 is ideal

**Host-level:**
- On GEX44: `nvidia-smi dmon -s mu -c 60` during load test — expect GPU utilisation 40–80% during peak, <5% idle
- Memory: `free -m` — confirm vLLM does not leak toward OOM over an hour of light load

**Pause thresholds (any triggers pauses rollout at that step):**
- P95 latency > 2× the previous step's baseline
- Error rate > 2% of calls
- `FloorViolationError` rate > 10% (indicates the backend is returning low-quality results consistently)
- GPU utilisation stuck at 100% for > 60s (indicates a stuck or runaway vLLM process)

---

## 4.5 Post-rollout monitoring (first week)

Running checks for 7 days after step 5 completes:

**Daily:**
- `cat ~/.claude/depthfusion-metrics.jsonl | .venv/bin/python -m depthfusion.metrics.aggregator` on each host — review summary
- CIQS benchmark run at 05:00 SAST (aligns with the project's existing cron pattern)

**Nightly:**
- Capture-mechanism write-rate trend — confirm mechanisms are writing non-trivial discoveries rather than noise
- Fallback-chain depth — confirm primary backends succeed most of the time (depth > 1 is an event, not a rule)

**Weekly:**
- Greg's manual shadow-test: spend 2 hours per environment on Mon (local), Wed (vps-cpu), Fri (vps-gpu) doing normal work and note recall quality subjectively

**Pause triggers:**
- CIQS regression > 2 points from any daily run → pause and investigate before next day's rollout continuation
- Any category regression > 5 points → immediate rollback of the offending TG

---

## 4.6 Future modules (one-paragraph orientation)

Agreement Automation and Kitabu (both under `/home/gregmorris/projects/`) will be added to SF via the same adapter pattern established by Adapter B. Each will gain a `packages/skillforge-<module>-mcp-adapter/` that bridges the module's MCP surface to SF's StepExecutorRegistry / ACS / Capability Router / InvocationLog. This confirms the integration pattern established in Phase 3 §3.2 is reusable. No change to DepthFusion v0.5 is required to support those later integrations — each module's adapter is independent of the others.

When DepthFusion eventually migrates from SF plugin to Saihai core module (Phase 3 §3.8 evolution path), the Agreement Automation and Kitabu adapters remain as plugins. They can independently graduate to core modules if/when they reach similar API stability.

---

## 4.7 Out-of-scope for this runbook

- Migrating corpus from local → vps-gpu (deferred to v0.6 per Phase 2 §2.3.6)
- Multi-region failover (single-region — Falkenstein — for v0.5)
- Automated rollback via CI/CD — manual per-step revert for v0.5; automation added when SF deployment tooling matures
- Zero-downtime rolling upgrade across multiple SF instances — single-host deployment for v0.5
