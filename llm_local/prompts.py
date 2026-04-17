"""Prompt templates for LLM trading decisions.

Each version is a named function that builds the full prompt string.
The replay_runs table records which version was used for reproducibility.
"""

from datetime import datetime, timezone

AVAILABLE_VERSIONS = ["v1", "v2"]


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
        "v2": _build_v2,
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

    user_prompt = _build_user_prompt(candles, position)
    return system_prompt, user_prompt


def _build_v2(
    candles: list[dict],
    position: dict | None,
) -> tuple[str, str]:
    """V2: Discipline-first prompt targeting run #2's overtrading failure mode.

    Changes vs v1 (per docs/phase-1-findings.md):
      1. Fee awareness: explicit 0.2% round-trip cost, 0.5%+ expected-move bar
      2. Asymmetric entry: require 2+ indicators + long-horizon (SMA96) alignment
      3. Anti-panic exit: no selling on 1-2 red candles; thesis-invalidation only
      4. Calibrated confidence: concrete anchors at 0.3 / 0.5 / 0.7 / 0.9
      5. Default-hold bias: HOLD is the null action, trades require conviction
    """
    system_prompt = (
        "You are a disciplined BTC/USDT swing trader working on 15-minute candles. "
        "Your edge is patience, not reaction speed. Most candles are noise.\n\n"
        "Output JSON with these fields:\n"
        '- "action": "buy", "sell", or "hold"\n'
        '- "size_pct": capital fraction 0.0-1.0 (buy only; else 0)\n'
        '- "confidence": 0.0-1.0, calibrated (see scale below)\n'
        '- "reasoning": 1-3 sentences naming the SPECIFIC signals driving the call\n\n'
        "HARD RULES:\n"
        "1. FEES: Every round trip costs 0.2% (0.1% in + 0.1% out). Do not enter "
        "unless you expect at least 0.5% move in your favor. A +0.3% gross move is "
        "a loss after fees.\n"
        "2. DEFAULT IS HOLD: Holding is free. Trading is expensive. When in doubt, "
        "hold. Most candles should produce 'hold'.\n"
        "3. ENTRY BAR (buy requires ALL of these):\n"
        "   a. Price above SMA96 (long-horizon uptrend) OR clear reversal with "
        "volume confirmation\n"
        "   b. Two or more of {MACD crossover, RSI recovering from <35, BB lower "
        "touch + bounce, volume spike > 1.5x recent avg} align\n"
        "   c. Expected move to next resistance is 0.5% or greater\n"
        "4. EXIT BAR (sell ONLY if position open AND one of these):\n"
        "   a. Thesis invalidated: price breaks back below SMA96 on volume\n"
        "   b. MACD sign flip SUSTAINED for 3+ candles (not one red candle)\n"
        "   c. RSI > 75 (overbought, take profit)\n"
        "   d. Position up 0.8%+ and momentum clearly fading over 3+ candles\n"
        "   DO NOT SELL because of 1-2 red candles. That is noise on 15m.\n"
        "5. CONFIDENCE CALIBRATION:\n"
        "   0.3 = weak, barely better than coin flip. Default for marginal setups.\n"
        "   0.5 = one clear signal, rest ambiguous.\n"
        "   0.7 = 3+ independent signals aligned, structure supports the trade.\n"
        "   0.9 = textbook setup, every indicator agrees, clear target and stop.\n"
        "   If conf < 0.5, action should almost always be 'hold'.\n\n"
        "ANTI-PATTERNS (common LLM mistakes to avoid):\n"
        "- Chasing a single green candle as 'bullish momentum'.\n"
        "- Selling the first red candle after entry (panic exit).\n"
        "- High confidence on exits and low confidence on entries (inverted: "
        "good traders are more sure WHY they enter than WHEN they exit).\n"
        "- Treating every MACD wiggle as a crossover."
    )

    user_prompt = _build_user_prompt(candles, position)
    return system_prompt, user_prompt


def _build_user_prompt(candles: list[dict], position: dict | None) -> str:
    """Format candle data + position context into the user-turn prompt.

    Shared across all prompt versions; only system prompts differ.
    """
    candle_lines = []
    for c in candles:
        line = (
            f"  Time: {c['time']} | "
            f"O: {c['open']:.2f} H: {c['high']:.2f} "
            f"L: {c['low']:.2f} C: {c['close']:.2f} | "
            f"Vol: {c['volume']:.0f}"
        )
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

    return (
        f"Analyze the following BTC/USDT 15-minute candle data and make a trading decision.\n\n"
        f"Recent Candles (oldest to newest):\n{candle_text}\n"
        f"{position_text}\n\n"
        f"What is your trading decision?"
    )


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
