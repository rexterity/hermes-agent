"""Tests for the tool usage tracker (REX-1109)."""

from tools.tool_usage_tracker import (
    ToolUsageTracker,
    TERMINAL_TOOL_NAMES,
    _CONSECUTIVE_STREAK_THRESHOLD,
    _MIN_CALLS_FOR_PERCENT_ALERT,
    _TERMINAL_PERCENT_THRESHOLD,
)


class TestRecordAndCounters:
    def test_empty_tracker_has_zero_counts(self):
        tracker = ToolUsageTracker()
        assert tracker.total_calls == 0
        assert tracker.terminal_calls == 0
        assert tracker.consecutive_terminal == 0

    def test_record_terminal_increments_both_counters(self):
        tracker = ToolUsageTracker()
        tracker.record("terminal")
        assert tracker.total_calls == 1
        assert tracker.terminal_calls == 1
        assert tracker.consecutive_terminal == 1

    def test_record_non_terminal_increments_total_only(self):
        tracker = ToolUsageTracker()
        tracker.record("read_file")
        assert tracker.total_calls == 1
        assert tracker.terminal_calls == 0
        assert tracker.consecutive_terminal == 0

    def test_non_terminal_resets_consecutive_streak(self):
        tracker = ToolUsageTracker()
        tracker.record("terminal")
        tracker.record("terminal")
        assert tracker.consecutive_terminal == 2
        tracker.record("search_files")
        assert tracker.consecutive_terminal == 0

    def test_record_rejects_empty_string(self):
        tracker = ToolUsageTracker()
        try:
            tracker.record("")
            assert False, "Expected AssertionError"
        except AssertionError:
            pass

    def test_record_rejects_non_string(self):
        tracker = ToolUsageTracker()
        try:
            tracker.record(42)
            assert False, "Expected AssertionError"
        except AssertionError:
            pass


class TestTerminalPercent:
    def test_zero_calls_returns_zero(self):
        tracker = ToolUsageTracker()
        assert tracker.terminal_percent == 0.0

    def test_all_terminal_returns_one(self):
        tracker = ToolUsageTracker()
        for _ in range(5):
            tracker.record("terminal")
        assert tracker.terminal_percent == 1.0

    def test_half_terminal(self):
        tracker = ToolUsageTracker()
        tracker.record("terminal")
        tracker.record("read_file")
        assert tracker.terminal_percent == 0.5

    def test_mixed_usage(self):
        tracker = ToolUsageTracker()
        # 3 terminal, 7 non-terminal = 30%
        for _ in range(3):
            tracker.record("terminal")
        for _ in range(7):
            tracker.record("read_file")
        assert abs(tracker.terminal_percent - 0.3) < 1e-9


class TestGetTerminalNudge:
    def test_no_nudge_when_under_threshold(self):
        tracker = ToolUsageTracker()
        # 2 terminal, 8 non-terminal = 20% < 30%
        for _ in range(2):
            tracker.record("terminal")
        for _ in range(8):
            tracker.record("read_file")
        assert tracker.get_terminal_nudge() is None

    def test_no_nudge_when_too_few_calls(self):
        tracker = ToolUsageTracker()
        # 100% terminal but only 3 calls -- below _MIN_CALLS_FOR_PERCENT_ALERT
        for _ in range(3):
            tracker.record("terminal")
        assert tracker.total_calls < _MIN_CALLS_FOR_PERCENT_ALERT
        # Should not fire percent nudge (streak nudge fires separately)
        nudge = tracker.get_terminal_nudge()
        # Only streak nudge should fire at 3 consecutive
        assert nudge is not None
        assert "TOOL HINT" in nudge
        assert "TOOL SELECTION" not in nudge

    def test_percent_nudge_fires_above_threshold(self):
        tracker = ToolUsageTracker()
        # 5 terminal, 5 non-terminal = 50% > 30%, total = 10 >= min
        for _ in range(5):
            tracker.record("terminal")
        for _ in range(5):
            tracker.record("read_file")
        # Streak was reset by non-terminal calls, so only percent nudge
        nudge = tracker.get_terminal_nudge()
        assert nudge is not None
        assert "TOOL SELECTION" in nudge
        assert "50%" in nudge

    def test_streak_nudge_fires_at_threshold(self):
        tracker = ToolUsageTracker()
        for _ in range(_CONSECUTIVE_STREAK_THRESHOLD):
            tracker.record("terminal")
        nudge = tracker.get_terminal_nudge()
        assert nudge is not None
        assert "TOOL HINT" in nudge
        assert f"{_CONSECUTIVE_STREAK_THRESHOLD} consecutive" in nudge

    def test_streak_nudge_fires_only_once_per_streak(self):
        tracker = ToolUsageTracker()
        for _ in range(_CONSECUTIVE_STREAK_THRESHOLD):
            tracker.record("terminal")
        first_nudge = tracker.get_terminal_nudge()
        assert first_nudge is not None
        assert "TOOL HINT" in first_nudge

        # Record another terminal call in same streak
        tracker.record("terminal")
        second_nudge = tracker.get_terminal_nudge()
        # Streak nudge should NOT repeat (only percent nudge if applicable)
        if second_nudge is not None:
            assert "TOOL HINT" not in second_nudge

    def test_streak_nudge_resets_after_non_terminal(self):
        tracker = ToolUsageTracker()
        for _ in range(_CONSECUTIVE_STREAK_THRESHOLD):
            tracker.record("terminal")
        first_nudge = tracker.get_terminal_nudge()
        assert "TOOL HINT" in first_nudge

        # Break the streak
        tracker.record("read_file")
        # Start a new streak
        for _ in range(_CONSECUTIVE_STREAK_THRESHOLD):
            tracker.record("terminal")
        new_nudge = tracker.get_terminal_nudge()
        assert new_nudge is not None
        assert "TOOL HINT" in new_nudge

    def test_both_nudges_fire_simultaneously(self):
        tracker = ToolUsageTracker()
        # Build up enough calls to trigger percent nudge
        # 8 terminal, 2 non-terminal = 80%, total = 10
        for _ in range(2):
            tracker.record("read_file")
        # Now do 8 terminal in a row (triggers both streak and percent)
        for _ in range(8):
            tracker.record("terminal")
        nudge = tracker.get_terminal_nudge()
        assert nudge is not None
        assert "TOOL SELECTION" in nudge
        assert "TOOL HINT" in nudge


class TestReset:
    def test_reset_clears_all_counters(self):
        tracker = ToolUsageTracker()
        for _ in range(5):
            tracker.record("terminal")
        tracker.record("read_file")
        tracker.reset()
        assert tracker.total_calls == 0
        assert tracker.terminal_calls == 0
        assert tracker.consecutive_terminal == 0
        assert tracker.terminal_percent == 0.0
        assert tracker.get_terminal_nudge() is None


class TestTerminalToolNames:
    def test_terminal_is_tracked(self):
        assert "terminal" in TERMINAL_TOOL_NAMES

    def test_non_terminal_tools_not_tracked(self):
        for name in ("read_file", "write_file", "patch", "search_files",
                      "execute_code", "todo", "memory"):
            assert name not in TERMINAL_TOOL_NAMES


class TestConstants:
    def test_threshold_is_thirty_percent(self):
        assert _TERMINAL_PERCENT_THRESHOLD == 0.30

    def test_streak_threshold_is_reasonable(self):
        assert _CONSECUTIVE_STREAK_THRESHOLD >= 2
        assert _CONSECUTIVE_STREAK_THRESHOLD <= 10

    def test_min_calls_is_reasonable(self):
        assert _MIN_CALLS_FOR_PERCENT_ALERT >= 5
        assert _MIN_CALLS_FOR_PERCENT_ALERT <= 20
