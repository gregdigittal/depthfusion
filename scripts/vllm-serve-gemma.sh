#!/usr/bin/env bash
# scripts/vllm-serve-gemma.sh — launch vLLM serving Gemma for the DepthFusion
# vps-gpu deployment tier (GEX44, RTX 4000 SFF Ada, 20 GB VRAM).
#
# vLLM exposes an OpenAI-compatible /v1/chat/completions endpoint. GemmaBackend
# in src/depthfusion/backends/gemma.py posts to this endpoint. The backend
# reads DEPTHFUSION_GEMMA_URL (default http://127.0.0.1:8000/v1) — this script
# binds to 127.0.0.1:8000 by default to match.
#
# Usage (one-shot):
#   ./scripts/vllm-serve-gemma.sh
#
# Usage (systemd):
#   cp scripts/vllm-gemma.service /etc/systemd/system/
#   systemctl daemon-reload
#   systemctl enable --now vllm-gemma
#   journalctl -u vllm-gemma -f
#
# Env vars (all optional):
#   DEPTHFUSION_GEMMA_MODEL         default: google/gemma-3-12b-it-AWQ
#   DEPTHFUSION_GEMMA_PORT          default: 8000
#   DEPTHFUSION_GEMMA_HOST          default: 127.0.0.1
#   DEPTHFUSION_GEMMA_GPU_MEMORY    default: 0.90 (fraction of GPU VRAM to reserve)
#   DEPTHFUSION_GEMMA_MAX_MODEL_LEN default: 8192
#   DEPTHFUSION_GEMMA_EXTRA_ARGS    additional vllm-serve flags, passed verbatim

set -euo pipefail

MODEL="${DEPTHFUSION_GEMMA_MODEL:-google/gemma-3-12b-it-AWQ}"
PORT="${DEPTHFUSION_GEMMA_PORT:-8000}"
HOST="${DEPTHFUSION_GEMMA_HOST:-127.0.0.1}"
GPU_MEMORY="${DEPTHFUSION_GEMMA_GPU_MEMORY:-0.90}"
MAX_MODEL_LEN="${DEPTHFUSION_GEMMA_MAX_MODEL_LEN:-8192}"
EXTRA_ARGS="${DEPTHFUSION_GEMMA_EXTRA_ARGS:-}"

if ! command -v vllm >/dev/null 2>&1; then
    echo "error: vllm is not on PATH. install with 'pip install vllm' in the depthfusion[vps-gpu] env." >&2
    exit 127
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "warning: nvidia-smi not found. vLLM will fail to start without a CUDA GPU." >&2
fi

echo "[vllm-serve-gemma] starting vLLM"
echo "  model:          ${MODEL}"
echo "  host:           ${HOST}"
echo "  port:           ${PORT}"
echo "  gpu-memory:     ${GPU_MEMORY}"
echo "  max-model-len:  ${MAX_MODEL_LEN}"
if [[ -n "${EXTRA_ARGS}" ]]; then
    echo "  extra-args:     ${EXTRA_ARGS}"
fi
echo ""

# shellcheck disable=SC2086
exec vllm serve "${MODEL}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --gpu-memory-utilization "${GPU_MEMORY}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --trust-remote-code \
    ${EXTRA_ARGS}
