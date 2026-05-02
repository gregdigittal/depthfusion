"""DepthFusion configuration with environment variable overrides.

All feature flags default to True (enabled). Set to "false" / "0" / "no"
to disable any component without touching hook or MCP code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _load_env_file() -> None:
    env_path = Path.home() / ".claude" / "depthfusion.env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and value and key not in os.environ:
                os.environ[key] = value


_load_env_file()


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


def _env_float_opt(key: str) -> Optional[float]:
    """Return float if env var is set and valid, else None."""
    val = os.environ.get(key, "").strip()
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


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
    # v0.5.0 Haiku API calls (opt-in, requires DEPTHFUSION_API_KEY)
    haiku_enabled: bool = False

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

    # S-71 Decay rates (bucketed by importance)
    decay_rate_high: float = 0.01        # importance >= 0.8 → 1%/day
    decay_rate_mid: float = 0.02         # importance >= 0.5 → 2%/day
    decay_rate_low: float = 0.05         # importance < 0.5  → 5%/day
    hard_archive_threshold: float = 0.05 # salience < 0.05 → move to .archive/

    # S-73 High-importance event hook
    high_importance_threshold: float = 0.8
    event_log: str = "~/.claude/shared/depthfusion-events.jsonl"

    # S-77 Auto-compress cadence (None = manual-only)
    auto_compress_hours: Optional[float] = None

    # v0.5.0 backend provider interface
    # Empty string = use mode default from backends.factory._DEFAULT_DISPATCH
    reranker_backend: str = ""           # null | haiku | gemma
    extractor_backend: str = ""          # null | haiku | gemma
    linker_backend: str = ""             # null | haiku | gemma
    summariser_backend: str = ""         # null | haiku | gemma
    embedding_backend: str = ""          # null | local
    decision_extractor_backend: str = "" # null | haiku | gemma
    gemma_url: str = "http://127.0.0.1:8000/v1"
    gemma_model: str = "google/gemma-3-12b-it-AWQ"
    backend_fallback_log: bool = True    # emit JSONL record to metrics/ on fallback

    @classmethod
    def from_env(cls) -> "DepthFusionConfig":
        """Read all configuration from environment variables."""
        return cls(
            fusion_enabled=_env_bool("DEPTHFUSION_FUSION_ENABLED", True),
            session_enabled=_env_bool("DEPTHFUSION_SESSION_ENABLED", True),
            rlm_enabled=_env_bool("DEPTHFUSION_RLM_ENABLED", True),
            router_enabled=_env_bool("DEPTHFUSION_ROUTER_ENABLED", True),
            graph_enabled=_env_bool("DEPTHFUSION_GRAPH_ENABLED", False),
            haiku_enabled=_env_bool("DEPTHFUSION_HAIKU_ENABLED", False),
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
            reranker_backend=os.environ.get("DEPTHFUSION_RERANKER_BACKEND", ""),
            extractor_backend=os.environ.get("DEPTHFUSION_EXTRACTOR_BACKEND", ""),
            linker_backend=os.environ.get("DEPTHFUSION_LINKER_BACKEND", ""),
            summariser_backend=os.environ.get("DEPTHFUSION_SUMMARISER_BACKEND", ""),
            embedding_backend=os.environ.get("DEPTHFUSION_EMBEDDING_BACKEND", ""),
            decision_extractor_backend=os.environ.get("DEPTHFUSION_DECISION_EXTRACTOR_BACKEND", ""),
            gemma_url=os.environ.get("DEPTHFUSION_GEMMA_URL", "http://127.0.0.1:8000/v1"),
            gemma_model=os.environ.get("DEPTHFUSION_GEMMA_MODEL", "google/gemma-3-12b-it-AWQ"),
            backend_fallback_log=_env_bool("DEPTHFUSION_BACKEND_FALLBACK_LOG", True),
            decay_rate_high=_env_float("DEPTHFUSION_DECAY_RATE_HIGH", 0.01),
            decay_rate_mid=_env_float("DEPTHFUSION_DECAY_RATE_MID", 0.02),
            decay_rate_low=_env_float("DEPTHFUSION_DECAY_RATE_LOW", 0.05),
            hard_archive_threshold=_env_float("DEPTHFUSION_HARD_ARCHIVE_THRESHOLD", 0.05),
            high_importance_threshold=_env_float("DEPTHFUSION_HIGH_IMPORTANCE_THRESHOLD", 0.8),
            event_log=os.environ.get(
                "DEPTHFUSION_EVENT_LOG", "~/.claude/shared/depthfusion-events.jsonl"
            ),
            auto_compress_hours=_env_float_opt("DEPTHFUSION_AUTO_COMPRESS_HOURS"),
        )
