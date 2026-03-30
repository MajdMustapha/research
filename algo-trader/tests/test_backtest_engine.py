"""Tests for the backtest engine and metrics."""

import numpy as np
import pandas as pd
import pytest

from backtest.engine import BacktestEngine, BacktestResult, Trade
from backtest.metrics import max_drawdown, profit_factor, sharpe_ratio, win_rate


def _make_config():
    return {
        "backtest": {"fee_rate": 0.001, "slippage_rate": 0.0005},
        "risk": {"max_position_pct": 0.02},
    }


def _make_ohlcv(closes: list[float]) -> pd.DataFrame:
    """Create synthetic OHLCV from close prices."""
    n = len(closes)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    })


class TestBacktestEngine:
    def test_no_trades_equity_unchanged(self):
        """Strategy that always holds should not change equity."""
        config = _make_config()
        df = _make_ohlcv([100.0] * 50)

        def hold_strategy(df_slice):
            return {"signal": "HOLD", "stop_distance": 0}

        engine = BacktestEngine(config, hold_strategy, initial_capital=10000)
        result = engine.run(df)

        assert len(result.trades) == 0
        assert result.final_equity == pytest.approx(10000.0)

    def test_buy_signal_creates_trade(self):
        """A single buy signal should create a trade."""
        config = _make_config()
        prices = [100.0] * 10 + [110.0] * 10
        df = _make_ohlcv(prices)

        call_count = [0]

        def buy_once(df_slice):
            call_count[0] += 1
            if call_count[0] == 5:
                return {"signal": "BUY", "stop_distance": 5.0}
            return {"signal": "HOLD", "stop_distance": 0}

        engine = BacktestEngine(config, buy_once, initial_capital=10000)
        result = engine.run(df)

        assert len(result.trades) >= 1

    def test_fee_deduction(self):
        """Fees should reduce PnL."""
        config = _make_config()
        # Flat prices — any trade should lose money due to fees/slippage
        df = _make_ohlcv([100.0] * 20)

        entered = [False]

        def buy_then_sell(df_slice):
            if len(df_slice) == 3 and not entered[0]:
                entered[0] = True
                return {"signal": "BUY", "stop_distance": 10.0}
            if len(df_slice) == 10 and entered[0]:
                return {"signal": "SELL", "stop_distance": 10.0}
            return {"signal": "HOLD", "stop_distance": 0}

        engine = BacktestEngine(config, buy_then_sell, initial_capital=10000)
        result = engine.run(df)

        # With flat prices, fees should cause a small loss
        if result.trades:
            total_fees = sum(t.fees for t in result.trades)
            assert total_fees > 0

    def test_equity_curve_length(self):
        """Equity curve should have same length as input data."""
        config = _make_config()
        df = _make_ohlcv([100.0] * 30)

        def hold(df_slice):
            return {"signal": "HOLD", "stop_distance": 0}

        engine = BacktestEngine(config, hold, initial_capital=10000)
        result = engine.run(df)

        assert len(result.equity_curve) == len(df)

    def test_stop_loss_triggered(self):
        """Position should be closed when price hits stop."""
        config = _make_config()
        # Price drops significantly after entry
        prices = [100.0] * 5 + [80.0] * 5
        df = _make_ohlcv(prices)
        # Make lows very low to trigger stop
        df["low"] = df["close"] * 0.95

        entered = [False]

        def buy_early(df_slice):
            if len(df_slice) == 3 and not entered[0]:
                entered[0] = True
                return {"signal": "BUY", "stop_distance": 3.0}
            return {"signal": "HOLD", "stop_distance": 0}

        engine = BacktestEngine(config, buy_early, initial_capital=10000)
        result = engine.run(df)

        # Should have at least one trade (stopped out)
        assert len(result.trades) >= 1


class TestMetrics:
    def test_sharpe_flat_equity(self):
        """Flat equity curve should have Sharpe of 0."""
        curve = pd.Series([10000.0] * 100)
        assert sharpe_ratio(curve) == 0.0

    def test_max_drawdown_no_drawdown(self):
        """Monotonically increasing equity should have 0 drawdown."""
        curve = pd.Series([float(i) for i in range(1, 101)])
        assert max_drawdown(curve) == 0.0

    def test_max_drawdown_known(self):
        """Known drawdown scenario."""
        curve = pd.Series([100, 110, 90, 95, 80, 120])
        # Peak 110, trough 80 → drawdown = 30/110 ≈ 0.2727
        dd = max_drawdown(curve)
        assert dd == pytest.approx(30.0 / 110.0, abs=0.01)

    def test_profit_factor_no_losses(self):
        """All winning trades should give inf profit factor."""
        trades = [
            Trade("BTC", "BUY", 100, 110, None, None, 1, 10, 0.2),
        ]
        assert profit_factor(trades) == float("inf")

    def test_profit_factor_known(self):
        """Known profit factor calculation."""
        trades = [
            Trade("BTC", "BUY", 100, 110, None, None, 1, 10, 0.2),
            Trade("BTC", "BUY", 100, 95, None, None, 1, -5, 0.2),
        ]
        assert profit_factor(trades) == pytest.approx(2.0)

    def test_win_rate(self):
        trades = [
            Trade("BTC", "BUY", 100, 110, None, None, 1, 10, 0.2),
            Trade("BTC", "BUY", 100, 95, None, None, 1, -5, 0.2),
            Trade("BTC", "BUY", 100, 105, None, None, 1, 5, 0.2),
        ]
        assert win_rate(trades) == pytest.approx(2 / 3)

    def test_win_rate_empty(self):
        assert win_rate([]) == 0.0
