"""Prompt templates for LLM trading decisions.

Each version is a named function that builds the full prompt string.
The replay_runs table records which version was used for reproducibility.
"""

from datetime import datetime, timezone

AVAILABLE_VERSIONS = ["v1"]


def build_prompt(
    candles: list[dict],
    position: dict | None,
    version: str = "v1",
) -> tuple[str, str]:
    """Build system + user prompt for the given version.

    Returns (system_prompt, user_prompt).
    """
    builders = {
        "v1": _build_v1,
    }

    if version not in builders:
        raise ValueError(
            f"Unknown prompt version '{version}'. "
            f"Available: {AVAILABLE_VERSIONS}"
        )

    return builders[version](candles, position)


def _build_v1(
    candles: list[dict],
    position: dict | None,
) -> tuple[str, str]:
    """V1: Full TA context with structured reasoning.

    Includes: last N candles with OHLCV + all indicators,
    position context, and explicit reasoning instructions.
    """
    system_prompt = (
        "You are a cryptocurrency trading analyst. You analyze market data and "
        "technical indicators to make trading decisions for BTC/USDT.\n\n"
        "You must respond with a JSON object containing:\n"
        '- "action": one of "buy", "sell", or "hold"\n'
        '- "size_pct": percentage of capital to use (0.0 to 1.0), only for buy actions\n'
        '- "confidence": your confidence in this decision (0.0 to 1.0)\n'
        '- "reasoning": brief explanation of your analysis (1-3 sentences)\n\n'
        "Guidelines:\n"
        "- Consider trend direction, momentum, volatility, and volume\n"
        "- Higher confidence should mean stronger signal alignment\n"
        "- Prefer hold when signals are mixed or unclear\n"
        "- Only sell if you have an open position\n"
        "- Consider risk/reward ratio in your sizing"
    )

    # Format candle data
    candle_lines = []
    for c in candles:
        line = (
            f"  Time: {c['time']} | "
            f"O: {c['open']:.2f} H: {c['high']:.2f} "
            f"L: {c['low']:.2f} C: {c['close']:.2f} | "
            f"Vol: {c['volume']:.0f}"
        )
        # Add indicators if available
        indicators = []
        if c.get("rsi_14") is not None:
            indicators.append(f"RSI: {c['rsi_14']:.1f}")
        if c.get("sma_24") is not None:
            indicators.append(f"SMA24: {c['sma_24']:.2f}")
        if c.get("sma_96") is not None:
            indicators.append(f"SMA96: {c['sma_96']:.2f}")
        if c.get("macd") is not None:
            indicators.append(f"MACD: {c['macd']:.2f}")
        if c.get("macd_signal") is not None:
            indicators.append(f"Signal: {c['macd_signal']:.2f}")
        if c.get("bb_upper") is not None and c.get("bb_lower") is not None:
            indicators.append(f"BB: [{c['bb_lower']:.2f}, {c['bb_upper']:.2f}]")

        if indicators:
            line += " | " + " ".join(indicators)
        candle_lines.append(line)

    candle_text = "\n".join(candle_lines)

    # Format position context
    if position is not None:
        position_text = (
            f"\nCurrent Position:\n"
            f"  Side: LONG\n"
            f"  Entry Price: {position['entry_price']:.2f}\n"
            f"  Current Price: {position['current_price']:.2f}\n"
            f"  Unrealized P&L: {position['unrealized_pnl_pct']:.2%}\n"
            f"  Hold Duration: {position['hold_candles']} candles "
            f"({position['hold_candles'] * 15} minutes)\n"
            f"  Max Hold: {position['max_hold_candles']} candles remaining before auto-close"
        )
    else:
        position_text = "\nCurrent Position: NONE (flat)"

    user_prompt = (
        f"Analyze the following BTC/USDT 15-minute candle data and make a trading decision.\n\n"
        f"Recent Candles (oldest to newest):\n{candle_text}\n"
        f"{position_text}\n\n"
        f"What is your trading decision?"
    )

    return system_prompt, user_prompt


def candle_to_prompt_dict(candle, timestamp_fmt: str = "%Y-%m-%d %H:%M") -> dict:
    """Convert a Candle ORM object to a dict suitable for prompt building."""
    ts = datetime.fromtimestamp(candle.timestamp_ms / 1000, tz=timezone.utc)
    return {
        "time": ts.strftime(timestamp_fmt),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "sma_24": candle.sma_24,
        "sma_96": candle.sma_96,
        "rsi_14": candle.rsi_14,
        "macd": candle.macd,
        "macd_signal": candle.macd_signal,
        "bb_upper": candle.bb_upper,
        "bb_lower": candle.bb_lower,
    }
