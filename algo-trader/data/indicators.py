"""
Pure indicator functions for technical analysis.
All functions are stateless, vectorized with pandas/numpy, and NaN-safe.
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing.
    Returns values in range 0-100.
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Wilder's smoothing (equivalent to EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    result = 100.0 - (100.0 / (1.0 + rs))

    # Handle division by zero (all gains, no losses)
    result = result.where(avg_loss != 0, 100.0)

    return result


def bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Bollinger Bands.
    Returns (upper, middle, lower) bands.
    """
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Average True Range using Wilder's smoothing.
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return true_range.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.DataFrame:
    """
    Average Directional Index using Wilder's smoothing.
    Returns DataFrame with columns: adx, plus_di, minus_di.
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    # Directional movement
    plus_dm = high - prev_high
    minus_dm = prev_low - low

    # Only keep positive values where one is larger than the other
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    # True range
    atr_series = atr(high, low, close, period)

    # Smoothed directional movement using Wilder's smoothing
    smooth_plus_dm = plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smooth_minus_dm = minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # Directional indicators
    plus_di = 100.0 * smooth_plus_dm / atr_series
    minus_di = 100.0 * smooth_minus_dm / atr_series

    # Directional index
    di_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum.where(di_sum != 0, np.nan)

    # ADX is smoothed DX
    adx_series = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return pd.DataFrame({
        "adx": adx_series,
        "plus_di": plus_di,
        "minus_di": minus_di,
    })


def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Simple Moving Average of volume."""
    return sma(volume, period)
