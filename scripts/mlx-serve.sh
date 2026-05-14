#!/usr/bin/env bash
# scripts/mlx-serve.sh — launch mlx_lm.server for the DepthFusion mac-mlx
# deployment tier (Apple Silicon, unified memory).
#
# mlx_lm.server exposes an OpenAI-compatible /v1/chat/completions endpoint.
# GemmaBackend in src/depthfusion/backends/gemma.py posts to this endpoint
# identically to how it talks to vLLM. The backend reads DEPTHFUSION_GEMMA_URL
# (default http://127.0.0.1:8000/v1) — this script binds to 127.0.0.1:8000.
#
# Models are downloaded from HuggingFace on first run and cached in
# ~/.cache/huggingface/hub/. After the first download subsequent starts
# are fast (local load only).
#
# Usage:
#   ./scripts/mlx-serve.sh                     # interactive model selection
#   ./scripts/mlx-serve.sh --model mlx-community/Qwen2.5-14B-Instruct-4bit
#   DEPTHFUSION_GEMMA_MODEL=<id> ./scripts/mlx-serve.sh
#
# Env vars (all optional):
#   DEPTHFUSION_GEMMA_MODEL   model ID (HuggingFace or local path)
#   DEPTHFUSION_GEMMA_PORT    default: 8000
#   DEPTHFUSION_GEMMA_HOST    default: 127.0.0.1
#   MLX_EXTRA_ARGS            additional mlx_lm.server flags, passed verbatim

set -euo pipefail

PORT="${DEPTHFUSION_GEMMA_PORT:-8000}"
HOST="${DEPTHFUSION_GEMMA_HOST:-127.0.0.1}"
EXTRA_ARGS="${MLX_EXTRA_ARGS:-}"

# ---------------------------------------------------------------------------
# Check dependencies
# ---------------------------------------------------------------------------

if ! python3 -c "import mlx_lm" 2>/dev/null; then
    echo "error: mlx_lm is not installed." >&2
    echo "       Install with: pip install -e .[mac-mlx]" >&2
    echo "       or:           pip install mlx-lm" >&2
    exit 127
fi

if [[ "$(uname -s)" != "Darwin" ]] || [[ "$(uname -m)" != "arm64" ]]; then
    echo "error: mlx-serve.sh requires Apple Silicon (arm64 macOS)." >&2
    echo "       For NVIDIA GPU inference use scripts/vllm-serve-gemma.sh instead." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Detect unified memory for model guidance
# ---------------------------------------------------------------------------

MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo "0")
MEM_GB=$(( MEM_BYTES / 1073741824 ))

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

# --model flag takes precedence over env var
EXPLICIT_MODEL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)
            EXPLICIT_MODEL="$2"
            shift 2
            ;;
        --model=*)
            EXPLICIT_MODEL="${1#--model=}"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

MODEL="${EXPLICIT_MODEL:-${DEPTHFUSION_GEMMA_MODEL:-}}"

if [[ -z "${MODEL}" ]]; then
    echo ""
    echo "  DepthFusion — MLX model selection"
    echo "  Detected unified memory: ${MEM_GB} GB"
    echo ""
    echo "  Available models (4-bit quantized, via mlx-community on HuggingFace):"
    echo ""

    if [[ "${MEM_GB}" -ge 32 ]]; then
        echo "    [1] mlx-community/Qwen2.5-32B-Instruct-4bit  (~20 GB)  highest quality"
        echo "    [2] mlx-community/Qwen2.5-14B-Instruct-4bit  (~9 GB)   balanced"
        echo "    [3] mlx-community/gemma-3-12b-it-4bit         (~7 GB)   fast, efficient"
        echo ""
        echo "  Recommended for ${MEM_GB} GB: [1] Qwen2.5-32B (leaves ~16 GB headroom)"
        echo ""
        read -rp "  Choose [1/2/3] or press Enter for recommended: " CHOICE
        CHOICE="${CHOICE:-1}"
        case "${CHOICE}" in
            1) MODEL="mlx-community/Qwen2.5-32B-Instruct-4bit" ;;
            2) MODEL="mlx-community/Qwen2.5-14B-Instruct-4bit" ;;
            3) MODEL="mlx-community/gemma-3-12b-it-4bit" ;;
            *) MODEL="mlx-community/Qwen2.5-32B-Instruct-4bit" ;;
        esac
    elif [[ "${MEM_GB}" -ge 16 ]]; then
        echo "    [1] mlx-community/Qwen2.5-14B-Instruct-4bit  (~9 GB)   balanced  (recommended)"
        echo "    [2] mlx-community/gemma-3-12b-it-4bit         (~7 GB)   fast, efficient"
        echo ""
        echo "  Recommended for ${MEM_GB} GB: [1] Qwen2.5-14B"
        echo ""
        read -rp "  Choose [1/2] or press Enter for recommended: " CHOICE
        CHOICE="${CHOICE:-1}"
        case "${CHOICE}" in
            1) MODEL="mlx-community/Qwen2.5-14B-Instruct-4bit" ;;
            2) MODEL="mlx-community/gemma-3-12b-it-4bit" ;;
            *) MODEL="mlx-community/Qwen2.5-14B-Instruct-4bit" ;;
        esac
    else
        echo "    [1] mlx-community/gemma-3-12b-it-4bit  (~7 GB)  recommended for <16 GB"
        echo ""
        read -rp "  Press Enter to use gemma-3-12b, or type a custom model ID: " CHOICE
        MODEL="${CHOICE:-mlx-community/gemma-3-12b-it-4bit}"
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# Write chosen model back to env file so GemmaBackend.healthy() sees it
# ---------------------------------------------------------------------------

ENV_FILE="${HOME}/.claude/depthfusion.env"
if [[ -f "${ENV_FILE}" ]]; then
    if grep -q "^DEPTHFUSION_GEMMA_MODEL=" "${ENV_FILE}"; then
        # Update existing line (macOS sed -i requires '' suffix)
        sed -i '' "s|^DEPTHFUSION_GEMMA_MODEL=.*|DEPTHFUSION_GEMMA_MODEL=${MODEL}|" "${ENV_FILE}"
    else
        echo "DEPTHFUSION_GEMMA_MODEL=${MODEL}" >> "${ENV_FILE}"
    fi
    echo "[mlx-serve] Updated DEPTHFUSION_GEMMA_MODEL in ${ENV_FILE}"
fi

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

echo "[mlx-serve] starting server"
echo "  model:  ${MODEL}"
echo "  host:   ${HOST}"
echo "  port:   ${PORT}"
echo ""
echo "  First run downloads the model from HuggingFace (~7-20 GB)."
echo "  Subsequent starts load from cache and are fast."
echo ""
echo "  Press Ctrl+C to stop the server."
echo ""

# Use mlx-serve-direct.py instead of mlx_lm.server — the upstream server
# hangs loading the model from a background thread on macOS (Metal threading
# constraint). The direct wrapper loads the model on the main thread instead.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC2086
exec python3 "${SCRIPT_DIR}/mlx-serve-direct.py" \
    --model "${MODEL}" \
    --host "${HOST}" \
    --port "${PORT}"
