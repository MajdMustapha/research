#!/usr/bin/env python3
"""Compute technical indicators from candle data.

Usage: python skills/compute_technicals.py NVDA
Reads: workspace/{DATE}/{TICKER}/candles.json
Writes: workspace/{DATE}/{TICKER}/indicators.json

Computes: RSI-14, MACD(12/26/9), Bollinger Bands(20,2), SMA 20/50/200,
EMA-20, ATR-14, OBV, Stochastic %K/%D, support/resistance, drawdown metrics,
position sizing data, and DCA score (0-100).
"""

import argparse
import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.utils import (
    read_json,
    write_json,
    load_config,
    get_position,
    compute_portfolio_value,
    error_exit,
    timestamp_now,
)

# Try ta-lib first, fall back to manual computation
try:
    import talib

    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI manually if ta-lib unavailable."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_macd(close: pd.Series, fast=12, slow=26, signal=9):
    """Compute MACD line, signal, histogram."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger(close: pd.Series, period=20, std_dev=2):
    """Compute Bollinger Bands."""
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    pct_b = (close - lower) / (upper - lower)
    return upper, sma, lower, pct_b


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14):
    """Compute Average True Range."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Compute On-Balance Volume."""
    direction = np.where(close > close.shift(1), 1, np.where(close < close.shift(1), -1, 0))
    return (volume * direction).cumsum()


def compute_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k=14, d=3):
    """Compute Stochastic %K and %D."""
    lowest_low = low.rolling(k).min()
    highest_high = high.rolling(k).max()
    pct_k = 100 * (close - lowest_low) / (highest_high - lowest_low)
    pct_d = pct_k.rolling(d).mean()
    return pct_k, pct_d


def compute_drawdown_metrics(close: pd.Series, avg_cost: float) -> dict:
    """Compute drawdown metrics for position sizing and risk management."""
    current_price = close.iloc[-1]
    pct_from_cost = (current_price - avg_cost) / avg_cost if avg_cost else 0

    high_52w = close.max()  # approximation from available data
    low_52w = close.min()
    pct_from_52w_high = (current_price - high_52w) / high_52w if high_52w else 0
    pct_from_52w_low = (current_price - low_52w) / low_52w if low_52w else 0

    # Max drawdown in the period
    rolling_max = close.cummax()
    drawdowns = (close - rolling_max) / rolling_max
    max_drawdown = drawdowns.min()

    # Current drawdown from recent peak
    recent_peak = close.max()
    current_drawdown = (current_price - recent_peak) / recent_peak if recent_peak else 0

    # Recovery: how much of the max drawdown has been recovered
    trough_idx = drawdowns.idxmin()
    if trough_idx < close.index[-1]:
        trough_price = close.loc[trough_idx]
        recovery_pct = (current_price - trough_price) / (recent_peak - trough_price) if recent_peak != trough_price else 1.0
        recovery_pct = max(0, min(1, recovery_pct))
    else:
        recovery_pct = 0.0

    return {
        "current_price": round(float(current_price), 2),
        "pct_from_cost": round(float(pct_from_cost), 4),
        "pct_from_52w_high": round(float(pct_from_52w_high), 4),
        "pct_from_52w_low": round(float(pct_from_52w_low), 4),
        "max_drawdown_period": round(float(max_drawdown), 4),
        "current_drawdown_from_peak": round(float(current_drawdown), 4),
        "drawdown_recovery_pct": round(float(recovery_pct), 4),
    }


def compute_dca_score(
    rsi: float,
    price: float,
    sma200: float,
    pct_from_cost: float,
    bb_pct_b: float,
    macd_hist: float,
    prev_macd_hist: float,
) -> tuple[int, str, list]:
    """Compute DCA score (0-100) with label and breakdown.

    Score formula from spec:
      base = 50
      RSI < 30: +20 | RSI < 40: +10 | RSI > 70: -20
      price < SMA200: +15 | price > SMA200 * 1.2: -10
      pct_from_cost < -0.20: +20 | < -0.10: +10 | > 0.05: -10
      bb_%b < 0.1: +10
      macd_hist > 0 and rising: +5

    Labels: 80+ STRONG BUY, 65+ BUY, 50+ NIBBLE, 35+ HOLD, <35 WAIT
    """
    score = 50
    breakdown = []

    # RSI component
    if rsi is not None and not np.isnan(rsi):
        if rsi < 30:
            score += 20
            breakdown.append(f"RSI {rsi:.1f} < 30: +20 (oversold)")
        elif rsi < 40:
            score += 10
            breakdown.append(f"RSI {rsi:.1f} < 40: +10 (approaching oversold)")
        elif rsi > 70:
            score -= 20
            breakdown.append(f"RSI {rsi:.1f} > 70: -20 (overbought)")
        else:
            breakdown.append(f"RSI {rsi:.1f}: neutral")

    # Price vs SMA200
    if sma200 is not None and not np.isnan(sma200) and sma200 > 0:
        if price < sma200:
            score += 15
            breakdown.append(f"Price below SMA200 ({price:.0f} < {sma200:.0f}): +15")
        elif price > sma200 * 1.2:
            score -= 10
            breakdown.append(f"Price >20% above SMA200: -10")
        else:
            breakdown.append(f"Price near SMA200: neutral")

    # Distance from cost basis
    if pct_from_cost < -0.20:
        score += 20
        breakdown.append(f"{pct_from_cost:.1%} from cost (>20% below): +20")
    elif pct_from_cost < -0.10:
        score += 10
        breakdown.append(f"{pct_from_cost:.1%} from cost (>10% below): +10")
    elif pct_from_cost > 0.05:
        score -= 10
        breakdown.append(f"{pct_from_cost:.1%} from cost (>5% above): -10")
    else:
        breakdown.append(f"{pct_from_cost:.1%} from cost: neutral")

    # Bollinger Band position
    if bb_pct_b is not None and not np.isnan(bb_pct_b):
        if bb_pct_b < 0.1:
            score += 10
            breakdown.append(f"BB %B {bb_pct_b:.2f} < 0.1: +10 (oversold in bands)")

    # MACD momentum
    if (
        macd_hist is not None
        and prev_macd_hist is not None
        and not np.isnan(macd_hist)
        and not np.isnan(prev_macd_hist)
    ):
        if macd_hist > 0 and macd_hist > prev_macd_hist:
            score += 5
            breakdown.append("MACD histogram positive & rising: +5")

    # Clamp
    score = max(0, min(100, score))

    # Label
    if score >= 80:
        label = "STRONG BUY"
    elif score >= 65:
        label = "BUY"
    elif score >= 50:
        label = "NIBBLE"
    elif score >= 35:
        label = "HOLD"
    else:
        label = "WAIT"

    return score, label, breakdown


def compute_suggested_allocation(dca_score: int, position_weight_pct: float, config: dict) -> dict:
    """Compute suggested EUR allocation based on DCA score and position weight.

    Sizing tiers:
      DCA score 80+ AND position < 20%: full €200
      DCA score 65-79 AND position < 25%: €150
      DCA score 50-64: €100 (nibble)
      DCA score < 50 OR position > 25%: €0 (wait)
      Position > 30%: HARD VETO regardless of score
    """
    max_single = config.get("risk_rules", {}).get("max_single_add_eur", 200)
    max_position_pct = config.get("risk_rules", {}).get("max_position_pct", 0.30) * 100

    if position_weight_pct > max_position_pct:
        return {
            "suggested_eur": 0,
            "reason": f"VETO — position weight {position_weight_pct:.1f}% exceeds {max_position_pct:.0f}% cap",
            "veto": True,
        }

    if position_weight_pct > 25:
        return {
            "suggested_eur": 0,
            "reason": f"Position weight {position_weight_pct:.1f}% > 25% — wait for rebalance",
            "veto": False,
        }

    if dca_score >= 80 and position_weight_pct < 20:
        amount = min(200, max_single)
        reason = f"STRONG BUY zone (score {dca_score}), position {position_weight_pct:.1f}% — full allocation"
    elif dca_score >= 65 and position_weight_pct < 25:
        amount = min(150, max_single)
        reason = f"BUY zone (score {dca_score}), position {position_weight_pct:.1f}%"
    elif dca_score >= 50:
        amount = min(100, max_single)
        reason = f"NIBBLE zone (score {dca_score})"
    else:
        amount = 0
        reason = f"Score {dca_score} below nibble threshold — wait"

    return {"suggested_eur": amount, "reason": reason, "veto": False}


def main():
    parser = argparse.ArgumentParser(description="Compute technical indicators")
    parser.add_argument("ticker", help="Stock ticker symbol")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    # Read candle data
    try:
        candles = read_json("candles.json", ticker)
    except FileNotFoundError:
        error_exit(f"candles.json not found for {ticker}. Run fetch_candles.py first.")

    if candles.get("s") != "ok" or not candles.get("c"):
        error_exit(f"Invalid candle data for {ticker}")

    # Build DataFrame
    df = pd.DataFrame(
        {
            "open": candles["o"],
            "high": candles["h"],
            "low": candles["l"],
            "close": candles["c"],
            "volume": candles["v"],
            "timestamp": candles["t"],
        }
    )
    df["date"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # Compute indicators using ta-lib if available, else manual
    if HAS_TALIB:
        rsi = talib.RSI(close.values, timeperiod=14)
        macd_line, macd_signal, macd_hist = talib.MACD(
            close.values, fastperiod=12, slowperiod=26, signalperiod=9
        )
        bb_upper, bb_mid, bb_lower = talib.BBANDS(
            close.values, timeperiod=20, nbdevup=2, nbdevdn=2
        )
        sma20 = talib.SMA(close.values, timeperiod=20)
        sma50 = talib.SMA(close.values, timeperiod=50)
        sma200 = talib.SMA(close.values, timeperiod=200)
        ema20 = talib.EMA(close.values, timeperiod=20)
        atr = talib.ATR(high.values, low.values, close.values, timeperiod=14)
        stoch_k, stoch_d = talib.STOCH(
            high.values, low.values, close.values, fastk_period=14, slowk_period=3, slowd_period=3
        )
        obv = talib.OBV(close.values, volume.values.astype(float))

        # Convert to get latest values
        rsi_val = float(rsi[-1]) if not np.isnan(rsi[-1]) else None
        macd_line_val = float(macd_line[-1]) if not np.isnan(macd_line[-1]) else None
        macd_signal_val = float(macd_signal[-1]) if not np.isnan(macd_signal[-1]) else None
        macd_hist_val = float(macd_hist[-1]) if not np.isnan(macd_hist[-1]) else None
        prev_macd_hist_val = float(macd_hist[-2]) if len(macd_hist) > 1 and not np.isnan(macd_hist[-2]) else None
        bb_upper_val = float(bb_upper[-1]) if not np.isnan(bb_upper[-1]) else None
        bb_mid_val = float(bb_mid[-1]) if not np.isnan(bb_mid[-1]) else None
        bb_lower_val = float(bb_lower[-1]) if not np.isnan(bb_lower[-1]) else None
        bb_pct_b_val = (float(close.iloc[-1]) - float(bb_lower[-1])) / (float(bb_upper[-1]) - float(bb_lower[-1])) if bb_upper_val and bb_lower_val and bb_upper_val != bb_lower_val else None
        sma20_val = float(sma20[-1]) if not np.isnan(sma20[-1]) else None
        sma50_val = float(sma50[-1]) if not np.isnan(sma50[-1]) else None
        sma200_val = float(sma200[-1]) if len(sma200) > 0 and not np.isnan(sma200[-1]) else None
        ema20_val = float(ema20[-1]) if not np.isnan(ema20[-1]) else None
        atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else None
        stoch_k_val = float(stoch_k[-1]) if not np.isnan(stoch_k[-1]) else None
        stoch_d_val = float(stoch_d[-1]) if not np.isnan(stoch_d[-1]) else None
        obv_val = float(obv[-1])
    else:
        # Manual computation using finta/pandas
        rsi_series = compute_rsi(close, 14)
        macd_line_s, macd_signal_s, macd_hist_s = compute_macd(close)
        bb_upper_s, bb_mid_s, bb_lower_s, bb_pct_b_s = compute_bollinger(close)
        atr_series = compute_atr(high, low, close)
        obv_series = compute_obv(close, volume)
        stoch_k_s, stoch_d_s = compute_stochastic(high, low, close)

        def safe_last(s):
            v = s.iloc[-1] if len(s) > 0 else None
            return round(float(v), 4) if v is not None and not np.isnan(v) else None

        rsi_val = safe_last(rsi_series)
        macd_line_val = safe_last(macd_line_s)
        macd_signal_val = safe_last(macd_signal_s)
        macd_hist_val = safe_last(macd_hist_s)
        prev_macd_hist_val = safe_last(macd_hist_s.iloc[:-1]) if len(macd_hist_s) > 1 else None
        bb_upper_val = safe_last(bb_upper_s)
        bb_mid_val = safe_last(bb_mid_s)
        bb_lower_val = safe_last(bb_lower_s)
        bb_pct_b_val = safe_last(bb_pct_b_s)
        sma20_val = safe_last(close.rolling(20).mean())
        sma50_val = safe_last(close.rolling(50).mean())
        sma200_val = safe_last(close.rolling(200).mean())
        ema20_val = safe_last(close.ewm(span=20, adjust=False).mean())
        atr_val = safe_last(atr_series)
        stoch_k_val = safe_last(stoch_k_s)
        stoch_d_val = safe_last(stoch_d_s)
        obv_val = safe_last(obv_series)

    # Support / Resistance (rolling 20-bar min/max)
    support = float(low.rolling(20).min().iloc[-1]) if len(low) >= 20 else float(low.min())
    resistance = float(high.rolling(20).max().iloc[-1]) if len(high) >= 20 else float(high.max())

    # Trend classification
    current_price = float(close.iloc[-1])
    trend_signals = []
    if sma20_val and current_price > sma20_val:
        trend_signals.append("above_sma20")
    if sma50_val and current_price > sma50_val:
        trend_signals.append("above_sma50")
    if sma200_val and current_price > sma200_val:
        trend_signals.append("above_sma200")

    if len(trend_signals) == 3:
        trend = "strong uptrend"
    elif len(trend_signals) >= 2:
        trend = "uptrend"
    elif len(trend_signals) == 1:
        trend = "mixed"
    else:
        trend = "downtrend"

    # Drawdown metrics
    position = get_position(ticker)
    avg_cost = position.get("avg_cost", current_price)
    drawdown = compute_drawdown_metrics(close, avg_cost)

    # Portfolio-level position sizing data
    config = load_config()
    shares = position.get("shares", 0)
    position_value = shares * current_price
    # Estimate total portfolio value using this ticker's price and cost for others
    positions_config = config.get("portfolio", {}).get("positions", {})
    total_value_estimate = sum(
        p.get("shares", 0) * p.get("avg_cost", 0) for t, p in positions_config.items() if t != ticker
    ) + position_value
    position_weight_pct = (position_value / total_value_estimate * 100) if total_value_estimate > 0 else 0

    # SMA200 % distance
    price_vs_sma200_pct = ((current_price - sma200_val) / sma200_val * 100) if sma200_val and sma200_val > 0 else None

    # DCA Score
    dca_score, dca_label, dca_breakdown = compute_dca_score(
        rsi=rsi_val,
        price=current_price,
        sma200=sma200_val,
        pct_from_cost=drawdown["pct_from_cost"],
        bb_pct_b=bb_pct_b_val,
        macd_hist=macd_hist_val,
        prev_macd_hist=prev_macd_hist_val,
    )

    # Suggested allocation
    allocation = compute_suggested_allocation(dca_score, position_weight_pct, config)

    # Build output
    indicators = {
        "ticker": ticker,
        "timestamp": timestamp_now(),
        "current_price": current_price,
        # Trend
        "trend": trend,
        "trend_signals": trend_signals,
        # Moving Averages
        "sma_20": round(sma20_val, 2) if sma20_val else None,
        "sma_50": round(sma50_val, 2) if sma50_val else None,
        "sma_200": round(sma200_val, 2) if sma200_val else None,
        "ema_20": round(ema20_val, 2) if ema20_val else None,
        "price_vs_sma200_pct": round(price_vs_sma200_pct, 2) if price_vs_sma200_pct else None,
        # RSI
        "rsi_14": round(rsi_val, 2) if rsi_val else None,
        # MACD
        "macd_line": round(macd_line_val, 4) if macd_line_val else None,
        "macd_signal": round(macd_signal_val, 4) if macd_signal_val else None,
        "macd_histogram": round(macd_hist_val, 4) if macd_hist_val else None,
        # Bollinger Bands
        "bb_upper": round(bb_upper_val, 2) if bb_upper_val else None,
        "bb_middle": round(bb_mid_val, 2) if bb_mid_val else None,
        "bb_lower": round(bb_lower_val, 2) if bb_lower_val else None,
        "bb_pct_b": round(bb_pct_b_val, 4) if bb_pct_b_val else None,
        # Volatility
        "atr_14": round(atr_val, 2) if atr_val else None,
        # Stochastic
        "stoch_k": round(stoch_k_val, 2) if stoch_k_val else None,
        "stoch_d": round(stoch_d_val, 2) if stoch_d_val else None,
        # Volume
        "obv": round(obv_val, 0) if obv_val else None,
        # Support / Resistance
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        # Drawdown & Position Sizing
        "drawdown": drawdown,
        "position_value": round(position_value, 2),
        "position_weight_pct": round(position_weight_pct, 2),
        "suggested_allocation": allocation,
        # DCA Score
        "dca_score": dca_score,
        "dca_label": dca_label,
        "dca_breakdown": dca_breakdown,
    }

    write_json(indicators, "indicators.json", ticker)


if __name__ == "__main__":
    main()
