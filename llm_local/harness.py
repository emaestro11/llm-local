"""Decision harness: pure function that calls the LLM and returns a trading decision.

No side effects. No database access. No exchange calls.
The caller (replay engine, signal writer, live trader) handles I/O.
"""

import json
import logging
import time
from dataclasses import dataclass

from openai import OpenAI

from llm_local.config import Config
from llm_local.prompts import build_prompt

logger = logging.getLogger(__name__)

# JSON schema for grammar-constrained generation via llama.cpp
DECISION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["buy", "sell", "hold"],
        },
        "size_pct": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "reasoning": {
            "type": "string",
        },
    },
    "required": ["action", "confidence", "reasoning"],
}


@dataclass
class TradeDecision:
    """Result of an LLM trading decision."""

    action: str  # "buy", "sell", "hold"
    size_pct: float  # 0.0 to 1.0, only meaningful for buy
    confidence: float  # 0.0 to 1.0
    reasoning: str
    raw_response: str = ""
    latency_ms: int = 0
    is_fallback: bool = False


class CircuitBreakerError(Exception):
    """Raised after too many consecutive LLM failures."""

    def __init__(self, failures: int):
        self.failures = failures
        super().__init__(
            f"Circuit breaker tripped after {failures} consecutive LLM failures"
        )


def make_decision(
    candles: list[dict],
    position: dict | None,
    config: Config,
    prompt_version: str = "v1",
    _consecutive_failures: int = 0,
) -> TradeDecision:
    """Call the LLM and return a trading decision.

    This is a pure function with no side effects. It:
    1. Builds a prompt from candle data + position context
    2. Calls llama.cpp server with grammar-constrained JSON output
    3. Parses and returns the decision

    On timeout or error, returns a hold decision with is_fallback=True.
    After 3 consecutive failures, raises CircuitBreakerError.
    """
    if _consecutive_failures >= 3:
        raise CircuitBreakerError(_consecutive_failures)

    system_prompt, user_prompt = build_prompt(candles, position, prompt_version)

    client = OpenAI(
        base_url=config.llm_url,
        api_key="not-needed",
        timeout=config.timeout_seconds,
    )

    start_time = time.monotonic()

    try:
        response = client.chat.completions.create(
            model=config.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            extra_body={"json_schema": DECISION_JSON_SCHEMA},
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        raw_text = response.choices[0].message.content.strip()

        logger.debug(f"LLM response ({elapsed_ms}ms): {raw_text}")

        parsed = json.loads(raw_text)
        decision = TradeDecision(
            action=_validate_action(parsed.get("action", "hold")),
            size_pct=_clamp(parsed.get("size_pct", 0.0), 0.0, 1.0),
            confidence=_clamp(parsed.get("confidence", 0.0), 0.0, 1.0),
            reasoning=parsed.get("reasoning", ""),
            raw_response=raw_text,
            latency_ms=elapsed_ms,
            is_fallback=False,
        )

        return decision

    except json.JSONDecodeError as e:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        logger.warning(f"LLM returned invalid JSON ({elapsed_ms}ms): {e}")
        return _fallback_decision(
            f"JSON_PARSE_ERROR: {e}", elapsed_ms, raw_text if "raw_text" in dir() else ""
        )

    except Exception as e:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        logger.warning(f"LLM call failed ({elapsed_ms}ms): {type(e).__name__}: {e}")
        return _fallback_decision(f"LLM_ERROR: {type(e).__name__}: {e}", elapsed_ms)


def _fallback_decision(reason: str, latency_ms: int, raw: str = "") -> TradeDecision:
    """Create a safe hold decision when the LLM fails."""
    return TradeDecision(
        action="hold",
        size_pct=0.0,
        confidence=0.0,
        reasoning=reason,
        raw_response=raw,
        latency_ms=latency_ms,
        is_fallback=True,
    )


def _validate_action(action: str) -> str:
    """Ensure action is one of the valid values."""
    action = action.lower().strip()
    if action in ("buy", "sell", "hold"):
        return action
    return "hold"


def _clamp(value, min_val: float, max_val: float) -> float:
    """Clamp a numeric value to a range."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return min_val
    return max(min_val, min(max_val, v))
