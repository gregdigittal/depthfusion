# GPU VPS Migration Runbook

> **Owner:** E-19 (v0.5 GPU-Enabled LLM Routing)
> **Prepared:** 2026-04-21 — before migration, ratified pre-flight
> **Target host:** Hetzner GEX44 (NVIDIA RTX 4000 SFF Ada, 20 GB VRAM, CUDA 12+)
> **Source host:** current VPS running DepthFusion v0.5.2 in `vps-cpu` mode
> **Target release:** v0.6.0-alpha (GPU rollout)

This runbook takes DepthFusion from a working `vps-cpu` installation to a working `vps-gpu` installation with zero data loss and a documented rollback path. Every step has a verification command. If a verification fails, **stop and consult the rollback section** — do not proceed.

---

## 0. Before you start

Read all of section 1 and section 6 first. The rollback plan assumes you took the snapshots listed in section 1; skipping them means you cannot cleanly roll back.

Estimated total time: **2–4 hours** (mostly GPU driver + vLLM warmup).

### Prerequisites

| Item | How to confirm |
|---|---|
| Target VPS provisioned with NVIDIA driver | `ssh new-vps 'nvidia-smi'` returns GPU info |
| CUDA 12+ on target | `ssh new-vps 'nvcc --version'` shows ≥ 12.0 |
| SSH access both hosts | `ssh current-vps true && ssh new-vps true` exits 0 |
| Python 3.10+ on target | `ssh new-vps 'python3 --version'` shows ≥ 3.10 |
| Current DepthFusion version | On current-vps: `python -c "import depthfusion; print(depthfusion.__version__)"` shows 0.5.2+ |
| Claude Code API key | `grep DEPTHFUSION_API_KEY ~/.claude/depthfusion.env` on current-vps |

---

## 1. Pre-migration snapshot (on current-vps)

**Do not skip.** The rollback plan in §6 depends on these artefacts existing.

```bash
# On current-vps
mkdir -p ~/depthfusion-migration-backup/
cd ~/depthfusion-migration-backup/

# 1a. Discoveries directory (source of truth for captured knowledge)
cp -r ~/.claude/shared/discoveries/ ./discoveries/
echo "Backed up $(find ./discoveries -name '*.md' | wc -l) discovery files"

# 1b. Metrics (for post-migration comparison)
cp -r ~/.claude/depthfusion-metrics/ ./metrics/
echo "Backed up $(find ./metrics -name '*.jsonl' | wc -l) metric streams"

# 1c. Sessions (ChromaDB Tier 2 corpus if any)
cp -r ~/.claude/depthfusion-chroma/ ./chroma/ 2>/dev/null || echo "No ChromaDB dir — tier-2 not in use"

# 1d. Environment file (API keys + mode config)
cp ~/.claude/depthfusion.env ./depthfusion.env.backup

# 1e. Git state of the local depthfusion checkout (if editable install)
cd ~/projects/depthfusion 2>/dev/null && {
  git rev-parse HEAD > ~/depthfusion-migration-backup/git-head.txt
  git status --short > ~/depthfusion-migration-backup/git-status.txt
}
cd -

# 1f. Baseline metrics snapshot for post-migration comparison
python -c "
from depthfusion.metrics.aggregator import MetricsAggregator
from depthfusion.metrics.collector import MetricsCollector
from datetime import date, timedelta
import json
agg = MetricsAggregator(MetricsCollector())
for delta in range(7, 0, -1):
    d = date.today() - timedelta(days=delta)
    s = agg.backend_summary(d)
    if s.get('per_backend'):
        print(json.dumps({'date': d.isoformat(), **s}, indent=2))
" > ~/depthfusion-migration-backup/pre-migration-backend-summary.json

# 1g. Tarball for SCP
cd ~
tar czf depthfusion-migration-backup.tar.gz depthfusion-migration-backup/
ls -lh depthfusion-migration-backup.tar.gz
```

**Verify:** `tar tzf depthfusion-migration-backup.tar.gz | head -20` shows the files.

### Record for rollback

```bash
# Note these — you'll need them in §6 if something goes wrong.
echo "Current VPS version: $(python -c 'import depthfusion; print(depthfusion.__version__)')"
echo "Current mode: $(grep DEPTHFUSION_MODE ~/.claude/depthfusion.env)"
echo "Pre-migration discovery count: $(find ~/.claude/shared/discoveries -name '*.md' | wc -l)"
echo "Pre-migration metric stream count: $(find ~/.claude/depthfusion-metrics -name '*.jsonl' | wc -l)"
```

---

## 2. Transfer to new-vps

```bash
# From current-vps (or your workstation)
scp ~/depthfusion-migration-backup.tar.gz new-vps:~/

# On new-vps
mkdir -p ~/.claude/
cd ~/
tar xzf depthfusion-migration-backup.tar.gz
ls depthfusion-migration-backup/
# Expected contents: discoveries/ metrics/ chroma/ (if tier-2) depthfusion.env.backup pre-migration-backend-summary.json git-head.txt git-status.txt
```

---

## 3. Install DepthFusion on new-vps (in `vps-gpu` mode)

### 3a. Install Python package

```bash
# On new-vps
python3 -m venv ~/.venvs/depthfusion
source ~/.venvs/depthfusion/bin/activate
pip install --upgrade pip

# Option A: from PyPI (once published)
pip install 'depthfusion[vps-gpu]==0.5.2'

# Option B: from source (if using editable install on current-vps)
git clone git@github.com:gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion
git checkout $(cat ~/depthfusion-migration-backup/git-head.txt)
pip install -e '.[vps-gpu]'
```

**Verify:** `python -c 'import depthfusion; print(depthfusion.__version__)'` prints `0.5.2` (or the version you pinned).

### 3b. Install vLLM + serve Gemma

```bash
# vLLM is a [vps-gpu] extra dep
python -c "import vllm; print(vllm.__version__)"

# Install the service file (edit path to your venv if different)
sudo cp ~/projects/depthfusion/scripts/vllm-gemma.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vllm-gemma
sudo systemctl start vllm-gemma

# Wait ~30-60s for the model to load
until curl -sf http://127.0.0.1:8000/v1/models > /dev/null; do
  echo "Waiting for vLLM..."
  sleep 5
done
echo "vLLM is serving"
```

**Verify:**
```bash
curl -s http://127.0.0.1:8000/v1/models | python -m json.tool
# Should list the Gemma model as "data[0].id"

sudo journalctl -u vllm-gemma --since="5 minutes ago" | grep -iE "error|loaded"
# Should show "Model loaded" without errors
```

### 3c. Run the `vps-gpu` installer

```bash
python -m depthfusion.install.install --mode=vps-gpu
```

This will:
- Probe `nvidia-smi` (should succeed)
- Probe `sentence-transformers` (installed via `[vps-gpu]` extra)
- Probe vLLM at `DEPTHFUSION_GEMMA_URL` (defaults to `http://127.0.0.1:8000/v1`)
- Write `~/.claude/depthfusion.env` with mode and backend assignments
- Run `run_vps_gpu_smoke()` — three-probe smoke test (GPU + embeddings + Gemma roundtrip)

If the smoke test fails, the installer prints which probe failed. See §5 for per-probe troubleshooting.

### 3d. Restore data from backup

```bash
# Discoveries first — this is the irreplaceable data
cp -r ~/depthfusion-migration-backup/discoveries/* ~/.claude/shared/discoveries/

# Metrics (for continuity in backend_summary)
cp -r ~/depthfusion-migration-backup/metrics/* ~/.claude/depthfusion-metrics/

# ChromaDB Tier 2 (if in use)
if [ -d ~/depthfusion-migration-backup/chroma ]; then
  cp -r ~/depthfusion-migration-backup/chroma/* ~/.claude/depthfusion-chroma/
fi

# DO NOT restore the env file verbatim — it has vps-cpu settings.
# The installer (§3c) wrote a fresh one with vps-gpu mode.
# Instead, manually copy ONLY the API key:
grep DEPTHFUSION_API_KEY ~/depthfusion-migration-backup/depthfusion.env.backup \
  >> ~/.claude/depthfusion.env
```

**Verify:**
```bash
# Discovery count matches pre-migration
find ~/.claude/shared/discoveries -name '*.md' | wc -l
# Should equal the pre-migration count recorded in §1

# API key present and readable
grep -q DEPTHFUSION_API_KEY ~/.claude/depthfusion.env && echo OK
```

---

## 4. Validation

Work through this checklist in order. Each step gates the next.

### 4a. Health check

```bash
python -c "
from depthfusion.backends.factory import get_backend
for cap in ('reranker', 'extractor', 'linker', 'summariser', 'decision_extractor', 'embedding'):
    b = get_backend(cap)
    print(f'{cap:22} -> {b.name:20} (healthy={b.healthy()})')
"
```

**Expected:**
- LLM capabilities (`reranker`, `extractor`, `linker`, `summariser`, `decision_extractor`) → `gemma` (healthy=True)
- `embedding` → `local` (healthy=True)

If any capability routes to `null`, the installer-time health check failed silently. Check `~/.claude/depthfusion.env` and `sudo systemctl status vllm-gemma`.

### 4b. End-to-end recall

```bash
python -c "
from depthfusion.mcp.server import _tool_recall
import json
result = json.loads(_tool_recall({'query': 'migration verification test', 'top_k': 3}))
print(f'blocks: {len(result.get(\"blocks\", []))}')
print(f'error:  {result.get(\"error\", \"none\")}')
"
```

**Expected:** Some number of blocks (exact count depends on corpus), no error.

### 4c. Capture round-trip

Edit the MCP server via Claude Code, run a small task end-to-end that produces a discovery. After it completes:

```bash
# New discovery files should appear
find ~/.claude/shared/discoveries -name "$(date +%F)-*.md" | head -5

# Capture stream should have entries
tail -5 ~/.claude/depthfusion-metrics/$(date +%F)-capture.jsonl
```

### 4d. Latency sanity

```bash
python -c "
from depthfusion.metrics.collector import MetricsCollector
from depthfusion.metrics.aggregator import MetricsAggregator
import json
s = MetricsAggregator(MetricsCollector()).backend_summary()
print(json.dumps(s, indent=2, default=str))
"
```

Compare to `~/depthfusion-migration-backup/pre-migration-backend-summary.json`. Expect:
- p95 latency on `reranker::gemma` in the low hundreds of ms (vLLM on GPU is fast)
- `embedding::local` latency well under 100 ms for small batches
- Zero `error_count` on the first hour post-migration (indicates stable config)

If p95 is > 1500 ms on any capability, **do not proceed to §4e**. Investigate — likely causes: vLLM on CPU not GPU, sentence-transformers falling back to CPU, network hop between processes.

### 4e. CIQS baseline (optional but recommended)

Run the benchmark harness for a Category A baseline on vps-gpu:

```bash
python ~/projects/depthfusion/scripts/ciqs_harness.py run \
    --battery ~/projects/depthfusion/docs/benchmarks/prompts/ciqs-battery.yaml \
    --mode vps-gpu --run 1
```

Score the template, run twice more, then:

```bash
python ~/projects/depthfusion/scripts/ciqs_summarise.py --mode vps-gpu \
    ~/projects/depthfusion/docs/benchmarks/2026-*-vps-gpu-run*-scored.jsonl \
    --out ~/projects/depthfusion/docs/benchmarks/$(date +%F)-vps-gpu-summary.md
```

Compare Category A to the vps-cpu baseline. **Expected delta:** +3 points or more (S-43 AC-2 target). If less, the embedding backend or fusion weights may not be engaged — check `DEPTHFUSION_EMBEDDING_BACKEND=local` in the env file.

---

## 5. Troubleshooting probes

### `nvidia-smi` not found / no GPU

```bash
nvidia-smi || echo "GPU driver missing"
```

The host provisioning is wrong — contact hoster. Cannot proceed without a working NVIDIA driver.

### vLLM service won't start

```bash
sudo journalctl -u vllm-gemma -n 50 --no-pager
```

Common causes:
- **OOM on model load** — reduce `--gpu-memory-utilization` in `vllm-serve-gemma.sh`; try 0.80 if using a 20 GB card alongside something else
- **Wrong model path** — the model must be present on disk or downloadable; check `~/.cache/huggingface/` for partial downloads
- **Port conflict** — `sudo lsof -i :8000` to find the squatter

### `LocalEmbeddingBackend` reports unhealthy

```bash
python -c "import sentence_transformers; print(sentence_transformers.__version__)"
```

If ImportError, the `[vps-gpu]` extra didn't install cleanly. Re-run `pip install -e '.[vps-gpu]'` and check pip output for compile errors (sentence-transformers pulls in torch — CUDA build takes time on first install).

### `GemmaBackend` reports unhealthy

```bash
curl -s http://127.0.0.1:8000/v1/models
```

Non-200 → vLLM isn't serving. Return to §3b.

### Discoveries missing after migration

The restore step in §3d uses `cp -r`. If the source was empty (§1a), there's nothing to restore. Check:

```bash
ls ~/depthfusion-migration-backup/discoveries/ | head -5
# Should show .md files with dated names
```

If empty, §1a didn't capture anything. Either current-vps genuinely had zero discoveries (unlikely if DepthFusion was in use), or the path differs. Check `~/.claude/shared/discoveries/` on current-vps.

---

## 6. Rollback

If any validation step in §4 fails decisively and cannot be fixed within ~30 minutes, roll back:

### 6a. Stop the new-vps deployment

```bash
# On new-vps
sudo systemctl stop vllm-gemma
sudo systemctl disable vllm-gemma
```

### 6b. Return to current-vps

Current-vps is untouched by the migration. Just go back to using it:

```bash
# On your workstation, update ~/.ssh/config or DNS to point back to current-vps
# Or: update any client that was about to cut over to new-vps.
```

### 6c. Investigate

Collect the failing artefacts:

```bash
# On new-vps
sudo journalctl -u vllm-gemma > ~/migration-failure-vllm.log
cp ~/.claude/depthfusion.env ~/migration-failure-env.txt
tail -200 ~/.claude/depthfusion-metrics/$(date +%F)-recall.jsonl \
  > ~/migration-failure-recall.jsonl

scp ~/migration-failure-*.{log,txt,jsonl} current-vps:~/migration-failures/
```

Open an issue or backlog story with these artefacts attached. Do NOT retry the migration on the same host without understanding what failed — the same failure will recur.

### 6d. When to try again

- GPU / driver issue → after fresh provisioning
- vLLM / model issue → after updating `scripts/vllm-serve-gemma.sh` and testing locally
- DepthFusion issue → after the fix is released to main and tagged

---

## 7. Post-migration tasks (first week)

- [ ] Keep `depthfusion-migration-backup.tar.gz` on current-vps **for at least 30 days** — do not delete until new-vps has been stable for a week
- [ ] Run §4d (backend_summary) daily for the first 3 days — watch for latency drift or error counts rising
- [ ] Run the first pass of the dogfood telemetry runbook (`docs/runbooks/dogfood-telemetry.md`) starting day 1
- [ ] Run the 3-run CIQS battery per §4e — commit results under `docs/benchmarks/`
- [ ] Tag v0.6.0-alpha once the above are all green

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **vps-gpu mode** | DepthFusion configuration where LLM capabilities route to a local Gemma via vLLM and embeddings route to on-box sentence-transformers. |
| **Gemma** | Google's open-weight LLM family; v0.5 uses Gemma 3 12B AWQ by default, served via vLLM. |
| **vLLM** | OpenAI-compatible LLM server that runs quantised models on GPU with high throughput. |
| **AWQ** | Activation-aware Weight Quantisation — compresses a 12B model to fit in 20 GB VRAM. |
| **Smoke test** | `depthfusion.install.smoke.run_vps_gpu_smoke()` — three-probe check: nvidia-smi runs, sentence-transformers imports and loads a model, vLLM returns a valid completion. |
| **FallbackChain** | (S-44 AC-3 deliverable) Backend wrapper that falls through to Haiku → Null on typed errors. Enable via `DEPTHFUSION_FALLBACK_CHAIN_ENABLED=true` once available. |

---

## Appendix — rollback file template

Save at `~/.depthfusion-rollback/{date}-gpu-migration.sh`:

```bash
#!/bin/bash
# Rollback: GPU VPS migration on {date}
# Captured state: {current-vps SHA, discovery count, mode}
# If new-vps validation fails:

ssh new-vps 'sudo systemctl stop vllm-gemma'
ssh new-vps 'sudo systemctl disable vllm-gemma'

# Resume using current-vps. Nothing on current-vps was modified during migration.
echo "Rollback complete. Resume using current-vps."
```
