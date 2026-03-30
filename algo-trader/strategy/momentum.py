"""Momentum strategy: EMA crossover with volume confirmation."""

import pandas as pd

from data.indicators import ema, volume_sma


class MomentumStrategy:
    """
    Generates BUY/SELL signals based on EMA crossover + volume filter.
    - BUY: EMA(fast) crosses above EMA(slow) AND volume > multiplier * volume_SMA
    - SELL: EMA(fast) crosses below EMA(slow) AND volume > multiplier * volume_SMA
    """

    def __init__(self, config: dict):
        mom_cfg = config["strategy"]["momentum"]
        self.ema_fast_period = mom_cfg["ema_fast"]
        self.ema_slow_period = mom_cfg["ema_slow"]
        self.volume_sma_period = mom_cfg["volume_sma_period"]
        self.volume_multiplier = mom_cfg["volume_multiplier"]

    def signal(self, df: pd.DataFrame) -> str:
        """Return BUY, SELL, or HOLD for the latest bar."""
        if len(df) < self.ema_slow_period + 2:
            return "HOLD"

        ema_fast = ema(df["close"], self.ema_fast_period)
        ema_slow = ema(df["close"], self.ema_slow_period)
        vol_avg = volume_sma(df["volume"], self.volume_sma_period)

        curr_fast = ema_fast.iloc[-1]
        prev_fast = ema_fast.iloc[-2]
        curr_slow = ema_slow.iloc[-1]
        prev_slow = ema_slow.iloc[-2]
        curr_vol = df["volume"].iloc[-1]
        curr_vol_avg = vol_avg.iloc[-1]

        if pd.isna(curr_fast) or pd.isna(curr_slow) or pd.isna(curr_vol_avg):
            return "HOLD"

        volume_confirmed = curr_vol > self.volume_multiplier * curr_vol_avg

        # Bullish crossover: fast crosses above slow
        if prev_fast <= prev_slow and curr_fast > curr_slow and volume_confirmed:
            return "BUY"

        # Bearish crossover: fast crosses below slow
        if prev_fast >= prev_slow and curr_fast < curr_slow and volume_confirmed:
            return "SELL"

        return "HOLD"
