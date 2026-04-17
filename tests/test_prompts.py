"""Tests for prompt template construction."""

import pytest

from llm_local.prompts import build_prompt, candle_to_prompt_dict, AVAILABLE_VERSIONS


class TestBuildPrompt:
    def test_returns_system_and_user(self):
        """build_prompt returns a (system, user) tuple."""
        candles = [_make_candle_dict()]
        system, user = build_prompt(candles, None, "v1")
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert len(system) > 0
        assert len(user) > 0

    def test_system_prompt_has_output_format(self):
        """System prompt describes the expected JSON output."""
        system, _ = build_prompt([_make_candle_dict()], None, "v1")
        assert "action" in system
        assert "confidence" in system
        assert "reasoning" in system
        assert "buy" in system

    def test_user_prompt_contains_candle_data(self):
        """User prompt includes candle OHLCV data."""
        candle = _make_candle_dict(open=65000, high=65100, close=65050)
        _, user = build_prompt([candle], None, "v1")
        assert "65000" in user
        assert "65100" in user
        assert "65050" in user

    def test_user_prompt_contains_indicators(self):
        """Indicators are included when available."""
        candle = _make_candle_dict(rsi_14=55.3, sma_24=65000.0)
        _, user = build_prompt([candle], None, "v1")
        assert "RSI: 55.3" in user
        assert "SMA24: 65000.00" in user

    def test_no_position_shows_flat(self):
        """No position shows 'NONE (flat)'."""
        _, user = build_prompt([_make_candle_dict()], None, "v1")
        assert "NONE (flat)" in user

    def test_with_position(self):
        """Position context includes entry price and P&L."""
        position = {
            "entry_price": 64000.0,
            "current_price": 65000.0,
            "unrealized_pnl_pct": 0.015625,
            "hold_candles": 5,
            "max_hold_candles": 19,
        }
        _, user = build_prompt([_make_candle_dict()], position, "v1")
        assert "64000" in user
        assert "LONG" in user

    def test_unknown_version_raises(self):
        """Unknown prompt version raises ValueError."""
        with pytest.raises(ValueError, match="Unknown prompt version"):
            build_prompt([_make_candle_dict()], None, "nonexistent")

    def test_all_versions_listed(self):
        """AVAILABLE_VERSIONS matches actual implementations."""
        for version in AVAILABLE_VERSIONS:
            system, user = build_prompt([_make_candle_dict()], None, version)
            assert len(system) > 0


class TestV2Prompt:
    """v2 targets the run #2 overtrading failure mode.

    Each test locks in one of the 5 discipline changes from
    docs/phase-1-findings.md so a refactor cannot silently delete them.
    """

    def test_v2_is_registered(self):
        assert "v2" in AVAILABLE_VERSIONS

    def test_v2_has_fee_awareness(self):
        """System prompt must name the 0.2% round-trip fee."""
        system, _ = build_prompt([_make_candle_dict()], None, "v2")
        assert "0.2%" in system
        assert "fee" in system.lower() or "round trip" in system.lower()

    def test_v2_requires_minimum_expected_move(self):
        """System prompt must set a minimum expected-move bar above fees."""
        system, _ = build_prompt([_make_candle_dict()], None, "v2")
        assert "0.5%" in system

    def test_v2_has_default_hold_bias(self):
        """System prompt must state HOLD is the default / null action."""
        system, _ = build_prompt([_make_candle_dict()], None, "v2")
        s = system.lower()
        assert "default" in s and "hold" in s

    def test_v2_has_anti_panic_exit_rule(self):
        """System prompt must forbid panic-selling on 1-2 red candles."""
        system, _ = build_prompt([_make_candle_dict()], None, "v2")
        s = system.lower()
        assert "red candle" in s or "red candles" in s
        assert "sustained" in s or "3+ candles" in s or "3+ candle" in s

    def test_v2_has_calibrated_confidence_scale(self):
        """System prompt must anchor confidence at concrete levels."""
        system, _ = build_prompt([_make_candle_dict()], None, "v2")
        assert "0.3" in system
        assert "0.7" in system
        assert "0.9" in system

    def test_v2_requires_multi_indicator_entry(self):
        """Entry bar must require 2+ signals aligning (not single-indicator chases)."""
        system, _ = build_prompt([_make_candle_dict()], None, "v2")
        s = system.lower()
        assert "two or more" in s or "2+" in s or "2 or more" in s

    def test_v2_includes_candle_and_position_like_v1(self):
        """v2 shares the user-prompt body with v1."""
        candle = _make_candle_dict(close=65050.0, rsi_14=55.3)
        position = {
            "entry_price": 64000.0,
            "current_price": 65000.0,
            "unrealized_pnl_pct": 0.015625,
            "hold_candles": 5,
            "max_hold_candles": 19,
        }
        _, user = build_prompt([candle], position, "v2")
        assert "65050" in user
        assert "RSI: 55.3" in user
        assert "LONG" in user
        assert "64000" in user


class TestCandleToPromptDict:
    def test_converts_orm_like_object(self):
        """Converts an object with candle attributes to a dict."""
        class FakeCandle:
            timestamp_ms = 1710500000000
            open = 65000.0
            high = 65100.0
            low = 64900.0
            close = 65050.0
            volume = 1234.5
            sma_24 = 65000.0
            sma_96 = None
            rsi_14 = 55.0
            macd = 100.0
            macd_signal = 90.0
            bb_upper = 66000.0
            bb_lower = 64000.0

        result = candle_to_prompt_dict(FakeCandle())
        assert result["open"] == 65000.0
        assert result["rsi_14"] == 55.0
        assert result["sma_96"] is None
        assert "time" in result


def _make_candle_dict(**overrides):
    """Helper to create a candle dict for testing."""
    defaults = {
        "time": "2026-03-15 12:00",
        "open": 65000.0,
        "high": 65100.0,
        "low": 64900.0,
        "close": 65050.0,
        "volume": 1234.5,
        "sma_24": None,
        "sma_96": None,
        "rsi_14": None,
        "macd": None,
        "macd_signal": None,
        "bb_upper": None,
        "bb_lower": None,
    }
    defaults.update(overrides)
    return defaults
