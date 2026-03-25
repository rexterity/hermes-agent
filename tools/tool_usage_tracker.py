"""Track per-session tool usage to detect terminal dominance.

The tracker counts every tool call, partitions them into *terminal* vs
*non-terminal*, and exposes two signals the agent loop can act on:

1. **Terminal percentage** -- when terminal calls exceed 30% of all calls
   (and there are enough samples), a nudge is injected into the next tool
   result so the model self-corrects.
2. **Consecutive terminal streak** -- when the model makes 3+ terminal
   calls in a row *without* interleaving a purpose-built tool, a lighter
   nudge reminds it that search_files / read_file / patch exist.

Both nudges are plain-text strings suitable for appending to a tool
result message (same pattern as ``_get_budget_warning``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Tool names that count as "terminal" usage.
TERMINAL_TOOL_NAMES: frozenset[str] = frozenset({"terminal",})

# Minimum number of tool calls before we start alerting on percentage.
# Avoids noisy warnings during the first few calls of a session.
_MIN_CALLS_FOR_PERCENT_ALERT = 10

# Threshold above which we inject a percentage-based nudge.
_TERMINAL_PERCENT_THRESHOLD = 0.30

# Consecutive terminal calls that trigger a streak nudge.
_CONSECUTIVE_STREAK_THRESHOLD = 3


class ToolUsageTracker:
    """Lightweight per-session counter for terminal vs non-terminal calls.

    Designed to be instantiated once per ``AIAgent`` and called from the
    tool-dispatch loop.  All public methods are safe to call from a single
    thread (the agent loop is single-threaded).

    >>> tracker = ToolUsageTracker()
    >>> tracker.record("terminal")
    >>> tracker.record("read_file")
    >>> tracker.terminal_percent  # doctest: +ELLIPSIS
    0.5
    """

    def __init__(self) -> None:
        self._total_calls: int = 0
        self._terminal_calls: int = 0
        self._consecutive_terminal: int = 0
        # Track whether we already emitted a streak nudge this streak so
        # we don't repeat it on every single call in a long run.
        self._streak_nudge_emitted: bool = False

    # ── Recording ────────────────────────────────────────────────────

    def record(self, tool_name: str) -> None:
        """Record a single tool invocation."""
        assert isinstance(tool_name, str), "tool_name must be a string"
        assert len(tool_name) > 0, "tool_name must not be empty"

        self._total_calls += 1
        if tool_name in TERMINAL_TOOL_NAMES:
            self._terminal_calls += 1
            self._consecutive_terminal += 1
        else:
            self._consecutive_terminal = 0
            self._streak_nudge_emitted = False

    # ── Queries ──────────────────────────────────────────────────────

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def terminal_calls(self) -> int:
        return self._terminal_calls

    @property
    def terminal_percent(self) -> float:
        """Return terminal calls as a fraction of total (0.0 .. 1.0)."""
        if self._total_calls == 0:
            return 0.0
        return self._terminal_calls / self._total_calls

    @property
    def consecutive_terminal(self) -> int:
        return self._consecutive_terminal

    # ── Nudge generation ─────────────────────────────────────────────

    def get_terminal_nudge(self) -> str | None:
        """Return a nudge string if terminal usage is too high, else None.

        Checks both percentage threshold and consecutive-streak threshold.
        The streak nudge is emitted at most once per unbroken streak.
        """
        parts: list[str] = []

        # Percentage-based nudge
        if (self._total_calls >= _MIN_CALLS_FOR_PERCENT_ALERT
                and self.terminal_percent > _TERMINAL_PERCENT_THRESHOLD):
            pct = int(self.terminal_percent * 100)
            parts.append(
                f"[TOOL SELECTION: Terminal is {pct}% of your tool calls "
                f"({self._terminal_calls}/{self._total_calls}), target is "
                f"<{int(_TERMINAL_PERCENT_THRESHOLD * 100)}%. "
                "Use search_files instead of grep/find, read_file instead "
                "of cat/head/tail, patch instead of sed/awk, and "
                "execute_code for multi-step shell scripts.]"
            )

        # Streak-based nudge (lighter, fires once per streak)
        if (self._consecutive_terminal >= _CONSECUTIVE_STREAK_THRESHOLD
                and not self._streak_nudge_emitted):
            parts.append(
                f"[TOOL HINT: {self._consecutive_terminal} consecutive "
                "terminal calls — consider using a purpose-built tool "
                "(search_files, read_file, patch, write_file, "
                "execute_code) for the next operation.]"
            )
            self._streak_nudge_emitted = True

        if not parts:
            return None

        nudge = " ".join(parts)
        logger.info(
            "Terminal nudge fired: %d/%d calls (%.0f%%), streak=%d",
            self._terminal_calls, self._total_calls,
            self.terminal_percent * 100, self._consecutive_terminal,
        )
        return nudge

    def reset(self) -> None:
        """Reset all counters (e.g. at the start of a new conversation)."""
        self._total_calls = 0
        self._terminal_calls = 0
        self._consecutive_terminal = 0
        self._streak_nudge_emitted = False
