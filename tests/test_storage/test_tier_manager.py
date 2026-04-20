# tests/test_storage/test_tier_manager.py
from unittest.mock import patch

from depthfusion.storage.tier_manager import Tier, TierManager


def test_detect_tier_local_mode(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    tm = TierManager()
    cfg = tm.detect_tier()
    assert cfg.tier == Tier.LOCAL
    assert cfg.mode == "local"


def test_detect_tier_vps_tier1_small_corpus(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_THRESHOLD", "500")
    tm = TierManager()
    with patch.object(tm, "_count_corpus", return_value=10):
        cfg = tm.detect_tier()
    assert cfg.tier == Tier.VPS_TIER1
    assert cfg.sessions_until_promotion == 490


def test_detect_tier_vps_tier2_large_corpus(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_THRESHOLD", "500")
    tm = TierManager()
    with patch.object(tm, "_count_corpus", return_value=501):
        cfg = tm.detect_tier()
    assert cfg.tier == Tier.VPS_TIER2
    assert cfg.sessions_until_promotion == 0


def test_boundary_exactly_at_threshold(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_THRESHOLD", "500")
    tm = TierManager()
    with patch.object(tm, "_count_corpus", return_value=500):
        cfg = tm.detect_tier()
    assert cfg.tier == Tier.VPS_TIER2  # >= threshold = tier2


def test_autopromote_default_false_local(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "local")
    monkeypatch.delenv("DEPTHFUSION_TIER_AUTOPROMOTE", raising=False)
    tm = TierManager()
    assert not tm.auto_promote


def test_autopromote_true_vps(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_MODE", "vps")
    monkeypatch.setenv("DEPTHFUSION_TIER_AUTOPROMOTE", "true")
    tm = TierManager()
    assert tm.auto_promote
