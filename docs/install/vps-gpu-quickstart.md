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

# Python — 3.10 or newer; "or newer" means any modern Python is fine.
# Ubuntu 24.04 ships 3.12 as default. Don't try to install 3.10
# specifically on 24.04 — it's not in the repos and isn't needed.
python3 --version

# Build tools + venv module (fresh-install gotcha — venv isn't
# pre-installed on Ubuntu 24.04, and chromadb / hnswlib need compile tools)
sudo apt update
sudo apt install -y python3-full python3-venv build-essential python3-dev

# systemd — both root-level (for vLLM) and user-level (for weekly timer)
systemctl status                           # root systemd (for vLLM)
systemctl --user status || sudo loginctl enable-linger $USER

# Network bandwidth — model download is ~7 GB; plan for metered transit
```

You'll also need:
- `DEPTHFUSION_API_KEY` — **strongly recommended** even on GPU hosts;
  powers the Haiku fallback path when Gemma is down or OOM
- ≥ 30 GB free disk space (vLLM, torch CUDA build, Gemma weights)

---

## 1. Clone, create a venv, and install with GPU extras

On modern Ubuntu, pip refuses system-wide installs (PEP 668). You
must install into a virtualenv.

### 1a. Clone and venv

```bash
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion

python3 -m venv ~/venvs/depthfusion
source ~/venvs/depthfusion/bin/activate
# Prompt should now show (depthfusion) at the front
```

### 1b. Install with vps-gpu extras

```bash
pip install --upgrade pip
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

### 1c. Make the venv auto-activate in new shells

```bash
grep -q "# depthfusion venv auto-activate" ~/.bashrc || cat >> ~/.bashrc <<'EOF'

# depthfusion venv auto-activate
if [ -z "$VIRTUAL_ENV" ] && [ -f "$HOME/venvs/depthfusion/bin/activate" ]; then
    source "$HOME/venvs/depthfusion/bin/activate"
fi

# depthfusion PS1 prefix enforcement — robust against whatever the
# activate script does or doesn't do with PS1.
if [ -n "$VIRTUAL_ENV" ] && [[ "$PS1" != *"(depthfusion)"* ]]; then
    PS1="(depthfusion) $PS1"
fi
EOF
```

> **⚠ Do NOT `source ~/.bashrc` while the venv is already active.**
> Use `deactivate; exec bash` instead — sourcing on an active venv
> leaves the shell in a half-activated state (`$VIRTUAL_ENV` set
> but `$PATH` clobbered).

**Test in a fresh shell:**

```bash
deactivate 2>/dev/null; exec bash
which python3       # should print ~/venvs/depthfusion/bin/python3
echo "$VIRTUAL_ENV" # should print /home/$USER/venvs/depthfusion
```

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

## 3. Run the installer, then add credentials + shell integration

> **Order matters.** The installer **overwrites** `~/.claude/depthfusion.env`
> with mode-specific defaults — any credentials you add beforehand get
> wiped. Run the installer FIRST, then append your credentials.
> (Tracked as E-17 S-68; future installer will merge instead of
> overwrite.)

### 3a. Run the installer in vps-gpu mode

```bash
python3 -m depthfusion.install.install --mode=vps-gpu
```

The installer auto-probes for:
- `nvidia-smi` (should pass, GPU is present)
- `sentence-transformers` (pass, installed via `[vps-gpu]` extra)
- vLLM at `DEPTHFUSION_GEMMA_URL` (pass, service is running)

Then runs `run_vps_gpu_smoke()`: three-probe smoke test that actually
executes nvidia-smi, imports sentence-transformers, and issues a
one-shot Gemma completion. Failure is non-fatal — install completes,
smoke can be re-run later.

### 3b. Append credentials + enable flag to the env file

Even on a GPU host where Gemma is primary, setting `DEPTHFUSION_API_KEY`
is strongly recommended so the `FallbackChain` (v0.6.0a1) can fall
through to Haiku if Gemma is OOM, rate-limited, or down.

```bash
cat >> ~/.claude/depthfusion.env <<'EOF'
DEPTHFUSION_API_KEY=sk-ant-api03-your-real-key-here
DEPTHFUSION_HAIKU_ENABLED=true
EOF
chmod 600 ~/.claude/depthfusion.env
```

> **⚠ Billing safety — use `DEPTHFUSION_API_KEY`, NOT `ANTHROPIC_API_KEY`.**
> Setting `ANTHROPIC_API_KEY` flips Claude Code's billing to
> pay-per-token for all usage, not just DepthFusion. The installer
> refuses to use `ANTHROPIC_API_KEY` by design (E-12 S-22).

### 3c. Make the shell auto-load the env file

Writing to `depthfusion.env` doesn't put variables in your shell
environment — nothing auto-sources it. Python tools read `os.environ`,
so without this block the factory check in 3e will route LLM
capabilities to `null` despite the env file being correct.

```bash
grep -q "# depthfusion env auto-source" ~/.bashrc || cat >> ~/.bashrc <<'EOF'

# depthfusion env auto-source — export vars from the config file
if [ -f "$HOME/.claude/depthfusion.env" ]; then
    set -a
    source "$HOME/.claude/depthfusion.env"
    set +a
fi
EOF
```

### 3d. Reload the shell

```bash
deactivate 2>/dev/null; exec bash

# Confirm the env vars are live
echo "haiku:     $DEPTHFUSION_HAIKU_ENABLED"
echo "key:       ${DEPTHFUSION_API_KEY:0:16}..."
echo "gemma URL: ${DEPTHFUSION_GEMMA_URL:-not set (default http://127.0.0.1:8000/v1)}"
```

### 3e. Verify the install

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

If LLM capabilities route to `haiku` instead of `gemma`, vLLM isn't
reachable — check `curl http://127.0.0.1:8000/v1/models` and
`sudo systemctl status vllm-gemma`. This is expected behaviour
(the factory falls back to Haiku when Gemma is unhealthy) so the
install is usable, just not running on GPU until vLLM comes up.

---

## 4. Register the MCP server with Claude Code

**Important — this step is easy to miss.** The previous step set up
hooks, env config, and the vps-gpu smoke test, but did NOT register
DepthFusion's MCP tools (recall, confirm-discovery, prune) with
Claude Code. Without this, Claude Code sessions won't have access to
the tools even though the library is installed.

```bash
# Register DepthFusion as an MCP server at user scope.
claude mcp add depthfusion --scope user -- python3 -m depthfusion.mcp.server
```

**Verify:**

```bash
claude mcp list
# DepthFusion should appear. If not, check the command ran without error.
```

> **Why isn't this automatic?** The installer doesn't invoke
> `claude mcp add` today — tracked as a v0.7 polish item (see
> `BACKLOG.md` E-17 S-67). Once that lands, step 4 will be folded
> into step 3.

---

## 5. Install the research tools

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

## 6. Smoke test the full pipeline

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

## 7. Record the baseline CIQS run

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
