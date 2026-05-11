from depthfusion.retrieval.hybrid import PipelineMode, RecallPipeline


def test_local_mode(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    pipeline = RecallPipeline.from_env()
    assert pipeline.mode == PipelineMode.LOCAL


def test_vps_cpu_is_not_local(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-cpu")
    # Should NOT be LOCAL — this was the bug
    pipeline = RecallPipeline.from_env()
    assert pipeline.mode != PipelineMode.LOCAL


def test_vps_gpu_is_not_local(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps-gpu")
    pipeline = RecallPipeline.from_env()
    assert pipeline.mode != PipelineMode.LOCAL


def test_legacy_vps_alias_is_not_local(monkeypatch):
    import warnings
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        pipeline = RecallPipeline.from_env()
    assert pipeline.mode != PipelineMode.LOCAL


def test_missing_mode_env_defaults_to_local(monkeypatch):
    monkeypatch.delenv("DEPTHFUSION_MODE", raising=False)
    pipeline = RecallPipeline.from_env()
    assert pipeline.mode == PipelineMode.LOCAL
