"""Tests for risk management modules."""

import pytest

from risk.circuit_breaker import CircuitBreaker, State
from risk.position_sizer import PositionSizer
from risk.risk_manager import RiskManager


def _make_config():
    return {
        "risk": {
            "max_position_pct": 0.02,
            "atr_period": 14,
            "atr_stop_multiplier": 1.5,
            "daily_loss_limit_pct": 0.05,
            "max_drawdown_pct": 0.15,
        }
    }


class TestPositionSizer:
    def test_basic_sizing(self):
        """2% of 10000 with known stop distance."""
        sizer = PositionSizer(_make_config())
        # Risk = 10000 * 0.02 = 200, stop distance = 5, size = 200/5 = 40
        size = sizer.calculate(equity=10000, entry_price=100, stop_price=95)
        assert size == pytest.approx(40.0)

    def test_caps_at_equity(self):
        """Size should not exceed equity / entry_price."""
        sizer = PositionSizer(_make_config())
        # Risk = 10000 * 0.02 = 200, stop distance = 0.01, size = 20000
        # But max_size = 10000 / 100 = 100
        size = sizer.calculate(equity=10000, entry_price=100, stop_price=99.99)
        assert size <= 10000 / 100

    def test_zero_stop_distance(self):
        sizer = PositionSizer(_make_config())
        size = sizer.calculate(equity=10000, entry_price=100, stop_price=100)
        assert size == 0.0


class TestRiskManager:
    def test_within_limit(self):
        rm = RiskManager(_make_config())
        rm.reset_daily(10000)
        assert rm.check_daily_loss(9600) is True  # -4% < -5%

    def test_exceeds_limit(self):
        rm = RiskManager(_make_config())
        rm.reset_daily(10000)
        assert rm.check_daily_loss(9400) is False  # -6% > -5%

    def test_at_limit(self):
        rm = RiskManager(_make_config())
        rm.reset_daily(10000)
        # Exactly -5% should still be within limit (not strictly less)
        assert rm.check_daily_loss(9500) is False  # -5% is not > -5%

    def test_daily_pnl_pct(self):
        rm = RiskManager(_make_config())
        rm.reset_daily(10000)
        assert rm.daily_pnl_pct(9800) == pytest.approx(-0.02)
        assert rm.daily_pnl_pct(10500) == pytest.approx(0.05)


class TestCircuitBreaker:
    def test_initial_state_active(self):
        cb = CircuitBreaker(_make_config())
        assert cb.state == State.ACTIVE
        assert cb.can_trade() is True

    def test_daily_limit_triggered(self):
        cb = CircuitBreaker(_make_config())
        cb.update(current_equity=9400, peak_equity=10000, daily_pnl_pct=-0.06)
        assert cb.state == State.DAILY_LIMIT
        assert cb.can_trade() is False

    def test_daily_limit_auto_resets(self):
        cb = CircuitBreaker(_make_config())
        cb.update(current_equity=9400, peak_equity=10000, daily_pnl_pct=-0.06)
        assert cb.state == State.DAILY_LIMIT

        # New day — daily_pnl_pct resets to 0
        cb.update(current_equity=9400, peak_equity=10000, daily_pnl_pct=0.0)
        assert cb.state == State.ACTIVE

    def test_drawdown_triggered(self):
        cb = CircuitBreaker(_make_config())
        # Peak was 10000, current is 8400 → 16% drawdown > 15%
        cb.update(current_equity=8400, peak_equity=10000, daily_pnl_pct=-0.02)
        assert cb.state == State.DRAWDOWN
        assert cb.can_trade() is False

    def test_drawdown_requires_manual_resume(self):
        cb = CircuitBreaker(_make_config())
        cb.update(current_equity=8400, peak_equity=10000, daily_pnl_pct=-0.02)
        assert cb.state == State.DRAWDOWN

        # Price recovers but state should NOT auto-clear
        cb.update(current_equity=9500, peak_equity=10000, daily_pnl_pct=0.0)
        assert cb.state == State.DRAWDOWN

        # Manual resume clears it
        cb.resume()
        assert cb.state == State.ACTIVE

    def test_manual_halt(self):
        cb = CircuitBreaker(_make_config())
        cb.halt()
        assert cb.state == State.MANUAL_HALT
        assert cb.can_trade() is False

        cb.resume()
        assert cb.state == State.ACTIVE
        assert cb.can_trade() is True

    def test_manual_halt_overrides_other_states(self):
        cb = CircuitBreaker(_make_config())
        cb.update(current_equity=9400, peak_equity=10000, daily_pnl_pct=-0.06)
        assert cb.state == State.DAILY_LIMIT

        cb.halt()
        cb.update(current_equity=9400, peak_equity=10000, daily_pnl_pct=-0.06)
        assert cb.state == State.MANUAL_HALT
