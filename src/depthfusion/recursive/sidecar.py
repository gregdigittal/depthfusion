"""HTTP sidecar exposing RLMClient over FastAPI so TypeScript callers can
reach the Python recursive backend without a Python import dependency."""
from __future__ import annotations

import dataclasses
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from depthfusion.recursive.client import RLMClient

app = FastAPI(title="DepthFusion RLM Sidecar", docs_url=None, redoc_url=None)
_client = RLMClient()


class RunRequest(BaseModel):
    query: str
    content: str
    strategy: Optional[str] = None
    max_cost: Optional[float] = None


@app.get("/health")
def health() -> dict:
    available = _client.is_available()
    return {"available": available, "status": "ok" if available else "degraded"}


@app.post("/run")
def run(req: RunRequest) -> JSONResponse:
    try:
        result, trajectory = _client.run(
            query=req.query,
            content=req.content,
            strategy=req.strategy,
            max_cost=req.max_cost,
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": str(exc)})

    return JSONResponse(
        content={
            "result": result,
            "trajectory": dataclasses.asdict(trajectory),
        }
    )


@app.get("/schema")
def schema() -> dict:
    return {
        "endpoints": {
            "GET /health": {
                "response": {"available": "bool", "status": "string"},
            },
            "POST /run": {
                "body": {
                    "query": "string (required)",
                    "content": "string (required)",
                    "strategy": "string|null",
                    "max_cost": "number|null",
                },
                "response": {"result": "string", "trajectory": "object"},
            },
        }
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("DEPTHFUSION_RLM_PORT", "8771"))
    # Loopback-only: SkillForge connects via localhost; public bind not needed.
    uvicorn.run(app, host="127.0.0.1", port=port)
