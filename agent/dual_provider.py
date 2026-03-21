"""Dual-provider context budget manager for concurrent Anthropic + Codex operation.

Tracks per-provider token budgets and orchestrates hot-swaps between providers
when the active provider's context window approaches its limit. Carries a
compressed handoff summary + last N verbatim messages to the new provider so
the conversation continues seamlessly.

Config (config.yaml):

    dual_provider:
      enabled: true
      swap_threshold: 0.85
      carry_last_n_messages: 4
      providers:
        - anthropic
        - openai-codex
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Short labels used in the status bar and logs
PROVIDER_LABELS: Dict[str, str] = {
    "anthropic": "A",
    "openai-codex": "C",
}

# Default context lengths per provider (used when not yet probed)
DEFAULT_CONTEXT_LENGTHS: Dict[str, int] = {
    "anthropic": 200_000,
    "openai-codex": 192_000,
}

# Default models per provider (used during swap when ProviderConfig lacks a model field)
DEFAULT_MODELS: Dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai-codex": "codex-mini-latest",
}


def _make_provider_usage() -> Dict[str, Any]:
    """Factory for per-provider cumulative token counters."""
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "api_calls": 0,
        "estimated_cost_usd": 0.0,
        "context_tokens": 0,       # last known context usage (prompt_tokens)
        "context_length": 0,       # provider's context limit
        "swaps_from": 0,           # times we swapped away from this provider
    }


class DualProviderBudgetManager:
    """Tracks dual-provider budgets and decides when to swap.

    Instantiated once per AIAgent session when ``dual_provider.enabled`` is
    true in config.  The agent calls :meth:`check_and_swap` after every API
    response; if a swap target is returned the agent performs the handoff.
    """

    def __init__(
        self,
        provider_order: List[str],
        swap_threshold: float = 0.85,
        carry_last_n_messages: int = 4,
    ) -> None:
        self.provider_order = list(provider_order)
        self.swap_threshold = swap_threshold
        self.carry_last_n_messages = carry_last_n_messages

        # Per-provider budget state
        self.budgets: Dict[str, Dict[str, Any]] = {}
        for pid in self.provider_order:
            self.budgets[pid] = {
                "context_length": DEFAULT_CONTEXT_LENGTHS.get(pid, 200_000),
                "context_tokens": 0,
                "authenticated": False,
            }

        # Per-provider cumulative usage counters (exposed to status bar)
        self.provider_usage: Dict[str, Dict[str, Any]] = defaultdict(_make_provider_usage)

        # Total swaps performed this session
        self.total_swaps = 0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def mark_authenticated(self, provider_id: str, context_length: int = 0) -> None:
        """Mark a provider as authenticated and optionally set its context length."""
        if provider_id not in self.budgets:
            self.budgets[provider_id] = {
                "context_length": context_length or DEFAULT_CONTEXT_LENGTHS.get(provider_id, 200_000),
                "context_tokens": 0,
                "authenticated": True,
            }
        else:
            self.budgets[provider_id]["authenticated"] = True
            if context_length:
                self.budgets[provider_id]["context_length"] = context_length

    def update_context_length(self, provider_id: str, context_length: int) -> None:
        """Update a provider's known context length (after probing)."""
        if provider_id in self.budgets:
            self.budgets[provider_id]["context_length"] = context_length
        # Also update usage counters
        self.provider_usage[provider_id]["context_length"] = context_length

    # ------------------------------------------------------------------
    # Per-call tracking
    # ------------------------------------------------------------------

    def record_usage(
        self,
        provider_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        prompt_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        """Record token usage for a single API call on a provider."""
        pu = self.provider_usage[provider_id]
        pu["input_tokens"] += input_tokens
        pu["output_tokens"] += output_tokens
        pu["cache_read_tokens"] += cache_read_tokens
        pu["cache_write_tokens"] += cache_write_tokens
        pu["reasoning_tokens"] += reasoning_tokens
        pu["api_calls"] += 1
        pu["estimated_cost_usd"] += estimated_cost_usd
        pu["context_tokens"] = prompt_tokens
        if provider_id in self.budgets:
            pu["context_length"] = self.budgets[provider_id]["context_length"]

        # Mirror into budgets for swap decision
        if provider_id in self.budgets:
            self.budgets[provider_id]["context_tokens"] = prompt_tokens

    # ------------------------------------------------------------------
    # Swap decision
    # ------------------------------------------------------------------

    def check_and_swap(
        self,
        active_provider: str,
        prompt_tokens: int,
    ) -> Optional[str]:
        """Check whether the active provider should be swapped out.

        Returns the provider ID to swap *to*, or ``None`` if no swap is needed
        (either below threshold or all providers are at capacity).
        """
        active = self.budgets.get(active_provider)
        if not active:
            return None

        context_length = active["context_length"]
        if context_length <= 0:
            return None

        usage_pct = prompt_tokens / context_length
        if usage_pct < self.swap_threshold:
            return None

        # Find best alternative
        for candidate in self.provider_order:
            if candidate == active_provider:
                continue
            cand = self.budgets.get(candidate)
            if not cand:
                continue
            if not cand["authenticated"]:
                continue
            cand_ctx_len = cand["context_length"]
            if cand_ctx_len <= 0:
                continue
            cand_usage_pct = cand["context_tokens"] / cand_ctx_len
            if cand_usage_pct < self.swap_threshold:
                logger.info(
                    "Dual-provider swap: %s (%.0f%%) -> %s (%.0f%%)",
                    active_provider,
                    usage_pct * 100,
                    candidate,
                    cand_usage_pct * 100,
                )
                return candidate

        # All providers at capacity — no swap, fall back to normal compression
        return None

    def record_swap(self, from_provider: str) -> None:
        """Record that a swap occurred away from ``from_provider``."""
        self.total_swaps += 1
        self.provider_usage[from_provider]["swaps_from"] += 1
        # Reset the old provider's context_tokens so it becomes eligible
        # as a swap target again (its actual context is cleared by the swap).
        if from_provider in self.budgets:
            self.budgets[from_provider]["context_tokens"] = 0
        self.provider_usage[from_provider]["context_tokens"] = 0

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def get_provider_status(self) -> Dict[str, Dict[str, Any]]:
        """Return a status dict for each provider (for status bar rendering)."""
        result: Dict[str, Dict[str, Any]] = {}
        for pid in self.provider_order:
            pu = self.provider_usage[pid]
            budget = self.budgets.get(pid, {})
            result[pid] = {
                "total_tokens": pu["input_tokens"] + pu["output_tokens"],
                "context_tokens": pu["context_tokens"],
                "context_length": budget.get("context_length", 0),
                "authenticated": budget.get("authenticated", False),
                "api_calls": pu["api_calls"],
                "swaps_from": pu["swaps_from"],
            }
        return result

    @property
    def active_provider_count(self) -> int:
        """Number of authenticated providers."""
        return sum(
            1 for b in self.budgets.values() if b.get("authenticated")
        )


def load_dual_provider_config() -> Dict[str, Any]:
    """Load dual_provider config from the Hermes config file.

    Returns a dict with keys: enabled, swap_threshold, carry_last_n_messages,
    providers.  Returns disabled defaults if not configured.
    """
    defaults = {
        "enabled": False,
        "swap_threshold": 0.85,
        "carry_last_n_messages": 4,
        "providers": ["anthropic", "openai-codex"],
    }
    try:
        from hermes_cli.config import load_config
        cfg = load_config().get("dual_provider", {})
        if not isinstance(cfg, dict):
            return defaults
        return {
            "enabled": str(cfg.get("enabled", False)).lower() in ("true", "1", "yes"),
            "swap_threshold": float(cfg.get("swap_threshold", 0.85)),
            "carry_last_n_messages": int(cfg.get("carry_last_n_messages", 4)),
            "providers": cfg.get("providers", defaults["providers"]),
        }
    except Exception:
        return defaults
