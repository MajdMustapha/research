"""Regime detection using ADX indicator."""

import pandas as pd

from data.indicators import adx


class RegimeDetector:
    """
    Detects market regime based on ADX:
    - ADX > trending_threshold → TRENDING
    - ADX < ranging_threshold → RANGING
    - Otherwise → NEUTRAL
    """

    TRENDING = "TRENDING"
    RANGING = "RANGING"
    NEUTRAL = "NEUTRAL"

    def __init__(self, config: dict):
        regime_cfg = config["strategy"]["regime"]
        self.adx_period = regime_cfg["adx_period"]
        self.trending_threshold = regime_cfg["adx_trending_threshold"]
        self.ranging_threshold = regime_cfg["adx_ranging_threshold"]

    def detect(self, df: pd.DataFrame) -> str:
        """Detect regime for the latest bar."""
        adx_df = adx(df["high"], df["low"], df["close"], self.adx_period)
        latest_adx = adx_df["adx"].iloc[-1]

        if pd.isna(latest_adx):
            return self.NEUTRAL

        if latest_adx > self.trending_threshold:
            return self.TRENDING
        elif latest_adx < self.ranging_threshold:
            return self.RANGING
        else:
            return self.NEUTRAL

    def detect_series(self, df: pd.DataFrame) -> pd.Series:
        """Detect regime for every bar (for backtesting)."""
        adx_df = adx(df["high"], df["low"], df["close"], self.adx_period)
        adx_values = adx_df["adx"]

        def classify(val):
            if pd.isna(val):
                return self.NEUTRAL
            if val > self.trending_threshold:
                return self.TRENDING
            elif val < self.ranging_threshold:
                return self.RANGING
            return self.NEUTRAL

        return adx_values.apply(classify)
