"""Configuration loading from TOML + environment variables."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # LLM settings
    llm_url: str = "http://localhost:8080/v1"
    llm_model: str = "gemma-4-26b"
    temperature: float = 0.3
    max_tokens: int = 512
    timeout_seconds: int = 30

    # Trading settings
    symbol: str = "BTC/USDT"
    timeframe: str = "15m"
    lookback_candles: int = 24
    max_hold_candles: int = 24
    fee_rate: float = 0.001
    starting_capital: float = 10000.0

    # Data settings
    days: int = 30
    quick_candles: int = 200

    # Database
    db_path: str = "trading.db"

    # Secrets (from env only, never in TOML)
    binance_api_key: str = field(default="", repr=False)
    binance_secret: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)


def load_config(path: str = "config.toml") -> Config:
    """Load config from TOML file, override secrets from environment."""
    config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"Invalid TOML in {path}: {e}") from e

    llm = data.get("llm", {})
    trading = data.get("trading", {})
    data_section = data.get("data", {})
    database = data.get("database", {})

    config = Config(
        llm_url=llm.get("url", Config.llm_url),
        llm_model=llm.get("model", Config.llm_model),
        temperature=llm.get("temperature", Config.temperature),
        max_tokens=llm.get("max_tokens", Config.max_tokens),
        timeout_seconds=llm.get("timeout_seconds", Config.timeout_seconds),
        symbol=trading.get("symbol", Config.symbol),
        timeframe=trading.get("timeframe", Config.timeframe),
        lookback_candles=trading.get("lookback_candles", Config.lookback_candles),
        max_hold_candles=trading.get("max_hold_candles", Config.max_hold_candles),
        fee_rate=trading.get("fee_rate", Config.fee_rate),
        starting_capital=trading.get("starting_capital", Config.starting_capital),
        days=data_section.get("days", Config.days),
        quick_candles=data_section.get("quick_candles", Config.quick_candles),
        db_path=database.get("path", Config.db_path),
        binance_api_key=os.environ.get("BINANCE_API_KEY", ""),
        binance_secret=os.environ.get("BINANCE_SECRET", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    )

    return config
