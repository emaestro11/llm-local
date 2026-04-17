# Phase 1 — v2 Results

Date: 2026-04-17
Run: `run_id=4`, quick mode (200 candles), v2 prompt
Status: **Disciplined but edgeless. Strategy doesn't fit the regime.**

## v1 vs v2 Side-by-Side

| Metric | v1 (run #2) | v2 (run #4) | Verdict |
|--------|-------------|-------------|---------|
| Total trades | 11 | 5 | v2 halved trade count (default-hold bias working) |
| Win rate | 18.2% | 20.0% | Barely moved, sample too small |
| Avg return | −0.35% | −0.30% | Marginally less bad |
| Total return | −3.76% | −1.52% | Less bleeding, fewer trades |
| Sharpe | −46.94 | −24.69 | Still deeply negative |
| Max drawdown | 3.57% | 0.94% | Much better (less exposure) |
| Confidence r | −0.067 | N/A | 5 trades = insufficient data |
| Fallbacks | 5 (2.5%) | **0 (0.0%)** | Infrastructure fix successful |

## What Worked

1. **Infrastructure fix.** 0 fallbacks vs 5 in v1. `chat_template_kwargs.enable_thinking=False` in `harness.py` killed the empty-content problem. Replay went from ~50 min to ~12 min (13x faster per call).

2. **Default-hold bias.** v2 held 189/200 candles at avg confidence 0.22. v1 held 177/200 at avg confidence 0.68. v2's hold-confidence distribution is calibrated correctly — low confidence = hold = no trade.

3. **Anti-panic exits partially working.** One winner (+0.86% net, 6 candles): model held through noise, exited when RSI hit 70.8 overbought. Exactly the behavior the v2 prompt prescribes.

4. **Exits cite the v2 rules by name.** All 4 v2 losses exit with "thesis invalidated: price has broken back below the SMA96" or "sustained MACD signal flip." The model is following the written rules, not freelancing.

## What Didn't Work

1. **Strategy doesn't fit the regime.** The last 50 hours are CHOPPY:
   - +1.00% drift over 50 hours (flat)
   - Per-candle volatility 0.215% (less than half the 0.5% minimum-move bar v2 requires)
   - **15 SMA96 crosses** in 200 candles — price oscillates around the long-term MA
   - v2's entry rule ("price reclaims SMA96 + MACD + volume") fires on chop. Most reclaims are false breakouts in this regime.

2. **Entry-timing problem, not discipline problem.** v2 is picking statistically sound setups ("SMA96 reclaim with volume + MACD crossover"), but in a chop regime these setups have negative expectancy. Would work in a trending market.

3. **Sample size too small to judge confidence calibration.** Only 5 trades. Pearson r undefined. We need a longer slice or a less restrictive prompt to measure this.

## The Honest Read

v2 proved the prompt mechanism works — the LLM reads and follows structured discipline rules. The remaining problem isn't prompt quality; it's strategy-market fit.

The Phase 1 success criteria (win rate >52%, confidence r >0.1) are not met. But that doesn't mean "signal is noise." It means **this specific strategy + this specific 50-hour window + this model produced no edge**. Three variables, one data point.

## Paths Forward

Pick one:

### A) Test v2 on a trending slice (fastest, 15 min)
Run a replay starting from an earlier candle range where BTC had a clear directional move. If v2's breakout rules produce edge in a trending regime, we've found strategy-regime fit and just need to add regime detection. If v2 still fails in trends, the strategy is wrong.

```bash
# Modify config.toml quick_candles to 200, but also need replay.py to support
# a --start-candle offset. Currently --quick = last 200.
```

### B) Write v3 with regime-adaptive rules (longer, ~1h + 12 min replay)
v3 system prompt detects regime (trending vs chop) via ADX or SMA slope, then applies different entry rules. Trending → breakout. Chop → mean-reversion (buy BB-lower, sell BB-upper).

### C) Run full 2,880-candle replay (no code change, ~3h at 12s/call)
Chop windows will average out over 30 days. Full replay gives a real sample size for confidence calibration. Burns time but proves the point definitively.

### D) `/office-hours` to reconsider fundamentals
Is 15-min the right timeframe? Is Gemma4 the right model? Is LLM-as-trader the right framing at all, or should the LLM only pick setups and let a deterministic rule execute? This is the hardest-to-reverse decision — worth questioning.

## Recommendation

**A + C in parallel.** A proves strategy-regime hypothesis in ~15 min. C produces a statistically real sample. If A shows edge in trends and C shows ~50/50 across regimes → regime detection is the fix. If both show no edge → strategy is wrong and we need B or D.

## Files Changed This Round

- `llm_local/prompts.py` — added `_build_v2()` with 5 discipline rules
- `llm_local/harness.py` — disabled Gemma4 reasoning via `chat_template_kwargs`, added EMPTY_RESPONSE fallback
- `llm_local/__main__.py` — fixed SQLAlchemy DetachedInstanceError in `analyze --compare`
- `llm_local/dashboard.py` — renamed to "LLM Local Trader"
- `tests/test_prompts.py` — 8 new tests locking in v2 discipline rules
- `tests/test_harness.py` — 3 new tests (thinking disabled, empty content, None content)

Test suite: 103/103 passing.
