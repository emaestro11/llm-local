"""Data fetching from Binance and technical indicator computation."""

import logging
import math
import time
from datetime import datetime, timezone, timedelta

import ccxt
import pandas as pd
import pandas_ta as ta
from sqlalchemy import select

from llm_local.config import Config
from llm_local.models import Candle, get_session, init_db

logger = logging.getLogger(__name__)


def fetch_ohlcv(config: Config, engine=None, force: bool = False) -> list[Candle]:
    """Fetch OHLCV candles from Binance, caching to SQLite.

    Paginates if more than 1000 candles needed.
    Skips fetch if candles already exist in DB (unless force=True).
    """
    if engine is None:
        engine = init_db(config.db_path)

    # Calculate time range
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=config.days)
    since_ms = int(start_time.timestamp() * 1000)

    # Check cache
    if not force:
        with get_session(engine) as session:
            existing = session.execute(
                select(Candle)
                .where(Candle.symbol == config.symbol)
                .where(Candle.timeframe == config.timeframe)
                .where(Candle.timestamp_ms >= since_ms)
            ).scalars().all()

            if len(existing) > 0:
                logger.info(
                    f"Found {len(existing)} cached candles, skipping fetch. "
                    f"Use --force-fetch to re-download."
                )
                return list(existing)

    # Fetch from Binance
    exchange = ccxt.binance({"enableRateLimit": True})

    all_candles = []
    current_since = since_ms
    page = 0

    while True:
        page += 1
        logger.info(f"Fetching page {page} from Binance (since={current_since})...")

        ohlcv = exchange.fetch_ohlcv(
            symbol=config.symbol,
            timeframe=config.timeframe,
            since=current_since,
            limit=1000,
        )

        if not ohlcv:
            break

        all_candles.extend(ohlcv)
        logger.info(f"Page {page}: got {len(ohlcv)} candles (total: {len(all_candles)})")

        if len(ohlcv) < 1000:
            break

        # Next page starts after the last candle
        current_since = ohlcv[-1][0] + 1
        time.sleep(0.1)  # Extra politeness beyond ccxt rate limiting

    logger.info(f"Fetched {len(all_candles)} total candles from Binance")

    # Store in database
    with get_session(engine) as session:
        for row in all_candles:
            timestamp_ms, open_, high, low, close, volume = row

            # Skip if already exists
            existing = session.execute(
                select(Candle).where(
                    Candle.symbol == config.symbol,
                    Candle.timeframe == config.timeframe,
                    Candle.timestamp_ms == timestamp_ms,
                )
            ).scalar_one_or_none()

            if existing is None:
                candle = Candle(
                    symbol=config.symbol,
                    timeframe=config.timeframe,
                    timestamp_ms=timestamp_ms,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
                session.add(candle)

        session.flush()

    # Return all candles from DB (sorted)
    with get_session(engine) as session:
        candles = session.execute(
            select(Candle)
            .where(Candle.symbol == config.symbol)
            .where(Candle.timeframe == config.timeframe)
            .where(Candle.timestamp_ms >= since_ms)
            .order_by(Candle.timestamp_ms)
        ).scalars().all()

        # Detach from session so they're usable after session closes
        session.expunge_all()
        return list(candles)


def compute_indicators(candles: list[Candle], engine=None, db_path: str = None) -> list[Candle]:
    """Compute technical indicators using pandas-ta and update candle records.

    Computes: SMA(24), SMA(96), RSI(14), MACD(12,26,9), Bollinger Bands(20,2).
    First ~34 candles will have NaN indicators (warmup period).
    """
    if not candles:
        return candles

    # Build DataFrame from candles
    df = pd.DataFrame([
        {
            "id": c.id,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ])

    # Compute indicators
    df["sma_24"] = ta.sma(df["close"], length=24)
    df["sma_96"] = ta.sma(df["close"], length=96)
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    macd_result = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_result is not None and not macd_result.empty:
        df["macd"] = macd_result.iloc[:, 0]  # MACD line
        df["macd_signal"] = macd_result.iloc[:, 1]  # Signal line
    else:
        df["macd"] = float("nan")
        df["macd_signal"] = float("nan")

    bb_result = ta.bbands(df["close"], length=20, std=2)
    if bb_result is not None and not bb_result.empty:
        df["bb_lower"] = bb_result.iloc[:, 0]  # Lower band
        df["bb_upper"] = bb_result.iloc[:, 2]  # Upper band
    else:
        df["bb_lower"] = float("nan")
        df["bb_upper"] = float("nan")

    # Update candle objects and DB
    if engine is not None:
        with get_session(engine) as session:
            for idx, row in df.iterrows():
                candle_id = row["id"]
                session.execute(
                    Candle.__table__.update()
                    .where(Candle.id == candle_id)
                    .values(
                        sma_24=_nan_to_none(row["sma_24"]),
                        sma_96=_nan_to_none(row["sma_96"]),
                        rsi_14=_nan_to_none(row["rsi_14"]),
                        macd=_nan_to_none(row["macd"]),
                        macd_signal=_nan_to_none(row["macd_signal"]),
                        bb_upper=_nan_to_none(row["bb_upper"]),
                        bb_lower=_nan_to_none(row["bb_lower"]),
                    )
                )

    # Update in-memory candle objects
    for i, candle in enumerate(candles):
        candle.sma_24 = _nan_to_none(df.at[i, "sma_24"])
        candle.sma_96 = _nan_to_none(df.at[i, "sma_96"])
        candle.rsi_14 = _nan_to_none(df.at[i, "rsi_14"])
        candle.macd = _nan_to_none(df.at[i, "macd"])
        candle.macd_signal = _nan_to_none(df.at[i, "macd_signal"])
        candle.bb_upper = _nan_to_none(df.at[i, "bb_upper"])
        candle.bb_lower = _nan_to_none(df.at[i, "bb_lower"])

    warmup_count = sum(1 for c in candles if c.rsi_14 is None)
    logger.info(
        f"Computed indicators for {len(candles)} candles "
        f"({warmup_count} in warmup period with NaN)"
    )

    return candles


def _nan_to_none(value) -> float | None:
    """Convert NaN/inf to None for SQLite storage."""
    if value is None:
        return None
    try:
        if math.isnan(value) or math.isinf(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)
