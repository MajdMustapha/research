"""Tests for lib/risk.py — ALL risk rules must pass."""

import pytest
from unittest.mock import patch, MagicMock

from lib.risk import risk_check, lp_risk_check, RiskResult


MOCK_CONFIG = {
    "max_position_pct": 0.02,
    "max_open_positions": 5,
    "min_bankroll_floor": 100.0,
    "max_buy_price": 0.95,
    "min_buy_price": 0.05,
    "min_position_size": 5.0,
    "lp_max_market_pct": 0.15,
    "daily_loss_limit_pct": 0.10,
}


def _patch_risk(bankroll=1000.0, open_positions=None):
    """Return a context stack of patches for risk module dependencies."""
    if open_positions is None:
        open_positions = []

    return [
        patch("lib.risk._load_config", return_value=MOCK_CONFIG),
        patch("lib.risk.get_bankroll", return_value=bankroll),
        patch("lib.risk.get_open_positions", return_value=open_positions),
    ]


class TestRiskCheck:
    def test_risk_check_allows_valid_trade(self):
        """Valid trade with sufficient bankroll and open slots passes."""
        patches = _patch_risk(bankroll=1000.0, open_positions=[])
        for p in patches:
            p.start()
        try:
            result = risk_check(
                market_id="market_1",
                side="buy",
                price=0.50,
                requested_size=15.0,
                token_id="token_1",
            )
            assert result.approved is True
            assert result.reason == "all_checks_passed"
            assert result.capped_size is None
        finally:
            for p in patches:
                p.stop()

    def test_risk_check_caps_size(self):
        """2% cap is enforced even if larger size requested."""
        patches = _patch_risk(bankroll=1000.0, open_positions=[])
        for p in patches:
            p.start()
        try:
            # 2% of 1000 = 20. Requesting 50 should be capped.
            result = risk_check(
                market_id="market_1",
                side="buy",
                price=0.50,
                requested_size=50.0,
                token_id="token_1",
            )
            assert result.approved is True
            assert result.reason == "size_capped"
            assert result.capped_size == 20.0
        finally:
            for p in patches:
                p.stop()

    def test_risk_check_blocks_max_positions(self):
        """Blocks new position when 5 already open."""
        open_positions = [
            {"market_id": f"market_{i}"} for i in range(5)
        ]
        patches = _patch_risk(bankroll=1000.0, open_positions=open_positions)
        for p in patches:
            p.start()
        try:
            result = risk_check(
                market_id="market_new",
                side="buy",
                price=0.50,
                requested_size=15.0,
                token_id="token_1",
            )
            assert result.approved is False
            assert "max_open_positions_reached" in result.reason
        finally:
            for p in patches:
                p.stop()

    def test_risk_check_blocks_duplicate_market(self):
        """Blocks entering same market twice."""
        open_positions = [{"market_id": "market_dup"}]
        patches = _patch_risk(bankroll=1000.0, open_positions=open_positions)
        for p in patches:
            p.start()
        try:
            result = risk_check(
                market_id="market_dup",
                side="buy",
                price=0.50,
                requested_size=15.0,
                token_id="token_1",
            )
            assert result.approved is False
            assert result.reason == "already_have_position_in_market"
        finally:
            for p in patches:
                p.stop()

    def test_risk_check_blocks_below_floor(self):
        """Blocks all trading when bankroll < floor."""
        patches = _patch_risk(bankroll=50.0, open_positions=[])
        for p in patches:
            p.start()
        try:
            result = risk_check(
                market_id="market_1",
                side="buy",
                price=0.50,
                requested_size=0.5,  # small enough to not trigger size cap
                token_id="token_1",
            )
            assert result.approved is False
            assert "bankroll_below_floor" in result.reason
        finally:
            for p in patches:
                p.stop()

    def test_risk_check_blocks_extreme_prices_high(self):
        """Blocks buys above 0.95."""
        patches = _patch_risk(bankroll=1000.0, open_positions=[])
        for p in patches:
            p.start()
        try:
            result = risk_check(
                market_id="market_1",
                side="buy",
                price=0.97,
                requested_size=15.0,
                token_id="token_1",
            )
            assert result.approved is False
            assert "price_out_of_range" in result.reason
        finally:
            for p in patches:
                p.stop()

    def test_risk_check_blocks_extreme_prices_low(self):
        """Blocks buys below 0.05."""
        patches = _patch_risk(bankroll=1000.0, open_positions=[])
        for p in patches:
            p.start()
        try:
            result = risk_check(
                market_id="market_1",
                side="buy",
                price=0.03,
                requested_size=15.0,
                token_id="token_1",
            )
            assert result.approved is False
            assert "price_out_of_range" in result.reason
        finally:
            for p in patches:
                p.stop()

    def test_risk_check_blocks_dust_trades(self):
        """Blocks trades below minimum position size."""
        patches = _patch_risk(bankroll=1000.0, open_positions=[])
        for p in patches:
            p.start()
        try:
            result = risk_check(
                market_id="market_1",
                side="buy",
                price=0.50,
                requested_size=2.0,  # below $5 minimum
                token_id="token_1",
            )
            assert result.approved is False
            assert "size_below_minimum" in result.reason
        finally:
            for p in patches:
                p.stop()


class TestLPRiskCheck:
    def test_lp_risk_check_caps_per_market(self):
        """LP exposure per market never exceeds 15% of LP capital."""
        with patch("lib.risk._load_config", return_value=MOCK_CONFIG), \
             patch("lib.risk._get_lp_capital", return_value=1000.0):
            # 15% of 1000 = 150. Requesting 200 should be capped.
            result = lp_risk_check(market_id="lp_market_1", quote_size=200.0)
            assert result.approved is True
            assert result.reason == "lp_size_capped"
            assert result.capped_size == 150.0

    def test_lp_risk_check_passes_valid(self):
        """LP order within limits passes."""
        with patch("lib.risk._load_config", return_value=MOCK_CONFIG), \
             patch("lib.risk._get_lp_capital", return_value=1000.0):
            result = lp_risk_check(market_id="lp_market_1", quote_size=50.0)
            assert result.approved is True
            assert result.reason == "lp_check_passed"
            assert result.capped_size is None
