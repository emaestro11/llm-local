# Phase 2 Plan — New-Listing Scalper (Deterministic + LLM Qualifier)

Date: 2026-04-21
Status: **DRAFT — needs revision after adversarial review (see Open Blockers below)**
Full design doc: `~/.gstack/projects/emaestro11-llm-local/esteban-main-design-20260421-221849.md`

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
- **Deterministic entry:** minute 15 of listing, if `current/listing > 1.30` AND volume declining, AND LLM qualified with confidence > 0.6 → short 0.5% of account.
- **Deterministic exit:** 40% retrace target OR minute 75 time stop OR 10% adverse stop.
- Grounded in v2's lesson: LLMs can follow rules, are mediocre at execution. Use them for what they're good at (pattern classification) and hand execution to code.

## Adversarial Review Results (2026-04-21)

Design doc was reviewed cold by an independent subagent that also cross-checked the repo code. **Quality score 5/10. Verdict: NEEDS_FIX.**

### Must-fix blockers before implementation

1. **"Reuse existing code" is overstated.**
   - `Decision` schema has no `side`, `symbol`, or `listing_id`.
   - `analysis._reconstruct_trades` pairs `buy→sell` only; drops short trades on the floor.
   - `harness.DECISION_JSON_SCHEMA` hardcoded to `{action,size_pct,confidence,reasoning}` with enum `buy/sell/hold` — not compatible with qualifier schema.
   - **Action:** either add side-aware columns + refactor `analysis.py` + parameterize the harness schema, OR create new `ListingTrade` model + new `qualify_setup()` function. Stop claiming "reuse as-is."

2. **Binance 1m history depth is a go/no-go blocker.**
   - Binance public klines has a rolling ~30 day floor for 1m bars on many symbols.
   - **Action:** before any code, spike `ccxt.fetch_ohlcv(symbol, '1m', since=listing_ts, limit=240)` against 5 recent listings. If it fails, switch to 5m.

3. **Listings registry source is hand-waved.**
   - "Community-maintained JSON list" unverified; Binance announcement HTML format changes.
   - **Action:** 15-min spike to verify a listings source before committing.

4. **Exit rule 1 math is wrong.**
   - "40% retrace from peak" is asymmetric: if peak occurred before minute-15 entry, price has to fall 40% from peak but short only profits on drop from entry.
   - **Action:** rewrite in terms of entry price (e.g., close at `entry * 0.80`) OR explicitly compute expected PnL given peak-to-entry gap from historical data.

5. **Futures-vs-spot listing timing gap.**
   - New spot listings rarely have perpetual futures available on day 1 (hours to weeks later).
   - **Action:** verify futures listing timing for last 10 spot listings. If typical gap > 2h, live strategy needs reframing (backtest on spot is still fine).

### Should-fix (tightening, can be done inline during implementation)

- Entry trigger ambiguity — define exact minute-index ranges.
- `peak_price` formal definition.
- Kill criterion denominator — 30 qualified trades vs 30 listings sampled.
- Pearson r > 0.1 on ~21 trades is statistically meaningless (state as descriptive-only or raise sample).
- Add slippage model (~0.3% per side flat).
- Define "listing price" unambiguously (close of minute 1).
- BTC daily trend computation unspecified.
- Rename `event_replay.py` → `listings_replay.py` (YAGNI on the generalization).
- Make The Assignment (manual 5-listing inspection) a BLOCKING gate before Lane A.
- 10% adverse stop too tight for first-hour listing vol (ATR-based or widen).
- Reset circuit breaker between listings in the event loop.

## Immediate Next Steps

Before writing any code:

1. **Spike blockers #2 and #3** in parallel (~30 min total):
   - `ccxt.fetch_ohlcv` for 5 recent Binance listings at 1m — does history go back to listing_ts?
   - Find a listings source: check `github.com/search?q=binance+listing+alert`, verify JSON format, license. Or parse Binance announcements API.
2. **Do The Assignment manually** (~30 min):
   - Pull TradingView charts for 5 recent Binance listings (1m, first 4 hours).
   - For each: listing price, peak, time of peak, did it reverse at the 15–30 min mark, what would a short have paid.
   - If pattern isn't visibly present on ≥3 of 5, **kill this direction before coding**.
3. **Revise the design doc to address must-fix items #1, #4, #5.** Rewrite "reuse" claims honestly; fix exit-rule math; reframe live path given futures-vs-spot gap.
4. **Only then:** proceed to Lane A (data) → Lane B (strategy + qualifier) → Lane C (listings_replay.py).

## If the Experiment Fails

Kill criterion: 30 qualified trades, WR < 55% → direction is dead.

Fallback options, in order of preference:
1. **Funding-rate arbitrage** — different non-directional edge on Binance perps. Short perps + long spot when funding > 0.05%, collect funding. Low variance, actual payoff, less sensitive to directional prediction.
2. **Full 2,880-candle BTC 15m replay** — salvage the Phase 1 continuous-timeline investment. Chop averages out over 30 days; gives statistically real confidence calibration.
3. **/office-hours again** — question more fundamentals (model choice? LLM-in-loop at all?).
