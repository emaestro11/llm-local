# Phase 1 Findings — Run #2 Investigation

Date: 2026-04-16
Run: `run_id=2`, quick mode (200 candles), v1 prompt
Status: **Signal is noise. Root cause found. v2 prompt not yet written.**

## Headline Numbers

| Metric | Value | Target | Verdict |
|--------|-------|--------|---------|
| Win rate | 18.2% (2/11) | >52% | FAIL |
| Total return | −3.76% | >0 | FAIL |
| Avg return per trade | −0.35% | >0.2% (fee) | FAIL |
| Pearson r (confidence vs return) | −0.067 | >0.1 | FAIL (inverted) |
| Fallback rate | 2.5% (5/200) | <5% | PASS |

Conclusion: Phase 1 v1 prompt does not produce usable signal. Confidence is anti-correlated with outcome — the model is *more* confident when it's wrong.

## Root Cause

**Gemma4 overtrades. It picks reasonable entries, then panic-sells on noise.**

Same indicators, same reasoning vocabulary, opposite actions 1-9 candles apart.

### Evidence

1. **Loss distribution.** 9 losses cluster at −0.4% to −0.8%. Round-trip fee is 0.2%. Model isn't losing on big moves — it's churning through noise just above the fee floor.

2. **Loser hold times.** 1, 2, 2, 4, 7, 7, 7, 9, 19 candles. Loss #4 held 1 candle. Loss #5 held 2 candles. That's flinching, not trading.

3. **Winner hold times.** 15 and 20 candles. Same entry setup as losers, but held through the noise. Thesis played out.

4. **Exit confidence > entry confidence.** 0.72 avg exit vs 0.62 avg entry. Model is *more certain* when panic-exiting than when entering. Backwards for a profitable trader.

5. **Reasoning symmetry.** Entry text: "MACD crossover, RSI rising, bullish momentum." Exit text: "MACD losing momentum, RSI dropping, bearish breakdown." Treats every candle's direction as new information.

### Example Losing Trade (Loss #4, hold=1c, −0.83%)

- **Entry** at 74429.99: "Price has recovered strongly from the 13:30 dip, breaking above the SMA96 with rising volume. Bullish MACD and RSI indicate a momentum shift."
- **Exit** at 73962.75 (one candle later): "The price experienced a sharp rejection at the 74500 level, followed by a high-volume drop that broke below the SMA24."

One candle. Model's entire thesis reversed in 15 minutes based on one red candle.

## Root Cause Hypothesis

The v1 prompt (`llm_local/prompts.py:43-57`) has:
- No fee awareness (model doesn't know it needs >0.2% to break even)
- No noise threshold (treats every MACD wiggle as signal)
- No anti-panic exit rule (no "hold through 1-2 red candles")
- No time-horizon guidance (scalp vs swing ambiguity)
- Underspecified confidence semantics ("higher = stronger alignment" — vague)

The model is competent at pattern-matching indicators. It just lacks the *discipline framework* a prompt can provide.

## Proposed v2 Prompt Changes

| # | Change | Rationale |
|---|--------|-----------|
| 1 | Inject fees: "Round-trip cost is 0.2%. You need 0.5%+ expected move to justify a trade." | Makes the model fee-aware. Raises the bar for entries. |
| 2 | Asymmetric entry bar: "Buy only when long-horizon structure (SMA96) agrees with short-horizon signal (MACD cross + volume). 2+ indicators must align." | Prevents chasing single-indicator bounces. |
| 3 | Anti-panic exit: "Do not sell on 1-2 red candles. Only exit on thesis invalidation: break of SMA96 OR MACD sign flip sustained 3+ candles OR stop-loss level hit." | Targets the main failure mode. |
| 4 | Confidence calibration: "0.3 = barely better than flip. 0.5 = one signal. 0.7 = 3+ signals aligned. 0.9 = textbook setup with all indicators agreeing." | Makes confidence mean something measurable. |
| 5 | Default-hold bias: "Default action is HOLD. Entry and exit both require positive conviction, not absence of red flags." | Reduces trade count, raises per-trade quality. |

## Next Actions

When picking this up:

1. **Write `prompts.py::_build_v2()`** implementing the 5 changes above. Update `AVAILABLE_VERSIONS = ["v1", "v2"]`.
2. **Add test** in `tests/test_prompts.py` covering the v2 builder.
3. **Run quick replay with v2**: `uv run python -m llm_local replay --quick --prompt-version v2` (~45 min on current hardware).
4. **Compare**: `uv run python -m llm_local analyze --compare` to see v1 vs v2 side by side.
5. **Pass criteria**: win rate >40% AND Pearson r >0.1. Stretch: >52% and >0.2.
6. **If v2 also fails**: `/office-hours` to reconsider — longer timeframe (1h candles), different model (Qwen3.5), or reframe as classification instead of open reasoning.

## Open Questions for Next Session

- Does Gemma4 actually have the capacity to trade, or is the 15-min timeframe fundamentally too noisy for any LLM?
- Would a longer timeframe (1h or 4h) make the signal-to-noise ratio tractable?
- Is "open reasoning" the wrong framing? Should we ask the model to rank pre-defined setups rather than freeform analyze?

## Useful Commands

```bash
# Re-inspect run #2 decisions
uv run python -c "
from llm_local.config import load_config
from llm_local.models import init_db, get_session, Decision, Candle
from sqlalchemy import select
config = load_config('config.toml')
engine = init_db(config.db_path)
with get_session(engine) as s:
    rows = s.execute(
        select(Decision, Candle).join(Candle, Decision.candle_id == Candle.id)
        .where(Decision.run_id == 2).order_by(Candle.timestamp_ms)
    ).all()
    for d, c in rows:
        if d.action != 'hold':
            print(f'{d.action} @ {c.close:.0f}  conf={d.confidence:.2f}  {d.reasoning[:100]}')
"

# Dashboard (if still wanting to view decisions visually)
uv run python -m llm_local.dashboard  # port 8090
```
