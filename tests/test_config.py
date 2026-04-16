"""Tests for config loading and validation."""

import os
import pytest

from llm_local.config import Config, load_config


class TestConfig:
    def test_default_values(self):
        """Config dataclass has sane defaults."""
        c = Config()
        assert c.llm_url == "http://localhost:8080/v1"
        assert c.symbol == "BTC/USDT"
        assert c.timeframe == "15m"
        assert c.fee_rate == 0.001
        assert c.lookback_candles == 24
        assert c.max_hold_candles == 24
        assert c.starting_capital == 10000.0

    def test_secrets_not_in_repr(self):
        """API keys should not appear in repr."""
        c = Config(binance_api_key="secret123")
        assert "secret123" not in repr(c)


class TestLoadConfig:
    def test_load_valid_toml(self, config_toml):
        """Load a valid TOML config file."""
        c = load_config(config_toml)
        assert c.llm_url == "http://localhost:8080/v1"
        assert c.llm_model == "test-model"
        assert c.symbol == "BTC/USDT"
        assert c.fee_rate == 0.001

    def test_missing_config_file(self, tmp_path):
        """FileNotFoundError for missing config."""
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nonexistent.toml"))

    def test_invalid_toml(self, tmp_path):
        """ValueError for malformed TOML."""
        bad_toml = tmp_path / "bad.toml"
        bad_toml.write_text("this is not valid toml ][}{")
        with pytest.raises(ValueError, match="Invalid TOML"):
            load_config(str(bad_toml))

    def test_missing_sections_use_defaults(self, tmp_path):
        """Config with empty TOML uses all defaults."""
        empty_toml = tmp_path / "empty.toml"
        empty_toml.write_text("")
        c = load_config(str(empty_toml))
        assert c.llm_url == "http://localhost:8080/v1"
        assert c.symbol == "BTC/USDT"

    def test_env_var_override(self, config_toml, monkeypatch):
        """Secrets come from environment variables."""
        monkeypatch.setenv("BINANCE_API_KEY", "test-key-123")
        monkeypatch.setenv("BINANCE_SECRET", "test-secret-456")
        c = load_config(config_toml)
        assert c.binance_api_key == "test-key-123"
        assert c.binance_secret == "test-secret-456"
