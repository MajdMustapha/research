"""
RISK CONTROLS — single source of truth.
Import this module in executor/orders.py ONLY.
Never call risk functions from analyst or notifier agents.
"""

import os
from dataclasses import dataclass
from typing import Optional

import yaml

from lib.state import get_open_positions, get_bankroll
from lib.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RiskResult:
    approved: bool
    reason: str
    capped_size: Optional[float] = None


def risk_check(
    market_id: str,
    side: str,
    price: float,
    requested_size: float,
    token_id: str,
) -> RiskResult:
    """
    Run all risk checks. Returns RiskResult.
    If approved=False, executor must abort — no exceptions.
    If capped_size is set, use that instead of requested_size.
    """
    cfg = _load_config()
    bankroll = get_bankroll()
    open_positions = get_open_positions()

    # Rule 1: Max position size (% of bankroll)
    max_size = bankroll * cfg["max_position_pct"]
    if requested_size > max_size:
        logger.info(f"Risk: size capped from {requested_size:.2f} to {max_size:.2f}")
        return RiskResult(True, "size_capped", capped_size=round(max_size, 2))

    # Rule 2: Max open positions
    if len(open_positions) >= cfg["max_open_positions"]:
        return RiskResult(False, f"max_open_positions_reached ({cfg['max_open_positions']})")

    # Rule 3: No duplicate market exposure
    existing_market_ids = [p["market_id"] for p in open_positions]
    if market_id in existing_market_ids:
        return RiskResult(False, "already_have_position_in_market")

    # Rule 4: Min bankroll floor — never trade if below floor
    if bankroll < cfg["min_bankroll_floor"]:
        return RiskResult(False, f"bankroll_below_floor (${cfg['min_bankroll_floor']})")

    # Rule 5: Price sanity (never buy above max or below min)
    if price > cfg["max_buy_price"] or price < cfg["min_buy_price"]:
        return RiskResult(False, f"price_out_of_range ({price})")

    # Rule 6: Minimum position size (avoid dust)
    if requested_size < cfg["min_position_size"]:
        return RiskResult(False, f"size_below_minimum (${cfg['min_position_size']})")

    return RiskResult(True, "all_checks_passed")


def lp_risk_check(market_id: str, quote_size: float) -> RiskResult:
    """Separate risk check for LP orders — different rules."""
    cfg = _load_config()
    lp_capital = _get_lp_capital()

    max_lp_per_market = lp_capital * cfg["lp_max_market_pct"]
    if quote_size > max_lp_per_market:
        return RiskResult(True, "lp_size_capped", capped_size=round(max_lp_per_market, 2))

    return RiskResult(True, "lp_check_passed")


def _load_config() -> dict:
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )
    with open(settings_path) as f:
        return yaml.safe_load(f)["risk"]


def _get_lp_capital() -> float:
    from lib.state import get_lp_capital
    return get_lp_capital()
