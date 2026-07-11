"""Named configuration profiles for DepthFusion.

Four presets collapse the combinatorial flag space into sensible defaults.
Individual env vars always override profile values (from_env() is still
the authoritative production constructor; from_profile() is the test-
and-operator-friendly variant).
"""
from __future__ import annotations

from typing import Any

# Profile definitions — only the flags that differ from the dataclass default
# are listed.  All other flags keep their default value.
_PROFILES: dict[str, dict[str, Any]] = {
    "minimal": {
        # BM25/FTS only — no LLM backends, no graph, no ambient capture.
        # Ideal for CI, unit tests, and air-gapped environments.
        "haiku_enabled": False,
        "graph_enabled": False,
        "tagger_llm": False,
        "fusion_gates_enabled": False,
        "cognitive_scoring_enabled": False,
        "cognitive_retrieval": False,
        "llm_classifier": False,
        "contradiction_engine": False,
        "decision_memory": False,
        "operational_memory": False,
        "multi_agent_wm": False,
        "autonomic": False,
        "rest_api_enabled": False,
        "mcp_http_enabled": False,
        "reranker_backend": "",
        "extractor_backend": "",
        "linker_backend": "",
        "summariser_backend": "",
        "embedding_backend": "",
        "decision_extractor_backend": "",
    },
    "standard": {
        # Today's shipped default — all on-by-default flags on, all off-by-default
        # flags off.  This is what DepthFusionConfig() produces; named here so
        # it can be explicitly requested and reported in status output.
    },
    "server": {
        # Adds graph, REST API, Fernet cache, RBAC (assumes Redis + OIDC available).
        "graph_enabled": True,
        "rest_api_enabled": True,
        "mcp_http_enabled": True,
        "cache_enabled": True,  # Fernet cache on /api/v1/search (S-225)
    },
    "research": {
        # Full-flag configuration — every optional feature on.
        # CIQS 95–97 projections are based on this profile.
        "graph_enabled": True,
        "haiku_enabled": True,
        "fusion_gates_enabled": True,
        "cognitive_scoring_enabled": True,
        "cognitive_retrieval": True,
        "rest_api_enabled": True,
        "mcp_http_enabled": True,
        "cache_enabled": True,
    },
}

PROFILE_NAMES = tuple(_PROFILES.keys())


def get_profile_overrides(name: str) -> dict[str, Any]:
    """Return the flag overrides for a named profile.

    Raises ValueError for unknown profile names so callers can surface a
    clear error rather than silently falling back to the standard defaults.
    """
    if name not in _PROFILES:
        raise ValueError(
            f"Unknown profile {name!r}. Valid names: {', '.join(PROFILE_NAMES)}"
        )
    return dict(_PROFILES[name])
