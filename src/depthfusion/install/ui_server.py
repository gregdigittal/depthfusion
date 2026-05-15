"""Guided web install wizard — FastAPI backend (S-110 T-366).

Endpoints:
  GET  /install                       -> serve static/index.html
  GET  /install/api/hardware          -> gpu_probe results + recommended mode
  GET  /install/api/steps/{step}      -> step data (1-6)
  POST /install/api/steps/{step}      -> submit step, returns next-step info
  POST /install/api/install/deps      -> SSE: stream pip install output
  POST /install/api/install/apply     -> dry_run=True summary
  POST /install/api/install/finish    -> real install, result JSON
  GET  /install/api/status            -> current session state

State is in-memory (one install session at a time - local tool, not multi-tenant).
Values written to ~/.claude/depthfusion.env are never echoed in HTTP responses.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from depthfusion.install import install as install_mod
from depthfusion.install.dep_checker import check_deps
from depthfusion.install.gpu_probe import detect_apple_silicon, detect_gpu

logger = logging.getLogger(__name__)

app = FastAPI(title="DepthFusion Install Wizard", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"

# One install session at a time.
_state: dict[str, Any] = {
    "mode": None,
    "steps_completed": [],
}


def _reset_state() -> None:
    """Reset install session state. Used by tests and by the status endpoint."""
    _state["mode"] = None
    _state["steps_completed"] = []


# ---------------------------------------------------------------------------
# Secret scrubbing for SSE pip output
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-\S+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)secret\s*[:=]\s*\S+"),
    re.compile(r"(?i)password\s*[:=]\s*\S+"),
    re.compile(r"(?i)token\s*[:=]\s*\S+"),
    re.compile(r"ANTHROPIC_API_KEY\s*=\s*\S+"),
    re.compile(r"DEPTHFUSION_SKILLFORGE_API_KEY\s*=\s*\S+"),
]


def _is_safe_line(line: str) -> bool:
    """Return True if the line contains no recognisable secret patterns."""
    return not any(p.search(line) for p in _SECRET_PATTERNS)


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

@app.get("/install", include_in_schema=False)
async def serve_ui() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="UI not built yet")
    return FileResponse(index)


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

@app.get("/install/api/hardware")
async def hardware() -> dict:
    apple = detect_apple_silicon()
    gpu = detect_gpu()

    if apple.has_apple_silicon:
        recommended = "mac-mlx"
        reason = apple.reason
    elif gpu.has_gpu:
        recommended = "vps-gpu"
        reason = gpu.reason
    else:
        recommended = "local"
        reason = "No GPU or Apple Silicon detected - local mode recommended"

    return {
        "apple_silicon": {
            "detected": apple.has_apple_silicon,
            "chip": apple.chip_name,
            "memory_gb": apple.memory_gb,
        },
        "nvidia_gpu": {
            "detected": gpu.has_gpu,
            "name": gpu.gpu_name,
            "vram_gb": gpu.vram_gb,
        },
        "recommended_mode": recommended,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Step data
# ---------------------------------------------------------------------------

_MODE_OPTIONS = [
    {"id": "local",   "label": "Local",   "description": "BM25 only, no API calls, no GPU"},
    {"id": "vps-cpu", "label": "VPS CPU", "description": "Haiku reranker + optional ChromaDB"},
    {"id": "vps-gpu", "label": "VPS GPU", "description": "Gemma on-box + local embeddings (NVIDIA)"},  # noqa: E501
    {"id": "mac-mlx", "label": "Mac MLX", "description": "MLX local inference (Apple Silicon GPU)"},  # noqa: E501
]

_ALLOWED_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "DEPTHFUSION_SKILLFORGE_URL",
    "DEPTHFUSION_SKILLFORGE_API_KEY",
    "DEPTHFUSION_MODE",
}

_ENV_FIELDS_COMMON = [
    {
        "key": "DEPTHFUSION_SKILLFORGE_URL",
        "label": "SkillForge URL",
        "required": False,
        "description": "Optional SkillForge server URL",
        "sensitive": False,
    },
    {
        "key": "DEPTHFUSION_SKILLFORGE_API_KEY",
        "label": "SkillForge API Key",
        "required": False,
        "description": "Optional SkillForge API key",
        "sensitive": True,
    },
]

_ENV_FIELDS_VPS_CPU = [
    {
        "key": "ANTHROPIC_API_KEY",
        "label": "Anthropic API Key",
        "required": False,
        "description": "Enables Haiku reranker (sk-ant-...)",
        "sensitive": True,
    },
]


def _get_recommended_mode() -> str:
    apple = detect_apple_silicon()
    if apple.has_apple_silicon:
        return "mac-mlx"
    gpu = detect_gpu()
    if gpu.has_gpu:
        return "vps-gpu"
    return "local"


@app.get("/install/api/steps/{step}")
async def get_step(step: int) -> dict:
    if step not in range(1, 7):
        raise HTTPException(status_code=404, detail=f"Step {step} not found")

    mode = _state["mode"]

    if step == 1:
        return {
            "step": 1,
            "title": "Install Mode",
            "mode_options": _MODE_OPTIONS,
            "recommended_mode": _get_recommended_mode(),
            "current_mode": mode,
        }

    if step == 2:
        import platform as _platform
        claude_dir = Path.home() / ".claude"
        return {
            "step": 2,
            "title": "System Checks",
            "checks": [
                {"name": "Python version", "ok": True, "detail": sys.version.split()[0]},
                {"name": "Platform", "ok": True, "detail": _platform.system()},
                {
                    "name": "~/.claude directory",
                    "ok": claude_dir.exists(),
                    "detail": str(claude_dir),
                },
            ],
        }

    if step == 3:
        if mode is None:
            raise HTTPException(status_code=400, detail="Mode not selected - complete step 1 first")
        return {"step": 3, "title": "Dependencies", "mode": mode, "deps": check_deps(mode)}

    if step == 4:
        if mode is None:
            raise HTTPException(status_code=400, detail="Mode not selected - complete step 1 first")
        fields = (
            _ENV_FIELDS_VPS_CPU + list(_ENV_FIELDS_COMMON)
            if mode == "vps-cpu"
            else list(_ENV_FIELDS_COMMON)
        )
        return {
            "step": 4,
            "title": "Environment Variables",
            "mode": mode,
            "auto_keys": [{"key": "DEPTHFUSION_MODE", "value_will_be_set": mode, "auto": True}],
            "fields": fields,
        }

    if step == 5:
        if mode is None:
            raise HTTPException(status_code=400, detail="Mode not selected - complete step 1 first")
        return {"step": 5, "title": "Hooks & MCP", "diff": install_mod.get_hooks_diff(mode)}

    # step == 6
    return {
        "step": 6,
        "title": "Confirm & Install",
        "mode": mode,
        "steps_completed": _state["steps_completed"],
        "ready": mode is not None,
    }


@app.post("/install/api/steps/{step}")
async def post_step(
    step: int,
    body: Optional[dict[str, Any]] = Body(default={}),
) -> dict:
    if body is None:
        body = {}
    if step not in range(1, 7):
        raise HTTPException(status_code=404, detail=f"Step {step} not found")

    if step == 1:
        mode = body.get("mode")
        if mode not in {"local", "vps-cpu", "vps-gpu", "mac-mlx"}:
            raise HTTPException(status_code=422, detail=f"Invalid mode: {mode!r}")
        _state["mode"] = mode
        if 1 not in _state["steps_completed"]:
            _state["steps_completed"].append(1)
        return {"step": 1, "next_step": 2, "mode": mode}

    if step == 2:
        if 2 not in _state["steps_completed"]:
            _state["steps_completed"].append(2)
        return {"step": 2, "next_step": 3}

    if step == 3:
        if 3 not in _state["steps_completed"]:
            _state["steps_completed"].append(3)
        return {"step": 3, "next_step": 4}

    if step == 4:
        # Collect allowlisted env vars; write to disk — never echo values back.
        user_vars: dict[str, str] = {
            k: v for k, v in body.items()
            if isinstance(k, str) and isinstance(v, str) and v and k in _ALLOWED_ENV_KEYS
        }
        if _state["mode"]:
            user_vars["DEPTHFUSION_MODE"] = _state["mode"]
        if user_vars:
            install_mod.write_env_from_dict(user_vars)
        if 4 not in _state["steps_completed"]:
            _state["steps_completed"].append(4)
        # Return keys only - values must not appear in the response body.
        return {"step": 4, "next_step": 5, "written_keys": list(user_vars.keys())}

    if step == 5:
        if 5 not in _state["steps_completed"]:
            _state["steps_completed"].append(5)
        return {"step": 5, "next_step": 6}

    # step == 6
    if 6 not in _state["steps_completed"]:
        _state["steps_completed"].append(6)
    return {"step": 6, "next_step": None, "ready_to_install": True}


# ---------------------------------------------------------------------------
# Install execution
# ---------------------------------------------------------------------------

def _capture_install(mode: str, dry_run: bool) -> tuple[bool, list[str]]:
    """Run the appropriate install_* function and capture its printed output."""
    buf = io.StringIO()
    success = True
    try:
        with redirect_stdout(buf):
            if mode == "local":
                install_mod.install_local(dry_run=dry_run)
            elif mode == "vps-cpu":
                install_mod.install_vps_cpu(dry_run=dry_run)
            elif mode == "vps-gpu":
                ret = install_mod.install_vps_gpu(dry_run=dry_run, skip_gpu_check=True)
                success = ret == 0
            elif mode == "mac-mlx":
                ret = install_mod.install_mac_mlx(dry_run=dry_run, skip_silicon_check=True)
                success = ret == 0
            else:
                buf.write(f"[ERROR] Unknown mode: {mode}\n")
                success = False
    except Exception as exc:  # noqa: BLE001
        buf.write(f"[ERROR] {exc}\n")
        success = False
    return success, [ln for ln in buf.getvalue().splitlines() if _is_safe_line(ln)]


@app.post("/install/api/install/apply")
async def install_apply() -> dict:
    mode = _state.get("mode")
    if not mode:
        raise HTTPException(status_code=400, detail="No mode selected")
    success, summary = _capture_install(mode, dry_run=True)
    return {"dry_run": True, "mode": mode, "success": success, "summary": summary}


@app.post("/install/api/install/finish")
async def install_finish() -> dict:
    mode = _state.get("mode")
    if not mode:
        raise HTTPException(status_code=400, detail="No mode selected")
    success, output = _capture_install(mode, dry_run=False)
    return {"dry_run": False, "mode": mode, "success": success, "output": output}


@app.post("/install/api/install/deps")
async def install_deps_sse(
    body: Optional[dict[str, Any]] = Body(default={}),
) -> StreamingResponse:
    """Stream pip install output as Server-Sent Events.

    Lines containing secret patterns are suppressed before streaming.
    """
    if body is None:
        body = {}
    mode = body.get("mode") or _state.get("mode")
    if not mode:
        raise HTTPException(status_code=400, detail="No mode specified")

    deps = check_deps(mode)
    packages = [d["name"] for d in deps if not d["installed"]]

    if not packages:
        async def _no_packages():
            yield "data: [INFO] All packages already installed\n\n"
            yield "data: [EXIT:0]\n\n"
        return StreamingResponse(_no_packages(), media_type="text/event-stream")

    async def _stream_pip():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", *packages,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if proc.stdout:
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if _is_safe_line(line):
                    yield f"data: {line}\n\n"
        try:
            await asyncio.wait_for(proc.wait(), timeout=300.0)
        except asyncio.TimeoutError:
            proc.kill()
            yield "data: [ERROR] Install timed out after 5 minutes\n\n"
            yield "data: [EXIT:1]\n\n"
            return
        yield f"data: [EXIT:{proc.returncode}]\n\n"

    return StreamingResponse(_stream_pip(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/install/api/status")
async def status() -> dict:
    return {"mode": _state["mode"], "steps_completed": _state["steps_completed"]}
