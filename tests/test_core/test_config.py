"""Tests for core/config.py — DepthFusionConfig and feature flags."""
from depthfusion.core.config import DepthFusionConfig


class TestDepthFusionConfigDefaults:
    def test_all_feature_flags_default_true(self):
        cfg = DepthFusionConfig()
        assert cfg.fusion_enabled is True
        assert cfg.session_enabled is True
        assert cfg.rlm_enabled is True
        assert cfg.router_enabled is True

    def test_session_selective_default_true(self):
        cfg = DepthFusionConfig()
        assert cfg.session_selective is True

    def test_tagger_llm_default_false(self):
        cfg = DepthFusionConfig()
        assert cfg.tagger_llm is False

    def test_rlm_cost_ceiling_default(self):
        cfg = DepthFusionConfig()
        assert cfg.rlm_cost_ceiling == 0.50

    def test_session_top_k_default(self):
        cfg = DepthFusionConfig()
        assert cfg.session_top_k == 5

    def test_rrf_k_default(self):
        cfg = DepthFusionConfig()
        assert cfg.rrf_k == 60

    def test_bus_backend_default_file(self):
        cfg = DepthFusionConfig()
        assert cfg.bus_backend == "file"


class TestDepthFusionConfigEnvOverrides:
    def test_fusion_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_FUSION_ENABLED", "false")
        cfg = DepthFusionConfig.from_env()
        assert cfg.fusion_enabled is False

    def test_session_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_SESSION_ENABLED", "0")
        cfg = DepthFusionConfig.from_env()
        assert cfg.session_enabled is False

    def test_rlm_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_RLM_ENABLED", "false")
        cfg = DepthFusionConfig.from_env()
        assert cfg.rlm_enabled is False

    def test_router_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_ROUTER_ENABLED", "false")
        cfg = DepthFusionConfig.from_env()
        assert cfg.router_enabled is False

    def test_cost_ceiling_override(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_RLM_COST_CEILING", "1.00")
        cfg = DepthFusionConfig.from_env()
        assert cfg.rlm_cost_ceiling == 1.00

    def test_top_k_override(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_SESSION_TOP_K", "10")
        cfg = DepthFusionConfig.from_env()
        assert cfg.session_top_k == 10

    def test_truthy_values_recognized(self, monkeypatch):
        for val in ["true", "True", "TRUE", "1", "yes"]:
            monkeypatch.setenv("DEPTHFUSION_FUSION_ENABLED", val)
            cfg = DepthFusionConfig.from_env()
            assert cfg.fusion_enabled is True, f"Expected True for {val!r}"

    def test_falsy_values_recognized(self, monkeypatch):
        for val in ["false", "False", "FALSE", "0", "no"]:
            monkeypatch.setenv("DEPTHFUSION_FUSION_ENABLED", val)
            cfg = DepthFusionConfig.from_env()
            assert cfg.fusion_enabled is False, f"Expected False for {val!r}"
