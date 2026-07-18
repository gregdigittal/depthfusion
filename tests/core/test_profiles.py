"""S-224: named configuration profiles for DepthFusionConfig."""
from __future__ import annotations

import pytest

from depthfusion.core.config import DepthFusionConfig
from depthfusion.core.profiles import PROFILE_NAMES, get_profile_overrides

# ---------------------------------------------------------------------------
# get_profile_overrides
# ---------------------------------------------------------------------------

def test_all_four_profile_names_known():
    assert set(PROFILE_NAMES) == {"minimal", "standard", "server", "research"}


def test_unknown_profile_raises():
    with pytest.raises(ValueError, match="Unknown profile"):
        get_profile_overrides("typo")


def test_standard_profile_is_empty():
    """Standard profile has no overrides — it is the dataclass default."""
    assert get_profile_overrides("standard") == {}


def test_minimal_profile_disables_llm_features():
    overrides = get_profile_overrides("minimal")
    assert overrides["haiku_enabled"] is False
    assert overrides["fusion_gates_enabled"] is False
    assert overrides["cognitive_scoring_enabled"] is False


def test_server_profile_enables_cache_and_rest():
    overrides = get_profile_overrides("server")
    assert overrides["cache_enabled"] is True
    assert overrides["rest_api_enabled"] is True


def test_research_profile_enables_haiku_and_graph():
    overrides = get_profile_overrides("research")
    assert overrides["haiku_enabled"] is True
    assert overrides["graph_enabled"] is True


# ---------------------------------------------------------------------------
# DepthFusionConfig.from_profile
# ---------------------------------------------------------------------------

def test_from_profile_sets_profile_name():
    cfg = DepthFusionConfig.from_profile("minimal")
    assert cfg.profile == "minimal"


def test_from_profile_server_enables_cache():
    cfg = DepthFusionConfig.from_profile("server")
    assert cfg.cache_enabled is True
    assert cfg.rest_api_enabled is True


def test_from_profile_research_enables_haiku():
    cfg = DepthFusionConfig.from_profile("research")
    assert cfg.haiku_enabled is True


def test_from_profile_override_wins_over_profile_default():
    """Keyword overrides must supersede profile values (S-224 AC-2)."""
    cfg = DepthFusionConfig.from_profile("server", cache_enabled=False)
    assert cfg.cache_enabled is False


def test_from_profile_unknown_raises():
    with pytest.raises(ValueError, match="Unknown profile"):
        DepthFusionConfig.from_profile("bogus")


def test_from_profile_standard_returns_default_config():
    """Standard profile is identical to the zero-arg constructor (no overrides)."""
    default = DepthFusionConfig()
    standard = DepthFusionConfig.from_profile("standard")
    # profile field differs; all other fields must match
    assert standard.cache_enabled == default.cache_enabled
    assert standard.haiku_enabled == default.haiku_enabled
    assert standard.graph_enabled == default.graph_enabled
