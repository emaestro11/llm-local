# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Local ML-based crypto trading system. Uses local LLMs (Gemma4-26B, Qwen3.5) via llama.cpp for trade execution and monitoring, with Claude Opus for research and strategy planning. Trades on Binance (paper trading first, live later).

## Development Commands

```bash
uv run python -m llm_local fetch                  # Fetch & cache 30 days of BTC/USDT candles + compute indicators
uv run python -m llm_local replay --quick          # Quick replay (200 candles, ~30 min)
uv run python -m llm_local replay                  # Full replay (2,880 candles, ~8-12 hours)
uv run python -m llm_local replay --prompt-version v1  # Specify prompt version
uv run python -m llm_local analyze                 # Analyze latest completed run
uv run python -m llm_local analyze --compare       # Compare all runs side by side
uv run python -m pytest tests/ -v                  # Run tests (92 tests)
uv add <package>                                   # Add a dependency
uv sync                                            # Install/sync all dependencies
```

## Local LLM Setup

The project uses llama.cpp's server mode to expose an OpenAI-compatible API at `http://localhost:8080/v1`:

```bash
~/Documents/repos/llama.cpp/build/bin/llama-server \
    -m ~/models/gemma4-26b/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf \
    -c 65536 -ngl 999 \
    -ctk q8_0 -ctv q8_0 \
    --host 0.0.0.0 --port 8080
```

Connect from Python using the `openai` client pointed at `http://localhost:8080/v1`.

## Architecture

Two-tier model approach:

- **Local LLM (Gemma4/Qwen)** — fast, free, handles trade execution decisions, market monitoring, and routine analysis
- **Claude Opus (API)** — deep reasoning for strategy research, performance review, and architecture planning

### Phase 1 Module Structure (current)

```
llm_local/
├── __init__.py        # Package init
├── __main__.py        # CLI entry point (fetch/replay/analyze)
├── config.py          # TOML config loading + Config dataclass
├── models.py          # SQLAlchemy models: Candle, Decision, ReplayRun
├── data.py            # Binance OHLCV fetch (cached) + pandas-ta indicators
├── prompts.py         # Named prompt templates (v1+) with version tracking
├── harness.py         # make_decision() pure function, grammar-constrained JSON via llama.cpp
├── replay.py          # Historical replay loop, position tracking, checkpoint/resume
└── analysis.py        # Win rate, Sharpe, drawdown, confidence calibration
tests/                 # 92 tests covering all modules
config.toml            # Trading configuration (pair, timeframe, LLM params, fees)
```

Key design decisions:
- **Grammar-constrained JSON** via `extra_body={"json_schema": ...}` on llama.cpp's OpenAI-compatible API
- **Pure function harness** — no side effects, caller validates action legality
- **15-minute candles**, 24-candle lookback, 24-candle max hold cap
- **Checkpoint/resume** for multi-hour replays, progress bar every 100 candles
- **Quick mode** (--quick) for 200-candle prompt iteration runs

Storage layers:

- **SQLite** — candles (cached), decisions, replay_runs. Outcomes computed at analysis time.
- **MemPalace** — persistent conversational memory and context across sessions (ChromaDB + knowledge graph)

Exchange integration via `ccxt` (Binance testnet for paper trading, live later).

## Tooling

- **GStack** installed at `~/.claude/skills/gstack` — provides 30+ Claude Code slash commands for structured planning and review (`/office-hours`, `/plan-eng-review`, `/plan-ceo-review`, `/review`, `/qa`, `/ship`, etc.)
- **MemPalace** — run `mempalace init` to configure, `mempalace mine` to ingest context, `mempalace search` to query

## Key Dependencies

- `ccxt` — exchange API (Binance OHLCV data)
- `openai` — client for local llama-server (grammar-constrained JSON)
- `pandas-ta` — technical indicators (RSI, MACD, Bollinger Bands, SMA)
- `pandas` — data manipulation (required by pandas-ta)
- `sqlalchemy` — database ORM for SQLite storage
- `anthropic` — Claude Opus API (Phase 3+)
- `mempalace` — local AI memory system (Phase 3+)
- `huggingface-hub` / `hf-transfer` — model downloads
- `pytest` — test framework (dev dependency)

## Documentation

- `docs/design.md` — Approved design doc from /office-hours (problem statement, premises, approach, all 4 phases)
- `docs/architecture-decisions.md` — 12 architecture decisions from /plan-eng-review with rationale, outside voice findings, operational learnings, and next steps
- `docs/phase-1-findings.md` — **Run #2 investigation (2026-04-16):** v1 prompt produces noise (win rate 18.2%, Pearson r −0.067). Root cause: overtrading, panic-sells. Contains proposed v2 prompt changes. **Read before iterating the prompt.**

## Current Status (2026-04-16)

- Phase 1 infrastructure: BUILT, tested, runs end-to-end
- First quick replay: COMPLETE (run_id=2, 200 candles, 5 fallbacks / 200 = 2.5%)
- Signal quality: **FAIL** — 18.2% win rate, confidence anti-correlated with outcome
- Dashboard: `uv run python -m llm_local.dashboard` → http://localhost:8090
- Next session pick-up: write v2 prompt per `docs/phase-1-findings.md`, re-run quick, compare

**Approach:** C then B — validate LLM signal quality first (decision harness + historical replay), then build on Freqtrade infrastructure.

**Phases:**
1. **Decision Harness (BUILT)** — `llm_local/` package: harness, data fetcher, replay engine, analysis. Replay 30 days of BTC/USDT 15m candles through Gemma4-26B with full TA indicators, measure signal quality. 92 tests.
2. **Freqtrade Integration (Week 1)** — Pre-computed signal file approach (LLM runs separately, writes signals to disk, Freqtrade reads them).
3. **Meta-Loop + Claude Opus (Weeks 2-3)** — Self-monitoring performance degradation, automatic strategy adjustment with rollback.
4. **Live Trading (Week 4+)** — $50-100 real capital, hard-coded risk rules (25% max position, 5% daily drawdown, 15% total drawdown).

## Next Steps

**Immediate (prompt iteration):**
1. Implement `_build_v2()` in `llm_local/prompts.py` per `docs/phase-1-findings.md` §Proposed v2 Prompt Changes (fee injection, asymmetric bars, anti-panic, confidence calibration, default-hold bias). Add `"v2"` to `AVAILABLE_VERSIONS`.
2. Add unit test in `tests/test_prompts.py` for v2 builder.
3. Start llama.cpp server, then: `uv run python -m llm_local replay --quick --prompt-version v2` (~45 min).
4. `uv run python -m llm_local analyze --compare` — check if win rate >40% and Pearson r >0.1.
5. If v2 fails: `/office-hours` to reconsider (longer timeframe? different model? classification framing?).

**After signal validated:**
6. Phase 2: Freqtrade integration via pre-computed signal files
7. Phase 3: Meta-loop with Claude Opus for self-monitoring strategy adjustment
8. Phase 4: Live trading with $50-100 real capital

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
