"""Tests for SQLAlchemy models and database operations."""

import pytest
from sqlalchemy import select, inspect

from llm_local.models import Base, Candle, Decision, ReplayRun, init_db, get_session


class TestInitDb:
    def test_creates_tables(self, tmp_path):
        """init_db creates all expected tables."""
        db_path = str(tmp_path / "test.db")
        engine = init_db(db_path)
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "candles" in tables
        assert "decisions" in tables
        assert "replay_runs" in tables

    def test_idempotent(self, tmp_path):
        """Calling init_db twice doesn't error."""
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        engine = init_db(db_path)
        assert engine is not None


class TestCandle:
    def test_insert_candle(self, tmp_db):
        engine, _ = tmp_db
        with get_session(engine) as session:
            candle = Candle(
                symbol="BTC/USDT",
                timeframe="15m",
                timestamp_ms=1710500000000,
                open=65000.0,
                high=65100.0,
                low=64900.0,
                close=65050.0,
                volume=1234.5,
            )
            session.add(candle)

        with get_session(engine) as session:
            result = session.execute(select(Candle)).scalar_one()
            assert result.symbol == "BTC/USDT"
            assert result.close == 65050.0

    def test_unique_constraint(self, tmp_db):
        """Duplicate (symbol, timeframe, timestamp) should raise."""
        engine, _ = tmp_db
        with get_session(engine) as session:
            session.add(Candle(
                symbol="BTC/USDT", timeframe="15m", timestamp_ms=1000,
                open=1, high=2, low=0.5, close=1.5, volume=100,
            ))

        with pytest.raises(Exception):
            with get_session(engine) as session:
                session.add(Candle(
                    symbol="BTC/USDT", timeframe="15m", timestamp_ms=1000,
                    open=2, high=3, low=1, close=2.5, volume=200,
                ))

    def test_nullable_indicators(self, tmp_db):
        """Indicator columns accept None (warmup period)."""
        engine, _ = tmp_db
        with get_session(engine) as session:
            candle = Candle(
                symbol="BTC/USDT", timeframe="15m", timestamp_ms=2000,
                open=1, high=2, low=0.5, close=1.5, volume=100,
                sma_24=None, rsi_14=None, macd=None,
            )
            session.add(candle)

        with get_session(engine) as session:
            result = session.execute(select(Candle)).scalar_one()
            assert result.sma_24 is None
            assert result.rsi_14 is None


class TestDecision:
    def test_insert_decision_with_fk(self, tmp_db):
        """Decisions reference run_id and candle_id."""
        engine, _ = tmp_db

        with get_session(engine) as session:
            session.add(Candle(
                symbol="BTC/USDT", timeframe="15m", timestamp_ms=1000,
                open=1, high=2, low=0.5, close=1.5, volume=100,
            ))
            session.add(ReplayRun(
                started_at="2026-01-01T00:00:00Z", status="running",
                symbol="BTC/USDT", timeframe="15m",
                prompt_version="v1", config="{}",
            ))

        with get_session(engine) as session:
            candle = session.execute(select(Candle)).scalar_one()
            run = session.execute(select(ReplayRun)).scalar_one()
            session.add(Decision(
                run_id=run.id, candle_id=candle.id,
                action="buy", size_pct=0.5, confidence=0.8,
                reasoning="test", created_at="2026-01-01T00:00:00Z",
            ))

        with get_session(engine) as session:
            result = session.execute(select(Decision)).scalar_one()
            assert result.action == "buy"
            assert result.confidence == 0.8


class TestReplayRun:
    def test_query_by_run_id(self, tmp_db):
        """Can query decisions filtered by run_id."""
        engine, _ = tmp_db

        with get_session(engine) as session:
            session.add(Candle(
                symbol="BTC/USDT", timeframe="15m", timestamp_ms=1000,
                open=1, high=2, low=0.5, close=1.5, volume=100,
            ))
            session.add(ReplayRun(
                started_at="2026-01-01", status="completed",
                symbol="BTC/USDT", timeframe="15m",
                prompt_version="v1", config="{}",
            ))
            session.add(ReplayRun(
                started_at="2026-01-02", status="completed",
                symbol="BTC/USDT", timeframe="15m",
                prompt_version="v2", config="{}",
            ))

        with get_session(engine) as session:
            runs = session.execute(
                select(ReplayRun).where(ReplayRun.prompt_version == "v1")
            ).scalars().all()
            assert len(runs) == 1
            assert runs[0].prompt_version == "v1"
