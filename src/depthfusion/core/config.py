"""DepthFusion configuration with environment variable overrides.

All feature flags default to True (enabled). Set to "false" / "0" / "no"
to disable any component without touching hook or MCP code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    skillforge_api_url: str = ""          # e.g. "http://127.0.0.1:3000"
    # Supabase HS256 JWT with MEMBER role (short-lived — no auto-refresh)
    skillforge_api_token: str = ""
    skillforge_recursive_skill_id: str = ""  # UUID of pre-registered recursive skill

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

    # E-67 S-220: retrieval pipeline gates (previously rogue env-only, now on config)
    fusion_gates_enabled: bool = False   # DEPTHFUSION_FUSION_GATES_ENABLED (Mamba B/C/Δ)
    cognitive_scoring_enabled: bool = False  # DEPTHFUSION_COGNITIVE_SCORING

    # E-31 Cognitive feature flags (all default OFF)
    cognitive_retrieval: bool = False
    llm_classifier: bool = False
    contradiction_engine: bool = False
    decision_memory: bool = False
    operational_memory: bool = False
    multi_agent_wm: bool = False
    autonomic: bool = False
    rest_api_enabled: bool = False
    api_public: bool = False
    api_token: str = ""
    mcp_http_enabled: bool = False   # DEPTHFUSION_MCP_HTTP_ENABLED
    mcp_http_port: int = 7301        # DEPTHFUSION_MCP_PORT
    mcp_http_token: str = ""         # DEPTHFUSION_MCP_TOKEN
    # E-67 S-225: Fernet cache on REST search (enabled by server/research profiles)
    cache_enabled: bool = False      # DEPTHFUSION_CACHE_ENABLED
    # E-67 S-224: active profile name (set by from_profile(); empty = standard)
    profile: str = ""                # "minimal"|"standard"|"server"|"research"|""

    # E-68 S-228 DistillationClient
    distillation_backend: str = "auto"   # "auto" | "local" | "haiku"
    local_llm_url: str = ""              # e.g. "http://127.0.0.1:11434/v1"

    # E-68 S-229 PersonaEngine
    persona_trigger_every_n: int = 50   # DEPTHFUSION_PERSONA_TRIGGER_EVERY_N

    # E-68 S-231 ContextOffloader (opt-in)
    offload_enabled: bool = False        # DEPTHFUSION_OFFLOAD_ENABLED
    offload_mmd_max_tokens: int = 400    # DEPTHFUSION_OFFLOAD_MMD_MAX_TOKENS

    # E-34 S-109 skill surfacing
    auto_draft_threshold: int = 3    # min distinct sessions before candidate is drafted

    # E-35 S-110 ambient capture
    ambient_capture: bool = True
    ambient_skip_tools: list[str] = field(default_factory=list)

    # E-35 S-111 session-start auto-recall
    auto_recall_at_session_start: bool = True
    auto_recall_top_k: int = 3
    auto_recall_snippet_len: int = 800

    # E-35 S-114 FTS5 index for memories
    fts_enabled: bool = True

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
            skillforge_api_url=os.environ.get("DEPTHFUSION_SKILLFORGE_API_URL", ""),
            skillforge_api_token=os.environ.get("DEPTHFUSION_SKILLFORGE_API_TOKEN", ""),
            skillforge_recursive_skill_id=os.environ.get(
                "DEPTHFUSION_SKILLFORGE_RECURSIVE_SKILL_ID", ""
            ),
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
            cognitive_retrieval=_env_bool("DEPTHFUSION_COGNITIVE_RETRIEVAL", False),
            llm_classifier=_env_bool("DEPTHFUSION_LLM_CLASSIFIER", False),
            contradiction_engine=_env_bool("DEPTHFUSION_CONTRADICTION_ENGINE", False),
            decision_memory=_env_bool("DEPTHFUSION_DECISION_MEMORY", False),
            operational_memory=_env_bool("DEPTHFUSION_OPERATIONAL_MEMORY", False),
            multi_agent_wm=_env_bool("DEPTHFUSION_MULTI_AGENT_WM", False),
            autonomic=_env_bool("DEPTHFUSION_AUTONOMIC", False),
            rest_api_enabled=_env_bool("DEPTHFUSION_REST_API", False),
            api_public=_env_bool("DEPTHFUSION_API_PUBLIC", False),
            api_token=os.environ.get("DEPTHFUSION_API_TOKEN", ""),
            mcp_http_enabled=_env_bool("DEPTHFUSION_MCP_HTTP_ENABLED", False),
            mcp_http_port=_env_int("DEPTHFUSION_MCP_PORT", 7301),
            mcp_http_token=os.environ.get("DEPTHFUSION_MCP_TOKEN", ""),
            auto_draft_threshold=_env_int("DEPTHFUSION_AUTO_DRAFT_THRESHOLD", 3),
            ambient_capture=_env_bool("DEPTHFUSION_AMBIENT_CAPTURE", True),
            ambient_skip_tools=[
                t.strip()
                for t in os.environ.get("DEPTHFUSION_AMBIENT_SKIP_TOOLS", "").split(",")
                if t.strip()
            ],
            auto_recall_at_session_start=_env_bool(
                "DEPTHFUSION_AUTO_RECALL_AT_SESSION_START", True
            ),
            auto_recall_top_k=_env_int("DEPTHFUSION_AUTO_RECALL_TOP_K", 3),
            auto_recall_snippet_len=_env_int("DEPTHFUSION_AUTO_RECALL_SNIPPET_LEN", 800),
            fts_enabled=_env_bool("DEPTHFUSION_FTS_ENABLED", True),
            fusion_gates_enabled=_env_bool("DEPTHFUSION_FUSION_GATES_ENABLED", False),
            cognitive_scoring_enabled=_env_bool("DEPTHFUSION_COGNITIVE_SCORING", False),
            cache_enabled=_env_bool("DEPTHFUSION_CACHE_ENABLED", False),
            profile=os.environ.get("DEPTHFUSION_PROFILE", ""),
            distillation_backend=os.environ.get("DEPTHFUSION_DISTILLATION_BACKEND", "auto"),
            local_llm_url=os.environ.get("DEPTHFUSION_LOCAL_LLM_URL", ""),
            persona_trigger_every_n=_env_int("DEPTHFUSION_PERSONA_TRIGGER_EVERY_N", 50),
            offload_enabled=_env_bool("DEPTHFUSION_OFFLOAD_ENABLED", False),
            offload_mmd_max_tokens=_env_int("DEPTHFUSION_OFFLOAD_MMD_MAX_TOKENS", 400),
        )

    @classmethod
    def from_profile(cls, name: str, **overrides: object) -> "DepthFusionConfig":
        """Return a config preset for the named profile.

        Individual keyword args override the profile defaults, mirroring how
        env vars override profile values in production.  Unknown profile names
        raise ValueError immediately (fast-fail for typos in tests).
        """
        from depthfusion.core.profiles import get_profile_overrides
        profile_overrides = get_profile_overrides(name)
        profile_overrides.update(overrides)
        # Strip keys that aren't dataclass fields (e.g. future-proofing in
        # profiles.py that hasn't landed in config yet).
        import dataclasses
        valid = {f.name for f in dataclasses.fields(cls)}
        safe_overrides = {k: v for k, v in profile_overrides.items() if k in valid}
        return cls(profile=name, **safe_overrides)

    @property
    def event_log_path(self) -> Path:
        base = os.environ.get(
            "DEPTHFUSION_EVENT_LOG",
            str(Path.home() / ".claude" / "depthfusion_events.jsonl"),
        )
        return Path(base).expanduser()

    @property
    def memory_store_path(self) -> Path:
        return Path.home() / ".claude" / ".depthfusion_memories.db"

    @property
    def telemetry_store_path(self) -> Path:
        return Path(
            os.environ.get(
                "DEPTHFUSION_TELEMETRY_DB",
                str(Path.home() / ".claude" / ".depthfusion_telemetry.db"),
            )
        ).expanduser()

    @property
    def working_memory_path(self) -> Path:
        return Path.home() / ".claude" / ".depthfusion_working_memory.db"
