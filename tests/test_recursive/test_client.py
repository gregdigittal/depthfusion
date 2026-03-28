"""Tests for RLMClient."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from depthfusion.core.config import DepthFusionConfig
from depthfusion.recursive.client import RLMClient, _estimate_cost
from depthfusion.recursive.trajectory import RecursiveTrajectory


def test_is_available_returns_bool():
    client = RLMClient()
    result = client.is_available()
    assert isinstance(result, bool)


def test_run_when_rlm_unavailable_returns_stub():
    """When rlm is not importable, run() must return stub without crashing."""
    with patch("depthfusion.recursive.client._check_rlm_available", return_value=False):
        client = RLMClient()
        client._available = False
        result_text, traj = client.run("test query", "some content here")
        assert result_text == "rlm not available"
        assert isinstance(traj, RecursiveTrajectory)
        assert traj.completed is True


def test_cost_ceiling_raises_value_error():
    """When estimated cost exceeds ceiling, ValueError must be raised."""
    config = DepthFusionConfig(rlm_cost_ceiling=0.00001)  # tiny ceiling
    client = RLMClient(config=config)
    # Force rlm to appear available so we reach the cost check
    client._available = True

    # Large content to push estimated cost above tiny ceiling
    large_content = " ".join(["word"] * 10000)

    with pytest.raises(ValueError, match="Estimated cost"):
        client.run("query", large_content, max_cost=0.00001)


def test_cost_ceiling_uses_config_default():
    """max_cost=None should fall back to config.rlm_cost_ceiling."""
    config = DepthFusionConfig(rlm_cost_ceiling=0.000001)
    client = RLMClient(config=config)
    client._available = True

    big_content = " ".join(["word"] * 5000)
    with pytest.raises(ValueError):
        client.run("query", big_content)


def test_strategy_auto_selected_peek_for_short_content():
    """Short content should auto-select 'peek' strategy."""
    client = RLMClient()
    client._available = False
    short_content = "short text"
    _, traj = client.run("query", short_content)
    assert traj.strategy == "peek"


def test_strategy_auto_selected_summarize_for_long_content():
    """Very long content (>20000 tokens) should auto-select 'summarize'."""
    client = RLMClient()
    client._available = False
    long_content = " ".join(["word"] * 25000)
    _, traj = client.run("query", long_content)
    assert traj.strategy == "summarize"


def test_explicit_strategy_respected():
    """Explicit strategy parameter should override auto-selection."""
    client = RLMClient()
    client._available = False
    _, traj = client.run("query", "short text", strategy="grep")
    assert traj.strategy == "grep"


def test_estimate_cost_is_positive_for_nonempty_content():
    cost = _estimate_cost("hello world this is content")
    assert cost > 0.0


# ── Lines 22-24: ImportError branch in _check_rlm_available ──────────────────

def test_check_rlm_available_handles_import_error():
    """When rlm import fails, _check_rlm_available() returns False (covers lines 22-24)."""
    import sys
    import depthfusion.recursive.client as client_mod

    original_available = client_mod._RLM_AVAILABLE
    try:
        # Reset the module-level cache so _check_rlm_available() re-evaluates
        client_mod._RLM_AVAILABLE = None
        # Setting sys.modules["rlm"] = None causes `import rlm` to raise ImportError
        with patch.dict(sys.modules, {"rlm": None}):
            result = client_mod._check_rlm_available()
        assert result is False
        assert client_mod._RLM_AVAILABLE is False
    finally:
        # Always restore so other tests are unaffected
        client_mod._RLM_AVAILABLE = original_available


# ── Lines 90-109: successful rlm execution path ──────────────────────────────

def test_run_succeeds_with_mocked_rlm():
    """When rlm is available and cost is within ceiling, returns result + completed trajectory.

    Covers lines 90-109 — the live rlm execution path.
    """
    from unittest.mock import MagicMock
    import sys

    mock_rlm_pkg = MagicMock()
    mock_rlm_instance = MagicMock()
    mock_rlm_instance.completion.return_value = "mocked rlm result"
    mock_rlm_pkg.RLM.return_value = mock_rlm_instance

    client = RLMClient()
    client._available = True

    with patch.dict(sys.modules, {"rlm": mock_rlm_pkg}):
        result_text, traj = client.run("query", "short content")

    assert result_text == "mocked rlm result"
    assert traj.completed is True
    assert traj.error is None
    mock_rlm_pkg.RLM.assert_called_once()
    mock_rlm_instance.completion.assert_called_once()


# ── Lines 111-114: exception propagation path ────────────────────────────────

def test_run_exception_propagates_when_rlm_fails():
    """When rlm raises during execution, exception propagates from run().

    Covers lines 111-114 — the except block that marks trajectory incomplete.
    """
    from unittest.mock import MagicMock
    import sys

    mock_rlm_pkg = MagicMock()
    mock_rlm_pkg.RLM.side_effect = RuntimeError("rlm internal error")

    client = RLMClient()
    client._available = True

    with patch.dict(sys.modules, {"rlm": mock_rlm_pkg}):
        with pytest.raises(RuntimeError, match="rlm internal error"):
            client.run("query", "short content")
