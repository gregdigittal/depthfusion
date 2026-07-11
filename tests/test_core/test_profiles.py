"""S-224: named config profiles round-trip tests."""
from __future__ import annotations

import dataclasses

import pytest

from depthfusion.core.config import DepthFusionConfig
from depthfusion.core.profiles import PROFILE_NAMES, get_profile_overrides


@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_from_profile_round_trips_name(name):
    """from_profile(name).profile == name for every defined profile."""
    cfg = DepthFusionConfig.from_profile(name)
    assert cfg.profile == name


@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_from_profile_produces_valid_config(name):
    """from_profile returns a fully-constructed DepthFusionConfig (no crash)."""
    cfg = DepthFusionConfig.from_profile(name)
    assert isinstance(cfg, DepthFusionConfig)


def test_unknown_profile_raises():
    with pytest.raises(ValueError, match="Unknown profile"):
        DepthFusionConfig.from_profile("nonexistent")


def test_minimal_profile_disables_extras():
    cfg = DepthFusionConfig.from_profile("minimal")
    assert cfg.graph_enabled is False
    assert cfg.haiku_enabled is False
    assert cfg.fusion_gates_enabled is False
    assert cfg.rest_api_enabled is False


def test_server_profile_enables_cache():
    cfg = DepthFusionConfig.from_profile("server")
    assert cfg.cache_enabled is True
    assert cfg.rest_api_enabled is True


def test_research_profile_enables_all_gates():
    cfg = DepthFusionConfig.from_profile("research")
    assert cfg.fusion_gates_enabled is True
    assert cfg.cognitive_scoring_enabled is True
    assert cfg.graph_enabled is True
    assert cfg.cache_enabled is True


def test_standard_profile_equals_defaults():
    """standard profile must be identical to bare DepthFusionConfig()."""
    standard = DepthFusionConfig.from_profile("standard")
    default = DepthFusionConfig()
    # Exclude 'profile' field since default is "" and standard is "standard"
    field_names = [f.name for f in dataclasses.fields(DepthFusionConfig) if f.name != "profile"]
    for name in field_names:
        assert getattr(standard, name) == getattr(default, name), (
            f"standard profile differs from default on field {name!r}: "
            f"{getattr(standard, name)!r} != {getattr(default, name)!r}"
        )


def test_override_kwarg_wins_over_profile():
    """Caller-supplied kwargs override profile defaults."""
    cfg = DepthFusionConfig.from_profile("minimal", graph_enabled=True)
    assert cfg.graph_enabled is True
    assert cfg.profile == "minimal"


def test_profile_overrides_are_subset_of_config_fields():
    """Every key in a profile definition must be a real config field."""
    valid_fields = {f.name for f in dataclasses.fields(DepthFusionConfig)}
    for name in PROFILE_NAMES:
        overrides = get_profile_overrides(name)
        unknown = set(overrides) - valid_fields
        assert not unknown, (
            f"Profile {name!r} has keys not in DepthFusionConfig: {unknown}"
        )
