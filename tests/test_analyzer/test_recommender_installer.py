"""Tests for InstallRecommender and DepthFusionInstaller."""
from __future__ import annotations

from depthfusion.analyzer.compatibility import GREEN, RED, YELLOW
from depthfusion.analyzer.installer import DepthFusionInstaller
from depthfusion.analyzer.recommender import InstallRecommender


def _make_check_results(**overrides) -> dict:
    """Create a minimal check_results dict, all GREEN by default."""
    base = {f"C{i}": {"status": GREEN, "message": f"C{i} ok", "detail": ""} for i in range(1, 12)}
    base.update(overrides)
    return base


def test_recommender_empty_steps_for_all_green():
    rec = InstallRecommender()
    results = _make_check_results()
    steps = rec.recommend(results)
    assert steps == []


def test_recommender_returns_step_for_red_constraint():
    rec = InstallRecommender()
    results = _make_check_results(C2={"status": RED, "message": "Too many tools", "detail": "fix it"})
    steps = rec.recommend(results)
    assert len(steps) == 1
    assert steps[0]["priority"] == "critical"
    assert "C2" in steps[0]["action"]


def test_recommender_returns_step_for_yellow_constraint():
    rec = InstallRecommender()
    results = _make_check_results(C3={"status": YELLOW, "message": "Skills dir missing", "detail": ""})
    steps = rec.recommend(results)
    assert len(steps) == 1
    assert steps[0]["priority"] == "recommended"
    assert "C3" in steps[0]["action"]


def test_recommender_multiple_issues():
    rec = InstallRecommender()
    results = _make_check_results(
        C2={"status": RED, "message": "Too many tools", "detail": ""},
        C6={"status": RED, "message": "No venv", "detail": "create one"},
        C7={"status": YELLOW, "message": "No recall", "detail": "add it"},
    )
    steps = rec.recommend(results)
    assert len(steps) == 3
    priorities = [s["priority"] for s in steps]
    assert priorities.count("critical") == 2
    assert priorities.count("recommended") == 1


def test_installer_dry_run_returns_prefixed_actions():
    installer = DepthFusionInstaller(dry_run=True)
    steps = [
        {"action": "Fix C2: too many tools", "detail": "", "priority": "critical"},
        {"action": "Review C7: no recall", "detail": "add it", "priority": "recommended"},
    ]
    completed = installer.install(steps)
    assert len(completed) == 2
    for item in completed:
        assert "[DRY RUN]" in item


def test_installer_dry_run_does_not_modify_filesystem(tmp_path):
    sentinel = tmp_path / "sentinel.txt"
    installer = DepthFusionInstaller(dry_run=True)
    steps = [{"action": f"Would create {sentinel}", "detail": "", "priority": "optional"}]
    installer.install(steps)
    assert not sentinel.exists()


def test_installer_live_mode_returns_actions():
    installer = DepthFusionInstaller(dry_run=False)
    steps = [{"action": "Do something", "detail": "", "priority": "optional"}]
    completed = installer.install(steps)
    assert completed == ["Do something"]


def test_installer_empty_steps_returns_empty():
    installer = DepthFusionInstaller(dry_run=True)
    assert installer.install([]) == []
