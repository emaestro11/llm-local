"""Tests for the replay engine: position tracking, checkpoint/resume, action validation."""

import json
import pytest
from unittest.mock import patch, MagicMock, call

from sqlalchemy import select

from llm_local.config import Config
from llm_local.harness import TradeDecision, CircuitBreakerError
from llm_local.models import Candle, Decision, ReplayRun, get_session, init_db
from llm_local.replay import (
    run_replay,
    SimulatedPosition,
    _apply_position_logic,
    WARMUP_CANDLES,
)


class TestSimulatedPosition:
    def test_unrealized_pnl_positive(self):
        pos = SimulatedPosition(entry_price=100.0, entry_candle_idx=0, size=1000.0, size_pct=0.1)
        assert pos.unrealized_pnl_pct(110.0) == pytest.approx(0.1)

    def test_unrealized_pnl_negative(self):
        pos = SimulatedPosition(entry_price=100.0, entry_candle_idx=0, size=1000.0, size_pct=0.1)
        assert pos.unrealized_pnl_pct(90.0) == pytest.approx(-0.1)

    def test_close_pnl_with_fees(self):
        """P&L accounts for 0.1% fee on both entry and exit."""
        pos = SimulatedPosition(entry_price=100.0, entry_candle_idx=0, size=1000.0, size_pct=0.1)
        pnl = pos.close_pnl(110.0, fee_rate=0.001)
        # gross: 10% = 0.1, fees: 0.2%, net: 9.8%
        expected = 1000.0 * (0.1 - 0.002)
        assert pnl == pytest.approx(expected)

    def test_close_pnl_loss(self):
        pos = SimulatedPosition(entry_price=100.0, entry_candle_idx=0, size=1000.0, size_pct=0.1)
        pnl = pos.close_pnl(95.0, fee_rate=0.001)
        assert pnl < 0


class TestApplyPositionLogic:
    def _make_decision(self, action="hold", confidence=0.5):
        return TradeDecision(
            action=action, size_pct=0.1, confidence=confidence,
            reasoning="test",
        )

    def test_buy_with_no_position(self):
        """Buy is allowed when flat."""
        result = _apply_position_logic(
            self._make_decision("buy"), None, 10, 24, 65000.0, 0.001
        )
        assert result == "buy"

    def test_buy_with_existing_position(self):
        """Buy blocked when already in position -> hold."""
        pos = SimulatedPosition(entry_price=65000, entry_candle_idx=5, size=1000, size_pct=0.1)
        result = _apply_position_logic(
            self._make_decision("buy"), pos, 10, 24, 65500.0, 0.001
        )
        assert result == "hold"

    def test_sell_with_position(self):
        """Sell is allowed when in position."""
        pos = SimulatedPosition(entry_price=65000, entry_candle_idx=5, size=1000, size_pct=0.1)
        result = _apply_position_logic(
            self._make_decision("sell"), pos, 10, 24, 65500.0, 0.001
        )
        assert result == "sell"

    def test_sell_with_no_position(self):
        """Sell blocked when flat -> hold."""
        result = _apply_position_logic(
            self._make_decision("sell"), None, 10, 24, 65000.0, 0.001
        )
        assert result == "hold"

    def test_max_hold_force_close(self):
        """Position force-closed after max hold candles."""
        pos = SimulatedPosition(entry_price=65000, entry_candle_idx=0, size=1000, size_pct=0.1)
        result = _apply_position_logic(
            self._make_decision("hold"), pos, 24, 24, 65500.0, 0.001
        )
        assert result == "force_close"

    def test_hold_within_max_hold(self):
        """Hold is fine when within max hold period."""
        pos = SimulatedPosition(entry_price=65000, entry_candle_idx=0, size=1000, size_pct=0.1)
        result = _apply_position_logic(
            self._make_decision("hold"), pos, 10, 24, 65500.0, 0.001
        )
        assert result == "hold"


class TestRunReplay:
    def _setup_candles(self, engine, count=50):
        """Insert candles into DB for replay. Returns candle IDs."""
        with get_session(engine) as session:
            for i in range(count):
                candle = Candle(
                    symbol="BTC/USDT", timeframe="15m",
                    timestamp_ms=1710500000000 + i * 900000,
                    open=65000 + i * 10, high=65100 + i * 10,
                    low=64900 + i * 10, close=65050 + i * 10,
                    volume=1000, rsi_14=50.0, sma_24=65000.0,
                )
                session.add(candle)

    def test_creates_replay_run(self, tmp_db):
        """Replay creates a run record in the database."""
        engine, db_path = tmp_db
        config = Config(db_path=db_path, max_hold_candles=24)
        self._setup_candles(engine, 50)

        with patch("llm_local.replay.make_decision") as mock_decision:
            mock_decision.return_value = TradeDecision(
                action="hold", size_pct=0, confidence=0.5,
                reasoning="test", latency_ms=100,
            )
            run_id = run_replay(config, quick=True, engine=engine)

        assert run_id is not None

        with get_session(engine) as session:
            run = session.get(ReplayRun, run_id)
            assert run.status == "completed"
            assert run.prompt_version == "v1"

    def test_records_decisions(self, tmp_db):
        """Each candle produces a decision record."""
        engine, db_path = tmp_db
        config = Config(db_path=db_path, quick_candles=10, max_hold_candles=24)
        self._setup_candles(engine, 50)

        with patch("llm_local.replay.make_decision") as mock_decision:
            mock_decision.return_value = TradeDecision(
                action="hold", size_pct=0, confidence=0.5,
                reasoning="test", latency_ms=100,
            )
            run_id = run_replay(config, quick=True, engine=engine)

        with get_session(engine) as session:
            decisions = session.execute(
                select(Decision).where(Decision.run_id == run_id)
            ).scalars().all()
            assert len(decisions) == 10  # quick_candles=10

    def test_no_candles_raises(self, tmp_db):
        """ValueError if no candles exist."""
        engine, db_path = tmp_db
        config = Config(db_path=db_path)

        with pytest.raises(ValueError, match="No candles found"):
            run_replay(config, engine=engine)

    def test_too_few_candles_raises(self, tmp_db):
        """ValueError if fewer candles than warmup period."""
        engine, db_path = tmp_db
        config = Config(db_path=db_path)
        self._setup_candles(engine, WARMUP_CANDLES)  # Exactly warmup, no replay candles

        with pytest.raises(ValueError, match="need at least"):
            run_replay(config, engine=engine)

    def test_circuit_breaker_pauses_run(self, tmp_db):
        """Run status set to 'paused' when circuit breaker trips."""
        engine, db_path = tmp_db
        config = Config(db_path=db_path, max_hold_candles=24)
        self._setup_candles(engine, 50)

        call_count = 0

        def failing_decision(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise CircuitBreakerError(3)
            return TradeDecision(
                action="hold", size_pct=0, confidence=0.5,
                reasoning="test", latency_ms=100, is_fallback=True,
            )

        with patch("llm_local.replay.make_decision", side_effect=failing_decision):
            run_id = run_replay(config, quick=True, engine=engine)

        with get_session(engine) as session:
            run = session.get(ReplayRun, run_id)
            assert run.status == "paused"

    def test_position_tracking_buy_sell(self, tmp_db):
        """Tracks position state across buy and sell decisions."""
        engine, db_path = tmp_db
        config = Config(db_path=db_path, quick_candles=5, max_hold_candles=24)
        self._setup_candles(engine, 50)

        decisions = [
            TradeDecision(action="buy", size_pct=0.5, confidence=0.8, reasoning="go", latency_ms=100),
            TradeDecision(action="hold", size_pct=0, confidence=0.5, reasoning="wait", latency_ms=100),
            TradeDecision(action="sell", size_pct=0, confidence=0.7, reasoning="exit", latency_ms=100),
            TradeDecision(action="hold", size_pct=0, confidence=0.3, reasoning="flat", latency_ms=100),
            TradeDecision(action="hold", size_pct=0, confidence=0.3, reasoning="flat", latency_ms=100),
        ]
        call_idx = 0

        def mock_decision(*args, **kwargs):
            nonlocal call_idx
            d = decisions[min(call_idx, len(decisions) - 1)]
            call_idx += 1
            return d

        with patch("llm_local.replay.make_decision", side_effect=mock_decision):
            run_id = run_replay(config, quick=True, engine=engine)

        with get_session(engine) as session:
            recorded = session.execute(
                select(Decision).where(Decision.run_id == run_id)
                .order_by(Decision.id)
            ).scalars().all()
            actions = [d.action for d in recorded]

        assert actions[0] == "buy"
        assert actions[2] == "sell"
