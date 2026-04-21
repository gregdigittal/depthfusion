# vps-gpu Quickstart — GPU-accelerated DepthFusion + research tools

> **Target:** Hetzner GEX44 (NVIDIA RTX 4000 SFF Ada) or any CUDA-12+ host
> **Time:** ~4 hours (mostly GPU-driver + Gemma model download)
> **Produces:** working DepthFusion in `vps-gpu` mode + weekly regression monitor + initial prompt corpus
> **Estimated first-run size on disk:** ~30 GB (vLLM + Gemma 3 12B AWQ + sentence-transformers)

This is the GPU-enabled counterpart to
[`vps-cpu-quickstart.md`](vps-cpu-quickstart.md). At the end you'll
have:

- DepthFusion in `vps-gpu` mode (local Gemma LLM + local embeddings)
- vLLM serving Gemma 3 12B AWQ as a systemd service
- Weekly regression monitor scheduled
- Initial prompt corpus mined

**If you are migrating** from an existing vps-cpu install (moving
data from a current VPS to the new GPU box), follow
[`../runbooks/gpu-vps-migration.md`](../runbooks/gpu-vps-migration.md)
instead — it includes the data-migration steps this quickstart omits.

---

## 0. Prerequisites

```bash
# Hardware
nvidia-smi                  # must show GPU; ≥ 20 GB VRAM for Gemma 3 12B AWQ
nvcc --version              # CUDA 12.0+

# Software
python3 --version           # 3.10+
pip --version
systemctl status            # root-level systemd for vLLM service
systemctl --user status     # user systemd for weekly timer

# Network bandwidth
# Model download is ~7 GB; plan accordingly if on metered transit
```

You'll also need:
- `DEPTHFUSION_API_KEY` (for the Haiku fallback path when Gemma is
  down; not mandatory but strongly recommended)
- ≥ 30 GB free disk space (vLLM, torch CUDA build, Gemma weights)

---

## 1. Clone and install with GPU extras

```bash
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion

# [vps-gpu] pulls in sentence-transformers + chromadb + vllm
# (vllm install compiles CUDA kernels — can take 10+ minutes)
pip install -e '.[vps-gpu]'
```

**Verify:**

```bash
python3 -c "import depthfusion, sentence_transformers, vllm; print('ok')"
# -> ok
```

If `import vllm` fails, the most common cause is a CUDA version
mismatch. Check `nvcc --version` matches the one `vllm` expects (see
[vllm install docs]).

[vllm install docs]: https://docs.vllm.ai/en/latest/getting_started/installation.html

---

## 2. Set up vLLM as a systemd service

A ready-to-use service file ships in the repo:

```bash
# Review it first — paths and model choice may need adjustment
less ~/projects/depthfusion/scripts/vllm-gemma.service

# Install (requires root)
sudo cp ~/projects/depthfusion/scripts/vllm-gemma.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vllm-gemma
sudo systemctl start vllm-gemma
```

First start downloads the Gemma 3 12B AWQ weights (~7 GB). Wait for
the server to be ready:

```bash
# Poll until /v1/models responds
until curl -sf http://127.0.0.1:8000/v1/models > /dev/null; do
  echo "Waiting for vLLM..."
  sleep 5
done
echo "vLLM is serving"
```

**Verify:**

```bash
curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool
# Should list the Gemma model

sudo journalctl -u vllm-gemma --since="10 minutes ago" | grep -iE "error|loaded"
# Should show "Model loaded" without errors
```

If vLLM fails to start, common causes:
- OOM on load → edit `--gpu-memory-utilization` in the service file
  (try 0.80 if the GPU is shared with anything else)
- Port conflict → `sudo lsof -i :8000` to find the squatter
- Model-path issue → check `~/.cache/huggingface/` for partial
  downloads; delete and retry

---

## 3. Run the interactive installer in vps-gpu mode

```bash
python3 -m depthfusion.install.install --mode=vps-gpu
```

The installer auto-probes for:
- `nvidia-smi` (should pass, GPU is present)
- `sentence-transformers` (pass, installed via `[vps-gpu]` extra)
- vLLM at `DEPTHFUSION_GEMMA_URL` (pass, service is running)

Then runs `run_vps_gpu_smoke()`: three-probe smoke test that
actually executes nvidia-smi, imports sentence-transformers,
and issues a one-shot Gemma completion. Failure is non-fatal —
install completes, smoke can be re-run later.

**Verify installation:**

```bash
python3 -c "
from depthfusion.backends.factory import get_backend
for cap in ('reranker', 'extractor', 'linker', 'summariser', 'decision_extractor', 'embedding'):
    b = get_backend(cap)
    print(f'{cap:22} -> {b.name:20} (healthy={b.healthy()})')
"
```

Expected:
- LLM capabilities → `gemma` (healthy)
- `embedding` → `local` (healthy)

---

## 4. Install the research tools

```bash
bash ~/projects/depthfusion/scripts/install-research-tools.sh
```

This is the **same script** as the vps-cpu path — the tools are
mode-agnostic.

**Verify:**

```bash
systemctl --user list-timers ciqs-weekly.timer --no-pager
ls -lh ~/.local/share/depthfusion/corpus/
```

---

## 5. Smoke test the full pipeline

```bash
# End-to-end recall via the real MCP interface
python3 -c "
from depthfusion.mcp.server import _tool_recall
import json
result = json.loads(_tool_recall({'query': 'GPU verification test', 'top_k': 3}))
print(f'blocks returned: {len(result.get(\"blocks\", []))}')
print(f'error: {result.get(\"error\", \"none\")}')
"

# Sanity-check latency: p95 on GPU should be much lower than on CPU
python3 -c "
from depthfusion.metrics.collector import MetricsCollector
from depthfusion.metrics.aggregator import MetricsAggregator
import json
s = MetricsAggregator(MetricsCollector()).backend_summary()
print(json.dumps(s, indent=2, default=str))
"
```

---

## 6. Record the baseline CIQS run

If you're running the parallel-comparison plan, now is the moment
to capture a 3-run CIQS baseline on this GPU host:

```bash
for i in 1 2 3; do
  python3 scripts/ciqs_harness.py run --mode vps-gpu --run $i
done
# Then score each via the operator/judge workflow — see docs/benchmarks/README.md
```

Scored files land in `docs/benchmarks/`. When you've also collected
baseline runs on the vps-cpu host, `scripts/ciqs_compare.py` produces
the honest delta report. See the end of the vps-cpu quickstart for
the exact invocation.

---

## Done

You now have:

- ✅ DepthFusion in `vps-gpu` mode (local Gemma + local embeddings)
- ✅ vLLM serving Gemma as a systemd service (root-level)
- ✅ Weekly regression monitor scheduled (user-level)
- ✅ Initial prompt corpus mined
- ✅ All three research tools available under `scripts/`

## Operational notes

**vLLM service health** — check occasionally:

```bash
sudo systemctl status vllm-gemma
sudo journalctl -u vllm-gemma -n 100 --no-pager
```

If vLLM OOMs or the model unloads, `GemmaBackend.healthy()` returns
`False` and the factory returns `NullBackend` for LLM capabilities
until vLLM recovers. The `FallbackChain` class (shipped in v0.6.0a1,
wired-by-default in v0.6.0 stable) will make this graceful — until
then, check `systemctl status vllm-gemma` if recall quality drops
suddenly.

**Model updates** — editing `scripts/vllm-gemma.service` to point at
a newer Gemma checkpoint requires:

```bash
sudo systemctl daemon-reload
sudo systemctl restart vllm-gemma
```

**Rollback** — if this GPU install misbehaves and you need to fall
back to the vps-cpu host temporarily:

```bash
# On the GPU host
sudo systemctl stop vllm-gemma
sudo systemctl disable vllm-gemma
# Traffic directed to the other host by whatever controls your routing
```

Data on the GPU host (`~/.claude/shared/discoveries/`, etc.) is
preserved — rollback is a routing decision, not a data migration.
