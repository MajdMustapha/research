"""Tests for indicator functions."""

import numpy as np
import pandas as pd
import pytest

from data.indicators import (
    adx,
    atr,
    bollinger_bands,
    ema,
    rsi,
    sma,
    volume_sma,
)


def _make_series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def _make_trending_up(n: int = 200, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """Generate strongly trending upward OHLCV data."""
    close = np.array([start + i * step for i in range(n)])
    high = close + np.random.default_rng(42).uniform(0.5, 2.0, n)
    low = close - np.random.default_rng(43).uniform(0.5, 2.0, n)
    open_ = close - np.random.default_rng(44).uniform(-0.5, 0.5, n)
    volume = np.random.default_rng(45).uniform(100, 1000, n)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume
    })


def _make_random(n: int = 200) -> pd.DataFrame:
    """Generate random OHLCV data."""
    rng = np.random.default_rng(42)
    close = 100.0 + rng.standard_normal(n).cumsum()
    high = close + rng.uniform(0.5, 3.0, n)
    low = close - rng.uniform(0.5, 3.0, n)
    open_ = close + rng.uniform(-1.0, 1.0, n)
    volume = rng.uniform(100, 1000, n)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume
    })


# --- SMA ---

class TestSMA:
    def test_known_values(self):
        s = _make_series([1, 2, 3, 4, 5])
        result = sma(s, 3)
        assert np.isnan(result.iloc[0])
        assert np.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_period_one(self):
        s = _make_series([10, 20, 30])
        result = sma(s, 1)
        pd.testing.assert_series_equal(result, s)


# --- EMA ---

class TestEMA:
    def test_first_value(self):
        s = _make_series([1, 2, 3, 4, 5])
        result = ema(s, 3)
        # EMA starts from the first value
        assert result.iloc[0] == pytest.approx(1.0)

    def test_period_one(self):
        s = _make_series([10, 20, 30])
        result = ema(s, 1)
        pd.testing.assert_series_equal(result, s)

    def test_smoothing(self):
        s = _make_series([10, 20, 10, 20, 10])
        result = ema(s, 3)
        # EMA should be smoother than raw values
        assert result.std() < s.std()


# --- RSI ---

class TestRSI:
    def test_range(self):
        df = _make_random()
        result = rsi(df["close"], 14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_overbought_on_rising(self):
        # Consistently rising prices
        s = _make_series([float(i) for i in range(50)])
        result = rsi(s, 14)
        # Last RSI values should be very high
        assert result.iloc[-1] > 90

    def test_oversold_on_falling(self):
        # Consistently falling prices
        s = _make_series([float(50 - i) for i in range(50)])
        result = rsi(s, 14)
        assert result.iloc[-1] < 10


# --- Bollinger Bands ---

class TestBollingerBands:
    def test_middle_equals_sma(self):
        s = _make_series([10, 20, 30, 40, 50, 60, 70])
        upper, middle, lower = bollinger_bands(s, period=3, std_dev=2.0)
        expected_sma = sma(s, 3)
        pd.testing.assert_series_equal(middle, expected_sma)

    def test_upper_above_lower(self):
        df = _make_random()
        upper, middle, lower = bollinger_bands(df["close"], 20, 2.0)
        valid_mask = upper.notna() & lower.notna()
        assert (upper[valid_mask] >= lower[valid_mask]).all()

    def test_band_width(self):
        s = _make_series([10, 20, 30, 40, 50, 60, 70])
        upper, middle, lower = bollinger_bands(s, period=3, std_dev=2.0)
        std = s.rolling(3).std()
        expected_width = 4.0 * std
        actual_width = upper - lower
        pd.testing.assert_series_equal(actual_width, expected_width)


# --- ATR ---

class TestATR:
    def test_positive(self):
        df = _make_random()
        result = atr(df["high"], df["low"], df["close"], 14)
        valid = result.dropna()
        assert (valid > 0).all()

    def test_constant_prices_low_atr(self):
        n = 50
        close = pd.Series([100.0] * n)
        high = pd.Series([101.0] * n)
        low = pd.Series([99.0] * n)
        result = atr(high, low, close, 14)
        # ATR should converge to 2.0 (high - low = 2)
        assert result.iloc[-1] == pytest.approx(2.0, abs=0.1)


# --- ADX ---

class TestADX:
    def test_returns_dataframe(self):
        df = _make_random()
        result = adx(df["high"], df["low"], df["close"], 14)
        assert isinstance(result, pd.DataFrame)
        assert "adx" in result.columns
        assert "plus_di" in result.columns
        assert "minus_di" in result.columns

    def test_adx_range(self):
        df = _make_random(300)
        result = adx(df["high"], df["low"], df["close"], 14)
        valid = result["adx"].dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_trending_data_high_adx(self):
        df = _make_trending_up(200)
        result = adx(df["high"], df["low"], df["close"], 14)
        # Strong trend should produce ADX > 25
        assert result["adx"].iloc[-1] > 25


# --- Volume SMA ---

class TestVolumeSMA:
    def test_equals_sma(self):
        df = _make_random()
        result = volume_sma(df["volume"], 20)
        expected = sma(df["volume"], 20)
        pd.testing.assert_series_equal(result, expected)
