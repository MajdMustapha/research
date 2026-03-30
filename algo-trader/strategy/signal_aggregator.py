"""Signal aggregator: routes to correct strategy based on market regime."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from data.indicators import atr
from strategy.mean_reversion import MeanReversionStrategy
from strategy.momentum import MomentumStrategy
from strategy.regime_detector import RegimeDetector

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    direction: str  # BUY, SELL, HOLD
    regime: str  # TRENDING, RANGING, NEUTRAL
    stop_distance: float
    timestamp: datetime | None = None


class SignalAggregator:
    """
    Main signal routing logic:
    1. Detect regime via ADX
    2. TRENDING → delegate to MomentumStrategy
    3. RANGING → delegate to MeanReversionStrategy
    4. NEUTRAL → HOLD (no new trades)
    """

    def __init__(self, config: dict):
        self.config = config
        self.regime_detector = RegimeDetector(config)
        self.momentum = MomentumStrategy(config)
        self.mean_reversion = MeanReversionStrategy(config)
        self.atr_period = config["risk"]["atr_period"]
        self.atr_stop_multiplier = config["risk"]["atr_stop_multiplier"]

    def get_signal(self, df: pd.DataFrame) -> Signal:
        """Get trading signal for the latest bar."""
        regime = self.regime_detector.detect(df)

        if regime == RegimeDetector.TRENDING:
            direction = self.momentum.signal(df)
        elif regime == RegimeDetector.RANGING:
            direction = self.mean_reversion.signal(df)
        else:
            direction = "HOLD"

        # Calculate stop distance from ATR
        stop_distance = 0.0
        if direction != "HOLD" and len(df) >= self.atr_period + 1:
            atr_values = atr(df["high"], df["low"], df["close"], self.atr_period)
            latest_atr = atr_values.iloc[-1]
            if not pd.isna(latest_atr):
                stop_distance = latest_atr * self.atr_stop_multiplier

        timestamp = df["timestamp"].iloc[-1] if "timestamp" in df.columns else None

        signal = Signal(
            direction=direction,
            regime=regime,
            stop_distance=stop_distance,
            timestamp=timestamp,
        )

        if direction != "HOLD":
            logger.info(f"Signal: {direction} | Regime: {regime} | Stop: {stop_distance:.2f}")

        return signal

    def as_strategy_fn(self):
        """Return a callable compatible with BacktestEngine."""
        def strategy_fn(df: pd.DataFrame) -> dict:
            signal = self.get_signal(df)
            return {
                "signal": signal.direction,
                "stop_distance": signal.stop_distance,
            }
        return strategy_fn
