"""S-220: RecallPipeline reads gate flags from DepthFusionConfig, not raw env."""
import pytest
from depthfusion.core.config import DepthFusionConfig
from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline


_BLOCKS = [{"text": "a", "score": 1.0}, {"text": "b", "score": 0.5}]


def _pipeline(fusion_gates: bool = False, cognitive_scoring: bool = False) -> RecallPipeline:
    cfg = DepthFusionConfig(fusion_gates_enabled=fusion_gates, cognitive_scoring_enabled=cognitive_scoring)
    return RecallPipeline(mode=PipelineMode.LOCAL, config=cfg)


class TestFusionGatesConfig:
    def test_gates_off_by_default_returns_blocks_unchanged(self):
        p = _pipeline(fusion_gates=False)
        result = p.apply_fusion_gates(_BLOCKS, query="test")
        assert result == _BLOCKS  # pass-through, no mutation

    def test_gates_on_via_config_runs_pipeline(self, monkeypatch):
        # If fusion_gates_enabled=True the gate code path executes; we stub
        # SelectiveFusionWeighter to avoid needing the full cognitive stack.
        import depthfusion.retrieval.hybrid as _mod

        class _FakeWeighter:
            def apply(self, blocks, *, query_embedding=None):
                return blocks, []

        class _FakeCfg:
            def version_id(self):
                return "test"

        import importlib, sys

        fake_module = type(sys)("depthfusion.fusion.selective_fusion_weighter")
        fake_module.SelectiveFusionWeighter = _FakeWeighter
        fake_module.SelectiveGateConfig = type("Cfg", (), {"from_env": staticmethod(lambda: _FakeCfg())})
        monkeypatch.setitem(sys.modules, "depthfusion.fusion.selective_fusion_weighter", fake_module)

        p = _pipeline(fusion_gates=True)
        result = p.apply_fusion_gates(_BLOCKS, query="test")
        # Fake weighter returns blocks unchanged; main point is no early return.
        assert len(result) == len(_BLOCKS)

    def test_no_config_falls_back_to_env_off(self, monkeypatch):
        """Without config, env var controls gate (default off)."""
        monkeypatch.delenv("DEPTHFUSION_FUSION_GATES_ENABLED", raising=False)
        p = RecallPipeline(mode=PipelineMode.LOCAL, config=None)
        result = p.apply_fusion_gates(_BLOCKS, query="test")
        assert result == _BLOCKS

    def test_no_config_falls_back_to_env_on(self, monkeypatch):
        """Without config, DEPTHFUSION_FUSION_GATES_ENABLED=true enables gate."""
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ENABLED", "true")
        import depthfusion.retrieval.hybrid as _mod, sys

        class _FW:
            def apply(self, blocks, *, query_embedding=None):
                return blocks, []

        class _FC:
            def version_id(self):
                return "env-test"

        fake = type(sys)("depthfusion.fusion.selective_fusion_weighter")
        fake.SelectiveFusionWeighter = _FW
        fake.SelectiveGateConfig = type("C", (), {"from_env": staticmethod(lambda: _FC())})
        monkeypatch.setitem(sys.modules, "depthfusion.fusion.selective_fusion_weighter", fake)

        p = RecallPipeline(mode=PipelineMode.LOCAL, config=None)
        result = p.apply_fusion_gates(_BLOCKS, query="test")
        assert len(result) == len(_BLOCKS)


class TestCognitiveScoringConfig:
    def test_scoring_off_by_default_returns_blocks_unchanged(self):
        p = _pipeline(cognitive_scoring=False)
        result = p.apply_cognitive_scoring(_BLOCKS)
        assert result == _BLOCKS

    def test_no_config_falls_back_to_env_off(self, monkeypatch):
        monkeypatch.delenv("DEPTHFUSION_COGNITIVE_SCORING", raising=False)
        p = RecallPipeline(mode=PipelineMode.LOCAL, config=None)
        result = p.apply_cognitive_scoring(_BLOCKS)
        assert result == _BLOCKS

    def test_config_fields_exist_on_default_config(self):
        cfg = DepthFusionConfig()
        assert hasattr(cfg, "fusion_gates_enabled")
        assert hasattr(cfg, "cognitive_scoring_enabled")
        assert cfg.fusion_gates_enabled is False
        assert cfg.cognitive_scoring_enabled is False

    def test_from_env_reads_both_vars(self, monkeypatch):
        monkeypatch.setenv("DEPTHFUSION_FUSION_GATES_ENABLED", "true")
        monkeypatch.setenv("DEPTHFUSION_COGNITIVE_SCORING", "1")
        cfg = DepthFusionConfig.from_env()
        assert cfg.fusion_gates_enabled is True
        assert cfg.cognitive_scoring_enabled is True
