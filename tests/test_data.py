"""Tests for data fetching and indicator computation."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from sqlalchemy import select

from llm_local.config import Config
from llm_local.data import fetch_ohlcv, compute_indicators, _nan_to_none
from llm_local.models import Candle, get_session, init_db


def _recent_ts(offset_minutes=0):
    """Generate a recent timestamp (within last 10 days) in milliseconds."""
    base = datetime.now(timezone.utc) - timedelta(days=10)
    return int((base + timedelta(minutes=offset_minutes)).timestamp() * 1000)


class TestFetchOhlcv:
    def test_caches_candles_in_db(self, tmp_db, config):
        """Fetched candles are stored in SQLite."""
        engine, db_path = tmp_db
        config.db_path = db_path

        mock_ohlcv = [
            [_recent_ts(i * 15), 65000 + i, 65100 + i, 64900 + i, 65050 + i, 1000 + i]
            for i in range(50)
        ]

        with patch("llm_local.data.ccxt") as mock_ccxt:
            mock_exchange = MagicMock()
            mock_exchange.fetch_ohlcv.return_value = mock_ohlcv
            mock_ccxt.binance.return_value = mock_exchange

            candles = fetch_ohlcv(config, engine)

        assert len(candles) == 50

        # Verify in DB
        with get_session(engine) as session:
            db_candles = session.execute(select(Candle)).scalars().all()
            assert len(db_candles) == 50

    def test_skips_fetch_when_cached(self, populated_db, config):
        """Does not call Binance API when candles exist in DB."""
        engine, db_path = populated_db
        config.db_path = db_path

        with patch("llm_local.data.ccxt") as mock_ccxt:
            candles = fetch_ohlcv(config, engine)
            mock_ccxt.binance.assert_not_called()

        assert len(candles) == 100

    def test_force_refetch(self, populated_db, config):
        """Force flag re-fetches even with cached data."""
        engine, db_path = populated_db
        config.db_path = db_path

        mock_ohlcv = [
            [1710500000000 + i * 900000, 65000, 65100, 64900, 65050, 1000]
            for i in range(10)
        ]

        with patch("llm_local.data.ccxt") as mock_ccxt:
            mock_exchange = MagicMock()
            mock_exchange.fetch_ohlcv.return_value = mock_ohlcv
            mock_ccxt.binance.return_value = mock_exchange

            candles = fetch_ohlcv(config, engine, force=True)

        # Should have called the exchange
        mock_ccxt.binance.assert_called_once()

    def test_pagination(self, tmp_db, config):
        """Fetches multiple pages when >1000 candles needed."""
        engine, db_path = tmp_db
        config.db_path = db_path

        page1 = [
            [_recent_ts(i * 15), 65000, 65100, 64900, 65050, 1000]
            for i in range(1000)
        ]
        page2 = [
            [_recent_ts((1000 + i) * 15), 65000, 65100, 64900, 65050, 1000]
            for i in range(500)
        ]

        with patch("llm_local.data.ccxt") as mock_ccxt:
            mock_exchange = MagicMock()
            mock_exchange.fetch_ohlcv.side_effect = [page1, page2]
            mock_ccxt.binance.return_value = mock_exchange

            candles = fetch_ohlcv(config, engine)

        assert len(candles) == 1500
        assert mock_exchange.fetch_ohlcv.call_count == 2

    def test_empty_response(self, tmp_db, config):
        """Handles exchange returning no data."""
        engine, db_path = tmp_db
        config.db_path = db_path

        with patch("llm_local.data.ccxt") as mock_ccxt:
            mock_exchange = MagicMock()
            mock_exchange.fetch_ohlcv.return_value = []
            mock_ccxt.binance.return_value = mock_exchange

            candles = fetch_ohlcv(config, engine)

        assert len(candles) == 0


class TestComputeIndicators:
    def test_computes_all_indicators(self, populated_db, config):
        """Indicators are computed and stored on candle objects."""
        engine, db_path = populated_db

        with get_session(engine) as session:
            candles = session.execute(
                select(Candle).order_by(Candle.timestamp_ms)
            ).scalars().all()
            session.expunge_all()

        result = compute_indicators(candles, engine)

        # Later candles (past warmup) should have indicators
        last_candle = result[-1]
        assert last_candle.sma_24 is not None
        assert last_candle.rsi_14 is not None

    def test_warmup_period_has_nans(self, populated_db, config):
        """First ~34 candles have None indicators (warmup)."""
        engine, db_path = populated_db

        with get_session(engine) as session:
            candles = session.execute(
                select(Candle).order_by(Candle.timestamp_ms)
            ).scalars().all()
            session.expunge_all()

        result = compute_indicators(candles, engine)

        # First candle should have None for SMA(96) since it needs 96 candles
        # With only 100 candles, sma_96 will be None for first 95
        assert result[0].sma_24 is None  # Needs 24 candles

    def test_empty_candle_list(self):
        """Empty input returns empty output."""
        result = compute_indicators([])
        assert result == []

    def test_updates_db(self, populated_db):
        """Indicators are persisted to the database."""
        engine, db_path = populated_db

        with get_session(engine) as session:
            candles = session.execute(
                select(Candle).order_by(Candle.timestamp_ms)
            ).scalars().all()
            session.expunge_all()

        compute_indicators(candles, engine)

        # Re-read from DB (latest candle should have indicators)
        with get_session(engine) as session:
            db_candle = session.execute(
                select(Candle).order_by(Candle.timestamp_ms.desc()).limit(1)
            ).scalar_one()
            assert db_candle.sma_24 is not None


class TestNanToNone:
    def test_nan_becomes_none(self):
        assert _nan_to_none(float("nan")) is None

    def test_inf_becomes_none(self):
        assert _nan_to_none(float("inf")) is None

    def test_none_stays_none(self):
        assert _nan_to_none(None) is None

    def test_normal_float_passes(self):
        assert _nan_to_none(42.5) == 42.5

    def test_zero_passes(self):
        assert _nan_to_none(0.0) == 0.0
