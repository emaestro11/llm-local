# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Local ML-based crypto trading system. Uses local LLMs (Gemma4-26B, Qwen3.5) via llama.cpp for trade execution and monitoring, with Claude Opus for research and strategy planning. Trades on Binance (paper trading first, live later).

## Development Commands

```bash
uv run main.py          # Run the application
uv add <package>        # Add a dependency
uv sync                 # Install/sync all dependencies
uv run python -m pytest # Run tests (once pytest is added)
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

Storage layers:

- **SQLite** — structured trade data (orders, P&L, strategy configs)
- **MemPalace** — persistent conversational memory and context across sessions (ChromaDB + knowledge graph)

Exchange integration via `ccxt` (Binance testnet for paper trading, live later).

## Tooling

- **GStack** installed at `~/.claude/skills/gstack` — provides 30+ Claude Code slash commands for structured planning and review (`/office-hours`, `/plan-eng-review`, `/plan-ceo-review`, `/review`, `/qa`, `/ship`, etc.)
- **MemPalace** — run `mempalace init` to configure, `mempalace mine` to ingest context, `mempalace search` to query

## Key Dependencies

- `ccxt` — exchange API (Binance)
- `openai` — client for local llama-server
- `anthropic` — Claude Opus API
- `mempalace` — local AI memory system (ChromaDB + SQLite knowledge graph)
- `sqlalchemy` — database ORM for trade storage
- `huggingface-hub` / `hf-transfer` — model downloads

## Design Doc

The approved design document lives at:
`~/.gstack/projects/emaestro11-llm-local/esteban-main-design-20260412-200049.md`

**Approach:** C then B — validate LLM signal quality first (decision harness + historical replay), then build on Freqtrade infrastructure.

**Phases:**
1. **Decision Harness (Weekend)** — `decision_harness.py`, `data_fetcher.py`, `replay_engine.py`, `analysis.py`. Replay 30 days of BTC/USDT through Gemma4-26B, measure signal quality.
2. **Freqtrade Integration (Week 1)** — Pre-computed signal file approach (LLM runs separately, writes signals to disk, Freqtrade reads them).
3. **Meta-Loop + Claude Opus (Weeks 2-3)** — Self-monitoring performance degradation, automatic strategy adjustment with rollback.
4. **Live Trading (Week 4+)** — $50-100 real capital, hard-coded risk rules (25% max position, 5% daily drawdown, 15% total drawdown).

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
