"""DepthFusion configuration with environment variable overrides.

All feature flags default to True (enabled). Set to "false" / "0" / "no"
to disable any component without touching hook or MCP code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_TRUTHY = {"true", "1", "yes"}
_FALSY = {"false", "0", "no"}


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in _TRUTHY:
        return True
    if val in _FALSY:
        return False
    return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, ""))
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, ""))
    except (ValueError, TypeError):
        return default


@dataclass
class DepthFusionConfig:
    """All DepthFusion runtime configuration in one place.

    Instantiate directly for tests (no env side-effects).
    Use DepthFusionConfig.from_env() in production hooks/MCP server.
    """

    # Feature flags
    fusion_enabled: bool = True
    session_enabled: bool = True
    rlm_enabled: bool = True
    router_enabled: bool = True
    graph_enabled: bool = False       # v0.4.0 Knowledge Graph (opt-in)

    # Session behaviour
    session_selective: bool = True   # False → load_all() fallback
    session_top_k: int = 5
    tagger_llm: bool = False         # True → use LLM for tag extraction (costs tokens)

    # Fusion parameters
    rrf_k: int = 60                  # RRF constant k (standard default)

    # RLM parameters
    rlm_cost_ceiling: float = 0.50   # USD per query
    rlm_timeout_seconds: int = 120

    # Context bus
    bus_backend: str = "file"        # "memory" | "file" | "supabase"
    bus_file_dir: str = "~/.claude/context-bus"

    @classmethod
    def from_env(cls) -> "DepthFusionConfig":
        """Read all configuration from environment variables."""
        return cls(
            fusion_enabled=_env_bool("DEPTHFUSION_FUSION_ENABLED", True),
            session_enabled=_env_bool("DEPTHFUSION_SESSION_ENABLED", True),
            rlm_enabled=_env_bool("DEPTHFUSION_RLM_ENABLED", True),
            router_enabled=_env_bool("DEPTHFUSION_ROUTER_ENABLED", True),
            graph_enabled=_env_bool("DEPTHFUSION_GRAPH_ENABLED", False),
            session_selective=_env_bool("DEPTHFUSION_SESSION_SELECTIVE", True),
            session_top_k=_env_int("DEPTHFUSION_SESSION_TOP_K", 5),
            tagger_llm=_env_bool("DEPTHFUSION_TAGGER_LLM", False),
            rrf_k=_env_int("DEPTHFUSION_RRF_K", 60),
            rlm_cost_ceiling=_env_float("DEPTHFUSION_RLM_COST_CEILING", 0.50),
            rlm_timeout_seconds=_env_int("DEPTHFUSION_RLM_TIMEOUT_SECONDS", 120),
            bus_backend=os.environ.get("DEPTHFUSION_BUS_BACKEND", "file"),
            bus_file_dir=os.environ.get(
                "DEPTHFUSION_BUS_FILE_DIR", "~/.claude/context-bus"
            ),
        )
