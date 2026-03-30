"""Mean reversion strategy: RSI + Bollinger Bands."""

import pandas as pd

from data.indicators import bollinger_bands, rsi


class MeanReversionStrategy:
    """
    Generates BUY/SELL signals based on RSI extremes + Bollinger Band touches.
    - BUY: RSI < oversold AND price <= lower BB
    - SELL: RSI > overbought AND price >= upper BB
    """

    def __init__(self, config: dict):
        mr_cfg = config["strategy"]["mean_reversion"]
        self.rsi_period = mr_cfg["rsi_period"]
        self.rsi_oversold = mr_cfg["rsi_oversold"]
        self.rsi_overbought = mr_cfg["rsi_overbought"]
        self.bb_period = mr_cfg["bb_period"]
        self.bb_std = mr_cfg["bb_std"]

    def signal(self, df: pd.DataFrame) -> str:
        """Return BUY, SELL, or HOLD for the latest bar."""
        if len(df) < max(self.rsi_period, self.bb_period) + 2:
            return "HOLD"

        rsi_values = rsi(df["close"], self.rsi_period)
        upper, middle, lower = bollinger_bands(df["close"], self.bb_period, self.bb_std)

        curr_rsi = rsi_values.iloc[-1]
        curr_close = df["close"].iloc[-1]
        curr_upper = upper.iloc[-1]
        curr_lower = lower.iloc[-1]

        if pd.isna(curr_rsi) or pd.isna(curr_upper) or pd.isna(curr_lower):
            return "HOLD"

        # Oversold + at lower band → BUY
        if curr_rsi < self.rsi_oversold and curr_close <= curr_lower:
            return "BUY"

        # Overbought + at upper band → SELL
        if curr_rsi > self.rsi_overbought and curr_close >= curr_upper:
            return "SELL"

        return "HOLD"
