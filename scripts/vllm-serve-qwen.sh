#!/usr/bin/env bash
# scripts/vllm-serve-qwen.sh — launch vLLM serving Qwen2.5-14B for the
# DepthFusion vps-gpu deployment tier on cards with 16-20 GB VRAM
# (e.g. RTX 4000 SFF Ada, 19.55 GiB).
#
# Qwen2.5-14B-Instruct-AWQ weighs ~8.5 GB, leaving ~9 GB for KV cache at
# 0.90 gpu-memory-utilization on a 19.55 GiB card. Use this instead of
# vllm-serve-gemma.sh when Gemma 4 26B AWQ (~13 GB) OOMs on load.
#
# Usage (one-shot):
#   ./scripts/vllm-serve-qwen.sh
#
# Usage (systemd):
#   cp scripts/vllm-qwen.service /etc/systemd/system/
#   systemctl daemon-reload
#   systemctl enable --now vllm-qwen
#   journalctl -u vllm-qwen -f
#
# Env vars (all optional):
#   DEPTHFUSION_GEMMA_MODEL         default: Qwen/Qwen2.5-14B-Instruct-AWQ
#   DEPTHFUSION_GEMMA_PORT          default: 8000
#   DEPTHFUSION_GEMMA_HOST          default: 127.0.0.1
#   DEPTHFUSION_GEMMA_GPU_MEMORY    default: 0.90
#   DEPTHFUSION_GEMMA_MAX_MODEL_LEN default: 8192
#   DEPTHFUSION_GEMMA_EXTRA_ARGS    additional vllm serve flags, passed verbatim

set -euo pipefail

MODEL="${DEPTHFUSION_GEMMA_MODEL:-Qwen/Qwen2.5-14B-Instruct-AWQ}"
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

echo "[vllm-serve-qwen] starting vLLM"
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
    --quantization awq \
    --host "${HOST}" \
    --port "${PORT}" \
    --gpu-memory-utilization "${GPU_MEMORY}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --max-num-seqs 32 \
    ${EXTRA_ARGS}
