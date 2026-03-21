"""Tests for the dual-provider budget manager and context swapping logic."""

import pytest

from agent.dual_provider import (
    DEFAULT_CONTEXT_LENGTHS,
    PROVIDER_LABELS,
    DualProviderBudgetManager,
    _make_provider_usage,
    load_dual_provider_config,
)


class TestProviderUsageFactory:
    def test_returns_dict_with_expected_keys(self):
        usage = _make_provider_usage()
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0
        assert usage["cache_read_tokens"] == 0
        assert usage["cache_write_tokens"] == 0
        assert usage["reasoning_tokens"] == 0
        assert usage["api_calls"] == 0
        assert usage["estimated_cost_usd"] == 0.0
        assert usage["context_tokens"] == 0
        assert usage["context_length"] == 0
        assert usage["swaps_from"] == 0


class TestDualProviderBudgetManager:
    def _make_manager(self, **kwargs):
        defaults = {
            "provider_order": ["anthropic", "openai-codex"],
            "swap_threshold": 0.85,
            "carry_last_n_messages": 4,
        }
        defaults.update(kwargs)
        mgr = DualProviderBudgetManager(**defaults)
        mgr.mark_authenticated("anthropic", 200_000)
        mgr.mark_authenticated("openai-codex", 192_000)
        return mgr

    # -- Initialisation --------------------------------------------------------

    def test_init_sets_provider_order(self):
        mgr = self._make_manager()
        assert mgr.provider_order == ["anthropic", "openai-codex"]

    def test_init_sets_default_context_lengths(self):
        mgr = self._make_manager()
        assert mgr.budgets["anthropic"]["context_length"] == 200_000
        assert mgr.budgets["openai-codex"]["context_length"] == 192_000

    def test_init_sets_swap_threshold(self):
        mgr = self._make_manager(swap_threshold=0.90)
        assert mgr.swap_threshold == 0.90

    # -- mark_authenticated ----------------------------------------------------

    def test_mark_authenticated_sets_flag(self):
        mgr = DualProviderBudgetManager(provider_order=["anthropic"])
        assert not mgr.budgets["anthropic"]["authenticated"]
        mgr.mark_authenticated("anthropic")
        assert mgr.budgets["anthropic"]["authenticated"]

    def test_mark_authenticated_with_context_length(self):
        mgr = DualProviderBudgetManager(provider_order=["anthropic"])
        mgr.mark_authenticated("anthropic", context_length=100_000)
        assert mgr.budgets["anthropic"]["context_length"] == 100_000
        assert mgr.budgets["anthropic"]["authenticated"]

    def test_mark_authenticated_unknown_provider(self):
        mgr = DualProviderBudgetManager(provider_order=["anthropic"])
        mgr.mark_authenticated("new-provider", 50_000)
        assert mgr.budgets["new-provider"]["authenticated"]
        assert mgr.budgets["new-provider"]["context_length"] == 50_000

    # -- record_usage ----------------------------------------------------------

    def test_record_usage_accumulates(self):
        mgr = self._make_manager()
        mgr.record_usage("anthropic", input_tokens=100, output_tokens=50, prompt_tokens=150)
        mgr.record_usage("anthropic", input_tokens=200, output_tokens=100, prompt_tokens=450)
        pu = mgr.provider_usage["anthropic"]
        assert pu["input_tokens"] == 300
        assert pu["output_tokens"] == 150
        assert pu["api_calls"] == 2
        assert pu["context_tokens"] == 450  # last prompt_tokens

    def test_record_usage_mirrors_to_budgets(self):
        mgr = self._make_manager()
        mgr.record_usage("anthropic", prompt_tokens=170_000)
        assert mgr.budgets["anthropic"]["context_tokens"] == 170_000

    # -- check_and_swap --------------------------------------------------------

    def test_no_swap_below_threshold(self):
        mgr = self._make_manager()
        result = mgr.check_and_swap("anthropic", 100_000)  # 50% of 200K
        assert result is None

    def test_swap_at_threshold(self):
        mgr = self._make_manager()
        # 85% of 200K = 170K
        result = mgr.check_and_swap("anthropic", 170_000)
        assert result == "openai-codex"

    def test_swap_above_threshold(self):
        mgr = self._make_manager()
        result = mgr.check_and_swap("anthropic", 190_000)
        assert result == "openai-codex"

    def test_no_swap_when_candidate_also_at_capacity(self):
        mgr = self._make_manager()
        # Push codex above threshold too
        mgr.budgets["openai-codex"]["context_tokens"] = 180_000
        result = mgr.check_and_swap("anthropic", 190_000)
        assert result is None  # both at capacity

    def test_no_swap_when_candidate_not_authenticated(self):
        mgr = DualProviderBudgetManager(
            provider_order=["anthropic", "openai-codex"],
        )
        mgr.mark_authenticated("anthropic", 200_000)
        # codex NOT authenticated
        result = mgr.check_and_swap("anthropic", 190_000)
        assert result is None

    def test_no_swap_unknown_provider(self):
        mgr = self._make_manager()
        result = mgr.check_and_swap("unknown-provider", 100_000)
        assert result is None

    def test_no_swap_zero_context_length(self):
        mgr = self._make_manager()
        mgr.budgets["anthropic"]["context_length"] = 0
        result = mgr.check_and_swap("anthropic", 100_000)
        assert result is None

    def test_swap_back_to_anthropic(self):
        mgr = self._make_manager()
        # Codex is active and above threshold; anthropic is below
        mgr.budgets["anthropic"]["context_tokens"] = 50_000
        result = mgr.check_and_swap("openai-codex", 170_000)
        assert result == "anthropic"

    def test_custom_swap_threshold(self):
        mgr = self._make_manager(swap_threshold=0.50)
        # 50% of 200K = 100K => swap at 100K
        result = mgr.check_and_swap("anthropic", 100_000)
        assert result == "openai-codex"

        result_below = mgr.check_and_swap("anthropic", 99_000)
        assert result_below is None

    # -- record_swap -----------------------------------------------------------

    def test_record_swap_increments_counters(self):
        mgr = self._make_manager()
        mgr.record_swap("anthropic")
        assert mgr.total_swaps == 1
        assert mgr.provider_usage["anthropic"]["swaps_from"] == 1

        mgr.record_swap("anthropic")
        assert mgr.total_swaps == 2
        assert mgr.provider_usage["anthropic"]["swaps_from"] == 2

    # -- get_provider_status ---------------------------------------------------

    def test_get_provider_status(self):
        mgr = self._make_manager()
        mgr.record_usage("anthropic", input_tokens=100, output_tokens=50, prompt_tokens=150)
        status = mgr.get_provider_status()
        assert "anthropic" in status
        assert "openai-codex" in status
        assert status["anthropic"]["total_tokens"] == 150
        assert status["anthropic"]["authenticated"]

    # -- active_provider_count -------------------------------------------------

    def test_active_provider_count(self):
        mgr = self._make_manager()
        assert mgr.active_provider_count == 2

    def test_active_provider_count_one(self):
        mgr = DualProviderBudgetManager(provider_order=["anthropic", "openai-codex"])
        mgr.mark_authenticated("anthropic")
        assert mgr.active_provider_count == 1


class TestProviderLabels:
    def test_known_providers(self):
        assert PROVIDER_LABELS["anthropic"] == "A"
        assert PROVIDER_LABELS["openai-codex"] == "C"


class TestDefaultContextLengths:
    def test_known_providers(self):
        assert DEFAULT_CONTEXT_LENGTHS["anthropic"] == 200_000
        assert DEFAULT_CONTEXT_LENGTHS["openai-codex"] == 192_000


class TestLoadDualProviderConfig:
    def test_returns_defaults_when_no_config(self, monkeypatch):
        # Force import failure to exercise the except branch
        import agent.dual_provider as dp
        original = dp.load_dual_provider_config

        def _patched():
            # Simulate import error for hermes_cli.config
            import importlib
            import sys
            saved = sys.modules.get("hermes_cli.config")
            sys.modules["hermes_cli.config"] = None  # type: ignore[assignment]
            try:
                result = original()
            finally:
                if saved is not None:
                    sys.modules["hermes_cli.config"] = saved
                else:
                    sys.modules.pop("hermes_cli.config", None)
            return result

        cfg = _patched()
        assert cfg["enabled"] is False
        assert cfg["swap_threshold"] == 0.85
        assert cfg["carry_last_n_messages"] == 4
        assert cfg["providers"] == ["anthropic", "openai-codex"]
