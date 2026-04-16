"""Replay engine: iterate historical candles, call decision harness, track positions."""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, func

from llm_local.config import Config
from llm_local.harness import make_decision, CircuitBreakerError, TradeDecision
from llm_local.models import Candle, Decision, ReplayRun, get_session, init_db
from llm_local.prompts import candle_to_prompt_dict

logger = logging.getLogger(__name__)

# Minimum candles needed for indicator warmup (MACD requires ~34)
WARMUP_CANDLES = 34


@dataclass
class SimulatedPosition:
    """Tracks a simulated trading position."""

    entry_price: float
    entry_candle_idx: int
    size: float  # dollar amount
    size_pct: float  # fraction of capital used

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """Unrealized P&L as a percentage."""
        return (current_price - self.entry_price) / self.entry_price

    def close_pnl(self, exit_price: float, fee_rate: float) -> float:
        """Realized P&L after fees (dollar amount)."""
        gross_return = (exit_price - self.entry_price) / self.entry_price
        # Fee on entry and exit
        net_return = gross_return - 2 * fee_rate
        return self.size * net_return


def run_replay(
    config: Config,
    quick: bool = False,
    prompt_version: str = "v1",
    engine=None,
) -> int:
    """Run historical replay: iterate candles, call LLM, record decisions.

    Returns the replay run_id.
    Supports checkpoint/resume: if a run with the same params exists and is
    incomplete, resumes from the last recorded decision.
    """
    if engine is None:
        engine = init_db(config.db_path)

    # Load candles from DB
    with get_session(engine) as session:
        candles = session.execute(
            select(Candle)
            .where(Candle.symbol == config.symbol)
            .where(Candle.timeframe == config.timeframe)
            .order_by(Candle.timestamp_ms)
        ).scalars().all()
        session.expunge_all()

    if not candles:
        raise ValueError(
            f"No candles found for {config.symbol} {config.timeframe}. "
            f"Run 'fetch' first."
        )

    # Skip warmup candles
    if len(candles) <= WARMUP_CANDLES:
        raise ValueError(
            f"Only {len(candles)} candles available, need at least "
            f"{WARMUP_CANDLES + 1} (warmup period)."
        )

    replay_candles = candles[WARMUP_CANDLES:]

    # Quick mode: last N candles only
    if quick:
        replay_candles = replay_candles[-config.quick_candles:]
        logger.info(f"Quick mode: using last {len(replay_candles)} candles")

    total_candles = len(replay_candles)
    logger.info(
        f"Replay: {total_candles} candles "
        f"({config.symbol} {config.timeframe}, prompt {prompt_version})"
    )

    # Create or resume replay run
    run_id, resume_from = _get_or_create_run(
        engine, config, quick, prompt_version, total_candles
    )

    # Replay loop
    position: SimulatedPosition | None = None
    consecutive_failures = 0
    decisions_made = 0
    start_time = time.monotonic()

    for i, candle in enumerate(replay_candles):
        # Skip already-processed candles (checkpoint/resume)
        if i < resume_from:
            continue

        # Build context window (lookback)
        candle_idx_in_full = candles.index(candle)
        lookback_start = max(0, candle_idx_in_full - config.lookback_candles)
        context_candles = candles[lookback_start : candle_idx_in_full + 1]
        context_dicts = [candle_to_prompt_dict(c) for c in context_candles]

        # Build position context for prompt
        position_dict = None
        if position is not None:
            hold_candles = i - position.entry_candle_idx
            remaining_hold = config.max_hold_candles - hold_candles
            position_dict = {
                "entry_price": position.entry_price,
                "current_price": candle.close,
                "unrealized_pnl_pct": position.unrealized_pnl_pct(candle.close),
                "hold_candles": hold_candles,
                "max_hold_candles": remaining_hold,
            }

        # Call LLM
        try:
            decision = make_decision(
                context_dicts,
                position_dict,
                config,
                prompt_version,
                _consecutive_failures=consecutive_failures,
            )
        except CircuitBreakerError:
            logger.error(
                f"Circuit breaker tripped at candle {i}/{total_candles}. "
                f"Pausing replay. Check LLM server."
            )
            _update_run_status(engine, run_id, "paused", decisions_made)
            return run_id

        # Track consecutive failures
        if decision.is_fallback:
            consecutive_failures += 1
        else:
            consecutive_failures = 0

        # Validate action and apply position logic
        effective_action = _apply_position_logic(
            decision, position, i, config.max_hold_candles, candle.close, config.fee_rate
        )

        # Update position state
        if effective_action == "buy" and position is None:
            capital = config.starting_capital  # Simplified: always use starting capital
            position = SimulatedPosition(
                entry_price=candle.close,
                entry_candle_idx=i,
                size=capital * decision.size_pct,
                size_pct=decision.size_pct,
            )
        elif effective_action == "sell" and position is not None:
            position = None
        elif effective_action == "force_close" and position is not None:
            position = None

        # Record decision to DB immediately
        _record_decision(engine, run_id, candle, decision, effective_action)
        decisions_made += 1

        # Progress reporting
        if decisions_made % 100 == 0 or i == total_candles - 1:
            elapsed = time.monotonic() - start_time
            rate = decisions_made / elapsed if elapsed > 0 else 0
            remaining = (total_candles - i - 1) / rate if rate > 0 else 0
            logger.info(
                f"Progress: {i + 1}/{total_candles} candles "
                f"({decisions_made} decisions, {rate:.1f}/s, "
                f"ETA: {remaining / 60:.0f}min)"
            )

    # Force-close any open position at end of replay
    if position is not None:
        logger.info("Replay ended with open position, force-closing.")

    _update_run_status(engine, run_id, "completed", decisions_made)
    elapsed = time.monotonic() - start_time
    logger.info(
        f"Replay complete: {decisions_made} decisions in {elapsed:.0f}s "
        f"(run_id={run_id})"
    )

    return run_id


def _apply_position_logic(
    decision: TradeDecision,
    position: SimulatedPosition | None,
    candle_idx: int,
    max_hold_candles: int,
    current_price: float,
    fee_rate: float,
) -> str:
    """Validate and apply position logic. Returns the effective action.

    Rules:
    - Can't sell without a position
    - Can't buy with an existing position
    - Force-close if max hold exceeded
    """
    # Check max hold cap
    if position is not None:
        hold_duration = candle_idx - position.entry_candle_idx
        if hold_duration >= max_hold_candles:
            logger.info(
                f"Max hold cap reached ({hold_duration} candles). "
                f"Force-closing position."
            )
            return "force_close"

    action = decision.action

    if action == "buy" and position is not None:
        return "hold"  # Already in a position
    if action == "sell" and position is None:
        return "hold"  # Nothing to sell

    return action


def _get_or_create_run(
    engine, config: Config, quick: bool, prompt_version: str, total_candles: int
) -> tuple[int, int]:
    """Get existing incomplete run or create a new one. Returns (run_id, resume_from)."""
    with get_session(engine) as session:
        # Check for resumable run
        existing = session.execute(
            select(ReplayRun)
            .where(ReplayRun.status == "running")
            .where(ReplayRun.symbol == config.symbol)
            .where(ReplayRun.timeframe == config.timeframe)
            .where(ReplayRun.prompt_version == prompt_version)
            .where(ReplayRun.quick_mode == (1 if quick else 0))
            .order_by(ReplayRun.id.desc())
        ).scalar_one_or_none()

        if existing is not None:
            # Count existing decisions to find resume point
            count = session.execute(
                select(func.count(Decision.id))
                .where(Decision.run_id == existing.id)
            ).scalar()
            logger.info(
                f"Resuming run {existing.id} from decision {count}"
            )
            return existing.id, count

        # Create new run
        config_json = json.dumps({
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "lookback_candles": config.lookback_candles,
            "max_hold_candles": config.max_hold_candles,
            "fee_rate": config.fee_rate,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "prompt_version": prompt_version,
            "quick_mode": quick,
        })

        run = ReplayRun(
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
            symbol=config.symbol,
            timeframe=config.timeframe,
            prompt_version=prompt_version,
            config=config_json,
            candle_count=total_candles,
            quick_mode=1 if quick else 0,
        )
        session.add(run)
        session.flush()
        run_id = run.id
        logger.info(f"Created new replay run {run_id}")
        return run_id, 0


def _record_decision(
    engine, run_id: int, candle: Candle, decision: TradeDecision, effective_action: str
):
    """Write a single decision to the database immediately."""
    with get_session(engine) as session:
        record = Decision(
            run_id=run_id,
            candle_id=candle.id,
            action=effective_action,
            size_pct=decision.size_pct,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            raw_response=decision.raw_response,
            latency_ms=decision.latency_ms,
            is_fallback=1 if decision.is_fallback else 0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(record)


def _update_run_status(engine, run_id: int, status: str, decision_count: int):
    """Update replay run status and decision count."""
    with get_session(engine) as session:
        run = session.get(ReplayRun, run_id)
        if run:
            run.status = status
            run.decision_count = decision_count
            run.completed_at = datetime.now(timezone.utc).isoformat()
