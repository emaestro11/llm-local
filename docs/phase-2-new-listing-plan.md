# Phase 2 Plan — New-Listing Scalper (Deterministic + LLM Qualifier)

Date: 2026-04-21 (updated 2026-05-08 after `/plan-eng-review`)
Status: **ENG CLEARED — ready to execute pre-code spikes (Phase 0), then implement.**
Full design doc: `~/.gstack/projects/emaestro11-llm-local/esteban-main-design-20260421-221849.md`
Test plan: `~/.gstack/projects/emaestro11-llm-local/esteban-main-eng-review-test-plan-20260508-162207.md`

## Decision from /office-hours (2026-04-21)

After two failed BTC 15m replay runs in Phase 1 (v1: 18.2% WR, v2: 20.0% WR edgeless), pivoted from "polish another prompt" to "find a market regime with a documented, mechanism-backed edge."

Chose path D (question the fundamentals) from `docs/phase-1-v2-results.md`. Ran YC-style forcing questions:
1. **Edge hypothesis?** User answer: "honestly, I don't know yet." (Best honest answer; most claimed edges are imaginary.)
2. **Evidence vs hope?** Ruled out timeframe-tweaking and self-improving-agent as "change a knob and hope." Picked the one with a real mechanism.
3. **Verify @crypto_scalper8 strategy?** Tried. Handle doesn't index in any top-trader list, X requires auth for profile content. Pivoted — don't copy a strategy you can't articulate from a trader you can't verify.
4. **New-listing scalping?** Picked. Landscape search (2025–2026) confirms pattern is live: 70% of Binance listings dump post-launch. **Edge is arbitrage-resistant because the dumpers ARE the smart money** (VC exit liquidity) — they have no incentive to compete it away.
5. **Kill criterion?** 30 listings, short WR > 55%, Pearson r > 0.1 on qualifier confidence. If not met, pivot to funding-rate arbitrage or resurrect the full 2,880-candle BTC replay.

## The Edge (written down specifically)

> In the first 60 minutes after a Binance spot listing, there is a predictable price pattern — initial FOMO buying pushes price 30–200% above the listing price within 15–30 minutes, followed by a 40–60% retrace in the next hour. An LLM that reads the first 15 minutes of price/volume action and classifies "reversal starting" vs "continuation" should produce above-random short signals at the 15-minute mark.

## Chosen Approach: C — Deterministic Shorts + LLM Qualifier

Split execution (deterministic code) from classification (LLM).

- **LLM job:** binary qualifier before entry. Output `{qualify, confidence, reasoning}`.
- **Deterministic entry:** minute 15 of listing, if `current/listing > 1.30` AND volume declining (`sum(vol[10:15]) < sum(vol[5:10])`), AND LLM qualified with confidence > 0.6 → short fixed-notional ($5 paper/live, fractional 0.5% for backtest math).
- **Deterministic exit (priority order, locked):** TP `current < entry * (1 - target_drop_pct)` (calibrated from The Assignment) > adverse stop (calibrated from observed 5m candle range, NOT flat 10%) > time stop at minute 75. Slippage 0.3% per side in PnL.
- Grounded in v2's lesson: LLMs can follow rules, are mediocre at execution. Use them for what they're good at (pattern classification) and hand execution to code.

## Adversarial Review Results (2026-04-21) — RESOLVED via `/plan-eng-review` (2026-05-08)

Original adversarial review: 5/10, NEEDS_FIX, 19 findings (5 must-fix + 11 should-fix). All 5 must-fix items now have locked resolutions.

### Must-fix resolutions

| # | Issue | Locked decision |
|---|---|---|
| 1 | Schema reuse overstated (Decision long-only) | **1A:** new `ListingTrade` ORM table parallel to `Decision`; long-only Phase 1 untouched |
| 1 | Harness JSON schema hardcoded | **2A:** new `qualify_setup()` function alongside `make_decision`, sharing private `_call_llm()` helper |
| 2 | 1m history depth unverified | **4A:** Phase 0 ccxt spike on 5 recent listings — BLOCKING gate before Lane A |
| 3 | Listings source hand-waved | **4A:** Phase 0 source spike (15 min) — BLOCKING gate before Lane A |
| 4 | Exit-rule peak-vs-entry asymmetry | **3A:** entry-relative exit — `current < entry * (1 - target_drop_pct)`, calibrated from The Assignment |
| 5 | Futures-vs-spot timing gap | **7A:** Phase 0 timing verification on last 20 listings; live may scope to perp-launches or margin shorts |

### Architecture decisions locked (`/plan-eng-review` 2026-05-08)

| ID | Decision | Why |
|---|---|---|
| **1A** | New `ListingTrade` table | Parallel to `Decision`; preserves long-only Phase 1; 0 regression risk to 103 tests |
| **2A** | New `qualify_setup()` function | Explicit > clever; doesn't break `make_decision`; testable in isolation |
| **3A** | Entry-relative exit math | PnL matches what the rule says |
| **4A** | Phase 0 spikes BLOCKING | 30 min of verification beats 2 days of code on a bad assumption |
| **5A** | Raw OHLCV + derived features in qualifier prompt | NO RSI/MACD/BB on listing data — insufficient warmup makes them noise |
| **6A** | Adverse stop calibrated from The Assignment | 10% flat fires on legitimate first-hour vol; widen using observed 5m candle range |
| **7A** | Live execution scoped after timing verification | Backtest on spot; live may be perp or margin |

### Code Quality fixes (mechanical, no decisions needed)

- Listings analysis skips Sharpe (N=30 meaningless); reports WR + Pearson r + avg return
- New `ListingPosition` class is short-aware from the start; `SimulatedPosition` untouched
- Backtest fractional sizing; paper/live fixed $5 notional, ≤20 trades per $100 account
- Slippage 0.3% per side flat in listings PnL
- `should_exit` rule priority: TP > adverse-stop > time-stop (whipsaw tiebreaker)

### Should-fix items folded into locked decisions

#1 entry trigger ranges, #2 peak_price formalization, #3/#4 kill-criterion denominator + Pearson sample-size note, #5 slippage, #10 listing price = close of minute 1, #11 BTC regime = close vs SMA(50d), #12 module rename to `listings_replay.py`, #13 Assignment as blocking gate, #17 adverse stop calibration, #19 circuit-breaker reset per listing — all captured in the test plan or the locked decisions above.

## Immediate Next Steps (Phase 0 — BLOCKING gates)

All four must pass before Lane A. ~75 min total.

1. **The Assignment** (~30 min, manual):
   - 5 recent Binance listings on TradingView 1m, first 4 hours each.
   - Record: listing price, peak, time of peak, was there a 15–30 min reversal, what would a short have paid (%), what's the typical 5m candle range.
   - **Pattern visible on <3 of 5 → KILL the direction.**
   - **Pattern visible on ≥3 of 5 → derive `target_drop_pct` and adverse-stop range; feed into design doc.**

2. **ccxt 1m history depth spike** (~10 min):
   - `ccxt.fetch_ohlcv(symbol, '1m', since=listing_ts, limit=240)` against 5 recent listings.
   - Any return <240 candles or zeros → switch to 5m, document.

3. **Listings registry source spike** (~15 min):
   - Vet a community registry OR Binance announcements API endpoint.
   - Verify JSON format, license, freshness.
   - No code commits until source is decided.

4. **Perp-listing timing spike** (~10 min):
   - Last 20 spot listings: how many had perps within first hour?
   - <50% → live strategy targets perp-launches OR Binance Margin shorts; document.

If all four pass: proceed to Lane A. Lanes B and C run in parallel after A. Lane D waits for both.

## If the Experiment Fails

Kill criterion: 30 qualified trades, WR < 55% → direction is dead.

Fallback options, in order of preference:
1. **Funding-rate arbitrage** — different non-directional edge on Binance perps. Short perps + long spot when funding > 0.05%, collect funding. Low variance, actual payoff, less sensitive to directional prediction.
2. **Full 2,880-candle BTC 15m replay** — salvage the Phase 1 continuous-timeline investment. Chop averages out over 30 days; gives statistically real confidence calibration.
3. **/office-hours again** — question more fundamentals (model choice? LLM-in-loop at all?).
