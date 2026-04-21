# tests/test_recursive/test_task_budget.py
"""Opus 4.7 task-budget probe + integration tests — S-54 / T-168 / TG-13.

AC-3: ≥ 4 new tests. Covers:
  - CostEstimator.budget_tokens_for_ceiling translation math
  - _task_budget_beta_available probe (env gate + SDK probe)
  - RLMClient.run passes task_budget_tokens when beta available AND
    rlm accepts the kwarg
  - Graceful fallback when SDK lacks beta OR env var is off
  - Best-effort wrapper per §TG-13 kill-criterion — no CIQS claim

Uses a mock Anthropic SDK module (via sys.modules injection) to exercise
the probe paths without requiring a real SDK upgrade.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from depthfusion.core.config import DepthFusionConfig
from depthfusion.recursive.client import (
    RLMClient,
    _task_budget_beta_available,
)
from depthfusion.router.cost_estimator import CostEstimator

# ---------------------------------------------------------------------------
# CostEstimator.budget_tokens_for_ceiling
# ---------------------------------------------------------------------------

class TestBudgetTokensForCeiling:
    def test_zero_ceiling_returns_zero_tokens(self):
        est = CostEstimator()
        assert est.budget_tokens_for_ceiling(0.0, "opus") == 0

    def test_negative_ceiling_returns_zero(self):
        """Defensive: negative ceilings don't produce negative budgets."""
        est = CostEstimator()
        assert est.budget_tokens_for_ceiling(-1.0, "opus") == 0

    def test_opus_ceiling_translation(self):
        """Opus input pricing is $0.015 per 1K tokens, so $0.50 → 33333 tokens."""
        est = CostEstimator()
        # 0.50 / 0.015 * 1000 = 33333.33 → floor → 33333
        assert est.budget_tokens_for_ceiling(0.50, "opus") == 33333

    def test_haiku_ceiling_translation(self):
        """Haiku input pricing is $0.00025 per 1K tokens, so $0.50 → 2_000_000."""
        est = CostEstimator()
        assert est.budget_tokens_for_ceiling(0.50, "haiku") == 2_000_000

    def test_unknown_model_raises_keyerror(self):
        est = CostEstimator()
        with pytest.raises(KeyError):
            est.budget_tokens_for_ceiling(1.0, "nonexistent-model")

    def test_sonnet_ceiling_translation(self):
        """Sonnet input pricing $0.003/1K → $1.00 → 333333 tokens."""
        est = CostEstimator()
        assert est.budget_tokens_for_ceiling(1.0, "sonnet") == 333_333

    def test_floor_semantics_never_exceed_input_rational(self):
        """Floor (int()) never produces a token count whose INPUT cost
        exceeds the USD ceiling. This is NOT a guarantee that real spend
        stays within the ceiling — output tokens cost 5× more and can
        push actual cost above it. See docstring "Important caveat".
        """
        est = CostEstimator()
        ceiling = 0.37
        budget = est.budget_tokens_for_ceiling(ceiling, "opus")
        implied_input_cost = budget * 0.015 / 1000
        assert implied_input_cost <= ceiling

    def test_output_heavy_spend_can_exceed_ceiling(self):
        """Document the real-world worst case so operators see it in tests.

        For opus ($0.075/1K output vs $0.015/1K input = 5× ratio), a
        ceiling-budget's token count consumed entirely as output can
        exceed the ceiling by the full output/input ratio.
        """
        est = CostEstimator()
        ceiling = 0.50
        budget = est.budget_tokens_for_ceiling(ceiling, "opus")
        worst_case_output_cost = budget * 0.075 / 1000
        # Worst case: ~5× ceiling — this is the documented behaviour.
        assert worst_case_output_cost > ceiling
        assert worst_case_output_cost < 6.0 * ceiling  # ratio sanity


# ---------------------------------------------------------------------------
# _task_budget_beta_available probe
# ---------------------------------------------------------------------------

class TestProbeGate:
    def test_env_var_off_returns_false(self, monkeypatch):
        """Default: env var unset → feature disabled regardless of SDK."""
        monkeypatch.delenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", raising=False)
        # Inject a mock anthropic that WOULD claim support
        fake_anthropic = SimpleNamespace(task_budget=object())
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        assert _task_budget_beta_available() is False

    def test_env_var_on_but_sdk_missing_returns_false(self, monkeypatch):
        """Env var on, but SDK has no task_budget surface → still False."""
        monkeypatch.setenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", "true")
        fake_anthropic = SimpleNamespace()  # no task_budget, no types
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        assert _task_budget_beta_available() is False

    def test_env_var_on_and_module_attr_returns_true(self, monkeypatch):
        """Env var on + anthropic.task_budget attribute present → True."""
        monkeypatch.setenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", "true")
        fake_anthropic = SimpleNamespace(task_budget=object())
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        assert _task_budget_beta_available() is True

    def test_env_var_on_and_types_subfeature_returns_true(self, monkeypatch):
        """Secondary probe: anthropic.types.TaskBudget also qualifies."""
        monkeypatch.setenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", "true")
        fake_types = SimpleNamespace(TaskBudget=object())
        fake_anthropic = SimpleNamespace(types=fake_types)
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        assert _task_budget_beta_available() is True

    def test_import_failure_returns_false(self, monkeypatch):
        """If anthropic itself isn't importable, probe fails closed.

        Implementation note: setting `sys.modules["anthropic"] = None`
        uses Python's negative-import-cache mechanism — a bare
        `import anthropic` against a None entry raises `ImportError`.
        This behaviour is documented for CPython and stable across 3.10+
        but is an implementation detail of the import machinery. If a
        future Python version changes how `None` entries in `sys.modules`
        behave, this test may need to switch to patching `__import__` or
        deleting the module from `sys.modules` entirely.
        """
        monkeypatch.setenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", "true")
        monkeypatch.setitem(sys.modules, "anthropic", None)
        assert _task_budget_beta_available() is False


# ---------------------------------------------------------------------------
# RLMClient integration
# ---------------------------------------------------------------------------

def _make_fake_rlm_module(*, accepts_task_budget: bool = False):
    """Build a mock rlm package exposing a stub RLM class.

    When `accepts_task_budget=True`, the RLM.__init__ signature includes
    `task_budget_tokens` so the `inspect.signature` probe in client.py
    picks it up. The captured kwargs are stored on the class (via
    `_last_init_kwargs`) so tests can assert what was passed without
    needing to replace __init__ with a spy — which would corrupt the
    signature and break the probe.
    """
    captured: dict[str, object] = {}

    if accepts_task_budget:
        def init(self, backend=None, max_budget=None, max_timeout=None,
                 task_budget_tokens=None):
            captured.clear()
            captured.update(
                backend=backend, max_budget=max_budget,
                max_timeout=max_timeout, task_budget_tokens=task_budget_tokens,
            )
    else:
        def init(self, backend=None, max_budget=None, max_timeout=None):
            captured.clear()
            captured.update(
                backend=backend, max_budget=max_budget, max_timeout=max_timeout,
            )

    class FakeRLM:
        __init__ = init

        def completion(self, prompt: str) -> str:
            return "fake-completion"

    module = SimpleNamespace(RLM=FakeRLM)
    # Attach the shared capture dict so tests can inspect after the call
    module._captured = captured  # type: ignore[attr-defined]
    return module


class TestRLMClientTaskBudget:
    def _install_beta_available(self, monkeypatch):
        """Make `_task_budget_beta_available()` return True."""
        monkeypatch.setenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", "true")
        monkeypatch.setitem(
            sys.modules, "anthropic",
            SimpleNamespace(task_budget=object()),
        )

    def _make_client_available(self, monkeypatch):
        """Force `_check_rlm_available` to report True regardless of real rlm."""
        import depthfusion.recursive.client as client_mod
        monkeypatch.setattr(client_mod, "_RLM_AVAILABLE", True)

    def test_passes_task_budget_when_supported(self, monkeypatch):
        """Env var on + anthropic beta + rlm accepts kwarg → RLM gets it."""
        self._install_beta_available(monkeypatch)
        self._make_client_available(monkeypatch)
        fake_rlm = _make_fake_rlm_module(accepts_task_budget=True)
        monkeypatch.setitem(sys.modules, "rlm", fake_rlm)

        client = RLMClient(config=DepthFusionConfig(rlm_cost_ceiling=0.50))
        result, traj = client.run(query="q", content="some content")
        assert result == "fake-completion"
        assert traj.completed
        # The RLM was constructed with a task_budget_tokens kwarg set to the
        # Opus-translated token budget: $0.50 / $0.015 per 1K = 33333 tokens.
        captured = fake_rlm._captured
        assert captured.get("task_budget_tokens") == 33333

    def test_skips_kwarg_when_rlm_does_not_accept(self, monkeypatch, caplog):
        """Env var on + anthropic beta BUT rlm signature doesn't accept
        `task_budget_tokens` → kwarg silently dropped, post-hoc fallback used.
        """
        import logging
        caplog.set_level(logging.DEBUG, logger="depthfusion.recursive.client")
        self._install_beta_available(monkeypatch)
        self._make_client_available(monkeypatch)
        # rlm DOES NOT accept task_budget_tokens
        fake_rlm = _make_fake_rlm_module(accepts_task_budget=False)
        monkeypatch.setitem(sys.modules, "rlm", fake_rlm)

        client = RLMClient(config=DepthFusionConfig(rlm_cost_ceiling=0.50))
        client.run(query="q", content="some content")
        # Kwarg NOT passed to rlm — the signature probe rejected it
        assert "task_budget_tokens" not in fake_rlm._captured
        # DEBUG log names the reason so operators can diagnose
        assert any(
            "task_budget_tokens" in r.message or "post-hoc" in r.message
            for r in caplog.records
        )

    def test_no_kwarg_when_env_var_off(self, monkeypatch):
        """Env var off → RLM constructed with NO task_budget_tokens kwarg,
        preserving v0.4.x byte-identity.
        """
        monkeypatch.delenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", raising=False)
        self._make_client_available(monkeypatch)
        fake_rlm = _make_fake_rlm_module(accepts_task_budget=True)
        monkeypatch.setitem(sys.modules, "rlm", fake_rlm)

        client = RLMClient(config=DepthFusionConfig(rlm_cost_ceiling=0.50))
        client.run(query="q", content="some content")
        # When the feature is disabled, we still pass None because our
        # fake accepts the kwarg with a default; the client code didn't
        # include it in the kwargs dict though. Verify the client didn't
        # ADD the kwarg — fake default is None.
        assert fake_rlm._captured.get("task_budget_tokens") is None

    def test_post_hoc_cost_ceiling_still_enforced(self, monkeypatch):
        """S-54 adds API-side budgeting on top of, not instead of, the
        pre-flight cost-ceiling check. A cost-exceeded call still raises
        ValueError before any RLM is constructed, whether or not
        task-budgets is enabled.
        """
        self._install_beta_available(monkeypatch)
        self._make_client_available(monkeypatch)
        fake_rlm = _make_fake_rlm_module(accepts_task_budget=True)
        monkeypatch.setitem(sys.modules, "rlm", fake_rlm)

        # Ceiling so low that even a tiny content string exceeds it
        client = RLMClient(config=DepthFusionConfig(rlm_cost_ceiling=0.0000001))
        # 10 words at $0.00001/token = $0.0001 > ceiling
        huge_content = "word " * 50
        with pytest.raises(ValueError, match="exceeds"):
            client.run(query="q", content=huge_content)


# ---------------------------------------------------------------------------
# Sanity: the feature is a no-op in the default environment (no SDK upgrade)
# ---------------------------------------------------------------------------

def test_default_environment_does_not_enable_task_budget(monkeypatch):
    """Safety net: a clean environment with the real anthropic SDK installed
    and DEPTHFUSION_RLM_TASK_BUDGET_ENABLED unset MUST return False, so
    the feature never silently activates on SDK upgrades alone.
    """
    monkeypatch.delenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", raising=False)
    # Don't mock anthropic — use whatever's installed
    assert _task_budget_beta_available() is False


def test_mock_sdk_without_env_var_rejected(monkeypatch):
    """Even a mocked SDK that claims support is rejected when the env
    var is missing — belt-and-braces.
    """
    monkeypatch.delenv("DEPTHFUSION_RLM_TASK_BUDGET_ENABLED", raising=False)
    fake_anthropic = MagicMock()
    fake_anthropic.task_budget = object()
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    assert _task_budget_beta_available() is False
