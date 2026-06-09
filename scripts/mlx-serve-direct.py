#!/usr/bin/env python3
"""
Minimal OpenAI-compatible server for mlx_lm on Apple Silicon.

Replaces `mlx_lm server` which hangs because it loads the model in a background
thread — Metal GPU init must happen on the main thread on macOS.

This script loads the model eagerly on the main thread, then serves
POST /v1/chat/completions compatible with DepthFusion's GemmaBackend.

Usage:
    python scripts/mlx-serve-direct.py [--model <id>] [--host <ip>] [--port <n>]
"""

import argparse
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser()
parser.add_argument("--model", default=os.environ.get(
    "DEPTHFUSION_GEMMA_MODEL", "mlx-community/Qwen2.5-14B-Instruct-4bit"))
parser.add_argument("--host", default=os.environ.get("DEPTHFUSION_GEMMA_HOST", "127.0.0.1"))
parser.add_argument(
    "--port", type=int, default=int(os.environ.get("DEPTHFUSION_GEMMA_PORT", "8000"))
)
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Load model on the main thread (fixes Metal threading hang in mlx_lm server)
# ---------------------------------------------------------------------------

try:
    import mlx_lm
except ImportError:
    print("error: mlx_lm is not installed.", file=sys.stderr)
    print("       Install with: pip install -e .[mac-mlx]  or  pip install mlx-lm", file=sys.stderr)
    sys.exit(127)

print(f"[mlx-serve] Loading model on main thread: {args.model}")
print("[mlx-serve] First run downloads from HuggingFace — subsequent starts are fast.")
print()

model, tokenizer = mlx_lm.load(args.model)
print()
print(f"[mlx-serve] Model ready. Serving on http://{args.host}:{args.port}")
print("[mlx-serve] Endpoint: POST /v1/chat/completions")
print("[mlx-serve] Press Ctrl+C to stop.")
print()

# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

MODEL_ID = args.model


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps({
                "object": "list",
                "data": [{"id": MODEL_ID, "object": "model"}]
            }).encode()
            self._respond(200, body)
        elif self.path in ("/health", "/v1/health"):
            self._respond(200, b'{"status":"ok"}')
        else:
            self._respond(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._respond(404, b'{"error":"not found"}')
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond(400, b'{"error":"invalid json"}')
            return

        try:
            messages = body.get("messages", [])
            max_tokens = body.get("max_tokens", 512)
            temperature = float(body.get("temperature", 0.0))

            # Apply chat template
            if hasattr(tokenizer, "apply_chat_template"):
                prompt = tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            else:
                prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages)

            # Generate (main thread — Metal-safe)
            # temp renamed to temperature in mlx_lm 0.31.x
            response_text = mlx_lm.generate(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                verbose=True,
            )

            response = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": MODEL_ID,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            self._respond(200, json.dumps(response).encode())

        except Exception as exc:
            import traceback
            traceback.print_exc()
            err = json.dumps({"error": str(exc)}).encode()
            self._respond(500, err)

    def _respond(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args_):  # noqa: N802
        print(f"[mlx-serve] {fmt % args_}")


# ---------------------------------------------------------------------------
# Serve
# ---------------------------------------------------------------------------

import socket as _socket


class _Server(HTTPServer):
    # Explicitly set SO_REUSEADDR so launchd restarts don't hit EADDRINUSE
    # while the OS is still draining TIME_WAIT from the previous instance.
    allow_reuse_address = True

    def server_bind(self):
        self.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        super().server_bind()


try:
    _Server((args.host, args.port), Handler).serve_forever()
except KeyboardInterrupt:
    print("\n[mlx-serve] stopped.")
