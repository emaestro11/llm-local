"""Tests for the decision harness (LLM call + JSON parsing)."""

import json
import pytest
from unittest.mock import patch, MagicMock

from llm_local.config import Config
from llm_local.harness import (
    make_decision,
    TradeDecision,
    CircuitBreakerError,
    _validate_action,
    _clamp,
    DECISION_JSON_SCHEMA,
)


class TestMakeDecision:
    def test_happy_path(self, mock_llm_response):
        """Valid LLM response returns a proper TradeDecision."""
        config = Config(timeout_seconds=5)
        candles = [_make_candle()]

        with patch("llm_local.harness.OpenAI") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_llm_response(
                action="buy", confidence=0.8, size_pct=0.3, reasoning="bullish"
            )

            decision = make_decision(candles, None, config)

        assert decision.action == "buy"
        assert decision.confidence == 0.8
        assert decision.size_pct == 0.3
        assert decision.reasoning == "bullish"
        assert not decision.is_fallback
        assert decision.latency_ms >= 0

    def test_timeout_returns_fallback(self):
        """LLM timeout produces a hold fallback."""
        config = Config(timeout_seconds=1)

        with patch("llm_local.harness.OpenAI") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.chat.completions.create.side_effect = TimeoutError("timeout")

            decision = make_decision([_make_candle()], None, config)

        assert decision.action == "hold"
        assert decision.confidence == 0.0
        assert decision.is_fallback
        assert "LLM_ERROR" in decision.reasoning

    def test_connection_refused_returns_fallback(self):
        """LLM unreachable produces a hold fallback."""
        config = Config(timeout_seconds=1)

        with patch("llm_local.harness.OpenAI") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.chat.completions.create.side_effect = ConnectionError("refused")

            decision = make_decision([_make_candle()], None, config)

        assert decision.action == "hold"
        assert decision.is_fallback

    def test_invalid_json_returns_fallback(self):
        """Malformed JSON response produces a hold fallback."""
        config = Config(timeout_seconds=5)

        with patch("llm_local.harness.OpenAI") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "not json at all"
            mock_client.chat.completions.create.return_value = mock_response

            decision = make_decision([_make_candle()], None, config)

        assert decision.action == "hold"
        assert decision.is_fallback
        assert "JSON_PARSE_ERROR" in decision.reasoning

    def test_circuit_breaker_after_3_failures(self):
        """CircuitBreakerError raised after 3 consecutive failures."""
        config = Config(timeout_seconds=1)

        with pytest.raises(CircuitBreakerError) as exc_info:
            make_decision(
                [_make_candle()], None, config,
                _consecutive_failures=3,
            )

        assert exc_info.value.failures == 3

    def test_confidence_clamped(self, mock_llm_response):
        """Confidence values outside [0,1] are clamped."""
        config = Config(timeout_seconds=5)

        with patch("llm_local.harness.OpenAI") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            # Response with confidence > 1
            response_json = json.dumps({
                "action": "hold", "confidence": 5.0,
                "size_pct": 0.1, "reasoning": "test",
            })
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = response_json
            mock_client.chat.completions.create.return_value = mock_response

            decision = make_decision([_make_candle()], None, config)

        assert decision.confidence == 1.0

    def test_json_schema_defined(self):
        """DECISION_JSON_SCHEMA has the expected structure."""
        assert DECISION_JSON_SCHEMA["type"] == "object"
        props = DECISION_JSON_SCHEMA["properties"]
        assert "action" in props
        assert "confidence" in props
        assert "reasoning" in props
        assert set(props["action"]["enum"]) == {"buy", "sell", "hold"}

    def test_passes_position_to_prompt(self, mock_llm_response):
        """Position context is passed through to prompt building."""
        config = Config(timeout_seconds=5)
        position = {
            "entry_price": 64000.0, "current_price": 65000.0,
            "unrealized_pnl_pct": 0.015, "hold_candles": 5,
            "max_hold_candles": 19,
        }

        with patch("llm_local.harness.OpenAI") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_llm_response()

            decision = make_decision([_make_candle()], position, config)

        # Just verify it doesn't crash with position
        assert decision.action == "hold"


class TestValidateAction:
    def test_valid_actions(self):
        assert _validate_action("buy") == "buy"
        assert _validate_action("sell") == "sell"
        assert _validate_action("hold") == "hold"

    def test_case_insensitive(self):
        assert _validate_action("BUY") == "buy"
        assert _validate_action("Hold") == "hold"

    def test_invalid_defaults_to_hold(self):
        assert _validate_action("invalid") == "hold"
        assert _validate_action("") == "hold"


class TestClamp:
    def test_within_range(self):
        assert _clamp(0.5, 0.0, 1.0) == 0.5

    def test_below_min(self):
        assert _clamp(-1.0, 0.0, 1.0) == 0.0

    def test_above_max(self):
        assert _clamp(5.0, 0.0, 1.0) == 1.0

    def test_invalid_type(self):
        assert _clamp("not a number", 0.0, 1.0) == 0.0

    def test_none(self):
        assert _clamp(None, 0.0, 1.0) == 0.0


def _make_candle():
    return {
        "time": "2026-03-15 12:00",
        "open": 65000.0, "high": 65100.0,
        "low": 64900.0, "close": 65050.0,
        "volume": 1234.5,
        "sma_24": None, "sma_96": None, "rsi_14": None,
        "macd": None, "macd_signal": None,
        "bb_upper": None, "bb_lower": None,
    }
