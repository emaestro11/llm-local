"""Tests for analysis metrics: win rate, Sharpe, drawdown, confidence calibration."""

import math
import pytest

from llm_local.analysis import (
    Trade,
    Report,
    _compound_returns,
    _sharpe_ratio,
    _max_drawdown,
    _confidence_calibration,
    format_report,
    analyze_run,
)
from llm_local.config import Config
from llm_local.models import Candle, Decision, ReplayRun, get_session, init_db


class TestCompoundReturns:
    def test_single_positive_return(self):
        assert _compound_returns([0.10]) == pytest.approx(0.10)

    def test_multiple_returns(self):
        # (1.10) * (1.05) * (0.97) = 1.12035 -> 0.12035
        result = _compound_returns([0.10, 0.05, -0.03])
        assert result == pytest.approx(0.12035, rel=1e-4)

    def test_empty_returns(self):
        assert _compound_returns([]) == pytest.approx(0.0)

    def test_all_losses(self):
        result = _compound_returns([-0.05, -0.05, -0.05])
        assert result < 0


class TestSharpeRatio:
    def test_positive_sharpe(self):
        returns = [0.02, 0.03, 0.01, 0.04, 0.02, 0.01, 0.03]
        sharpe = _sharpe_ratio(returns)
        assert sharpe > 0

    def test_zero_std_returns_zero(self):
        """All identical returns -> std=0 -> Sharpe=0."""
        returns = [0.01, 0.01, 0.01, 0.01]
        assert _sharpe_ratio(returns) == 0.0

    def test_single_return(self):
        """Less than 2 returns -> Sharpe=0."""
        assert _sharpe_ratio([0.05]) == 0.0

    def test_empty_returns(self):
        assert _sharpe_ratio([]) == 0.0


class TestMaxDrawdown:
    def test_no_drawdown(self):
        """Monotonically increasing returns have 0 drawdown."""
        returns = [0.05, 0.05, 0.05]
        assert _max_drawdown(returns) == pytest.approx(0.0)

    def test_single_loss(self):
        """Drawdown from a single loss."""
        returns = [0.10, -0.05, 0.10]
        dd = _max_drawdown(returns)
        assert dd > 0
        assert dd < 1.0

    def test_deep_drawdown(self):
        """Large losses produce significant drawdown."""
        returns = [0.10, -0.20, -0.20, 0.10]
        dd = _max_drawdown(returns)
        assert dd > 0.3  # Should be substantial

    def test_empty_returns(self):
        assert _max_drawdown([]) == 0.0


class TestConfidenceCalibration:
    def test_perfect_correlation(self):
        """Confidence perfectly predicts returns."""
        trades = [
            Trade(0, 1, 100, 110, 0.5, 0.9, 3, "sell", 0.10, 0.098),
            Trade(0, 1, 100, 105, 0.5, 0.7, 3, "sell", 0.05, 0.048),
            Trade(0, 1, 100, 102, 0.5, 0.5, 3, "sell", 0.02, 0.018),
            Trade(0, 1, 100, 101, 0.5, 0.3, 3, "sell", 0.01, 0.008),
        ]
        corr = _confidence_calibration(trades)
        assert corr is not None
        assert corr > 0.9

    def test_no_correlation(self):
        """Random confidence vs returns."""
        trades = [
            Trade(0, 1, 100, 110, 0.5, 0.9, 3, "sell", 0.10, 0.098),
            Trade(0, 1, 100, 90, 0.5, 0.8, 3, "sell", -0.10, -0.102),
            Trade(0, 1, 100, 105, 0.5, 0.2, 3, "sell", 0.05, 0.048),
            Trade(0, 1, 100, 95, 0.5, 0.5, 3, "sell", -0.05, -0.052),
        ]
        corr = _confidence_calibration(trades)
        assert corr is not None
        # Should be close to 0 (random)
        assert abs(corr) < 0.5

    def test_insufficient_data(self):
        """Fewer than 3 trades returns None."""
        trades = [
            Trade(0, 1, 100, 110, 0.5, 0.9, 3, "sell", 0.10, 0.098),
            Trade(0, 1, 100, 105, 0.5, 0.7, 3, "sell", 0.05, 0.048),
        ]
        assert _confidence_calibration(trades) is None

    def test_constant_confidence(self):
        """All same confidence -> None (undefined correlation)."""
        trades = [
            Trade(0, 1, 100, 110, 0.5, 0.5, 3, "sell", 0.10, 0.098),
            Trade(0, 1, 100, 105, 0.5, 0.5, 3, "sell", 0.05, 0.048),
            Trade(0, 1, 100, 102, 0.5, 0.5, 3, "sell", 0.02, 0.018),
        ]
        assert _confidence_calibration(trades) is None

    def test_empty_trades(self):
        assert _confidence_calibration([]) is None


class TestFormatReport:
    def test_formats_without_crash(self):
        """format_report produces a string for any valid report."""
        report = Report(
            run_id=1, prompt_version="v1",
            total_candles=100, total_decisions=100,
            total_trades=10, fallback_count=2,
            win_rate=0.6, avg_return=0.02,
            total_return=0.2, sharpe_ratio=1.5,
            max_drawdown=0.05, simulated_pnl=2000.0,
            confidence_correlation=0.15,
            buy_count=20, sell_count=15, hold_count=65,
        )
        text = format_report(report)
        assert "Run #1" in text
        assert "60.0%" in text  # win rate
        assert "1.50" in text  # sharpe

    def test_formats_zero_trades(self):
        """Handles zero trades gracefully."""
        report = Report(
            run_id=1, prompt_version="v1",
            total_candles=100, total_decisions=100,
            total_trades=0, fallback_count=0,
        )
        text = format_report(report)
        assert "No trades executed" in text

    def test_formats_none_calibration(self):
        """Handles None confidence correlation."""
        report = Report(
            run_id=1, prompt_version="v1",
            total_candles=50, total_decisions=50,
            total_trades=2, fallback_count=0,
            confidence_correlation=None,
        )
        text = format_report(report)
        assert "Insufficient data" in text


class TestAnalyzeRun:
    def _setup_completed_run(self, engine):
        """Insert a completed run with decisions for testing."""
        with get_session(engine) as session:
            # Insert candles
            candle_ids = []
            for i in range(10):
                candle = Candle(
                    symbol="BTC/USDT", timeframe="15m",
                    timestamp_ms=1710500000000 + i * 900000,
                    open=65000 + i * 100, high=65100 + i * 100,
                    low=64900 + i * 100, close=65050 + i * 100,
                    volume=1000,
                )
                session.add(candle)
                session.flush()
                candle_ids.append(candle.id)

            # Insert run
            run = ReplayRun(
                started_at="2026-01-01", status="completed",
                symbol="BTC/USDT", timeframe="15m",
                prompt_version="v1", config="{}",
                candle_count=10, decision_count=10,
            )
            session.add(run)
            session.flush()
            run_id = run.id

            # Insert decisions: buy at candle 0, sell at candle 3, buy at 5, sell at 8
            actions = ["buy", "hold", "hold", "sell", "hold", "buy", "hold", "hold", "sell", "hold"]
            for i, action in enumerate(actions):
                session.add(Decision(
                    run_id=run_id, candle_id=candle_ids[i],
                    action=action, size_pct=0.5 if action == "buy" else 0.0,
                    confidence=0.7, reasoning="test",
                    created_at="2026-01-01",
                ))

        return run_id

    def test_analyze_completed_run(self, tmp_db):
        engine, db_path = tmp_db
        config = Config(db_path=db_path)
        run_id = self._setup_completed_run(engine)

        report = analyze_run(run_id, config, engine)

        assert report.run_id == run_id
        assert report.total_decisions == 10
        assert report.total_trades == 2
        assert report.buy_count == 2
        assert report.sell_count == 2

    def test_analyze_nonexistent_run(self, tmp_db):
        engine, db_path = tmp_db
        config = Config(db_path=db_path)

        with pytest.raises(ValueError, match="not found"):
            analyze_run(999, config, engine)

    def test_analyze_run_no_decisions(self, tmp_db):
        """Run with no decisions produces empty report."""
        engine, db_path = tmp_db
        config = Config(db_path=db_path)

        with get_session(engine) as session:
            run = ReplayRun(
                started_at="2026-01-01", status="completed",
                symbol="BTC/USDT", timeframe="15m",
                prompt_version="v1", config="{}",
            )
            session.add(run)
            session.flush()
            run_id = run.id

        report = analyze_run(run_id, config, engine)
        assert report.total_trades == 0
        assert report.win_rate == 0.0
