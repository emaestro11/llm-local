"""Shared fixtures for tests."""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from llm_local.config import Config
from llm_local.models import Base, Candle, init_db, get_session


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    engine = init_db(db_path)
    return engine, db_path


@pytest.fixture
def config(tmp_db):
    """Config pointing at temp database."""
    engine, db_path = tmp_db
    return Config(db_path=db_path)


@pytest.fixture
def sample_candles():
    """Generate 100 sample candles with realistic BTC/USDT data."""
    import random

    candles = []
    # Use timestamps within the last 10 days so they're always within config.days window
    base_time = int((datetime.now(timezone.utc) - timedelta(days=10)).timestamp() * 1000)
    base_price = 65000.0

    for i in range(100):
        random.seed(42 + i)
        change = random.gauss(0, 100)
        price = base_price + change * (i / 10)

        candles.append({
            "timestamp_ms": base_time + i * 15 * 60 * 1000,  # 15min intervals
            "open": price - 50,
            "high": price + 100,
            "low": price - 100,
            "close": price,
            "volume": 1000 + random.random() * 500,
        })

    return candles


@pytest.fixture
def populated_db(tmp_db, sample_candles):
    """Temp DB with 100 sample candles inserted."""
    engine, db_path = tmp_db

    with get_session(engine) as session:
        for c in sample_candles:
            candle = Candle(
                symbol="BTC/USDT",
                timeframe="15m",
                timestamp_ms=c["timestamp_ms"],
                open=c["open"],
                high=c["high"],
                low=c["low"],
                close=c["close"],
                volume=c["volume"],
            )
            session.add(candle)

    return engine, db_path


@pytest.fixture
def config_toml(tmp_path):
    """Create a temporary config.toml file."""
    toml_content = """
[llm]
url = "http://localhost:8080/v1"
model = "test-model"
temperature = 0.3
max_tokens = 512
timeout_seconds = 5

[trading]
symbol = "BTC/USDT"
timeframe = "15m"
lookback_candles = 24
max_hold_candles = 24
fee_rate = 0.001
starting_capital = 10000.0

[data]
days = 30
quick_candles = 200

[database]
path = "test.db"
"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(toml_content)
    return str(toml_path)


@pytest.fixture
def mock_llm_response():
    """Create a mock LLM response factory."""
    def _make_response(action="hold", confidence=0.5, size_pct=0.1, reasoning="test"):
        response_json = json.dumps({
            "action": action,
            "confidence": confidence,
            "size_pct": size_pct,
            "reasoning": reasoning,
        })
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = response_json
        return mock_response

    return _make_response
