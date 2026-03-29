"""
LP order quoting logic — posts two-sided limit orders.
Prioritises Finance (50% rebate), Politics (25% rebate) categories.
"""

import os
from datetime import datetime, timezone

import yaml

from lib.risk import lp_risk_check
from lib.cli import (
    get_market_price,
    get_market,
    create_limit_order,
    cancel_market_orders,
    PolymarketCLIError,
)
from lib.state import save_lp_order, log_event
from lib.logger import get_logger

logger = get_logger(__name__)


def _load_settings() -> dict:
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "settings.yaml",
    )
    with open(settings_path) as f:
        return yaml.safe_load(f)


def _load_lp_markets() -> list:
    markets_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "markets.yaml",
    )
    with open(markets_path) as f:
        config = yaml.safe_load(f)
    return config.get("lp_markets") or []


def _is_paper_mode() -> bool:
    return _load_settings().get("system", {}).get("paper_trade", True)


def calculate_hours_remaining(end_date: str) -> float:
    """Calculate hours until market resolution."""
    try:
        if end_date:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            delta = end_dt - datetime.now(timezone.utc)
            return max(0, delta.total_seconds() / 3600)
    except Exception:
        pass
    return 999.0  # If we can't parse, assume far away


def refresh_lp_orders(market_config: dict) -> None:
    """
    Main LP quoting loop for one market.
    Posts two-sided orders: bid at mid-2c, ask at mid+2c.
    """
    settings = _load_settings()
    lp_settings = settings.get("lp_manager", {})
    spread = lp_settings.get("quote_spread_cents", 0.02)
    default_size = lp_settings.get("default_quote_size_usdc", 50.0)
    pull_hours = market_config.get(
        "pull_hours_before_resolution",
        lp_settings.get("pull_before_resolution_hours", 24),
    )

    market_id = market_config["market_id"]
    token_id = market_config["yes_token_id"]
    condition_id = market_config.get("condition_id", market_id)

    # 1. Get current mid price
    try:
        mid_data = get_market_price(token_id)
        mid = float(mid_data["mid"])
    except (PolymarketCLIError, KeyError, TypeError) as e:
        logger.warning(f"LP: Failed to get price for {market_id}: {e}")
        return

    # 2. Check if near resolution — pull and skip if so
    try:
        market = get_market(market_id)
        hours_to_resolution = calculate_hours_remaining(market.get("endDate"))
        if hours_to_resolution < pull_hours:
            if not _is_paper_mode():
                cancel_market_orders(condition_id)
            log_event("lp_manager", "pulled_orders_near_resolution", market_id)
            logger.info(f"LP: Pulled orders for {market_id} — {hours_to_resolution:.1f}h to resolution")
            return
    except PolymarketCLIError as e:
        logger.warning(f"LP: Failed to fetch market info for {market_id}: {e}")

    # 3. Risk check
    risk = lp_risk_check(market_id, quote_size=default_size)
    if not risk.approved:
        logger.info(f"LP: Risk check blocked for {market_id}: {risk.reason}")
        return

    quote_size = risk.capped_size or default_size

    # 4. Calculate quotes
    bid_price = round(mid - spread, 4)
    ask_price = round(mid + spread, 4)

    # Clamp to valid range
    bid_price = max(0.02, bid_price)
    ask_price = min(0.98, ask_price)

    if _is_paper_mode():
        logger.info(
            f"[PAPER LP] {market_id[:12]}: mid={mid:.4f}, "
            f"bid={bid_price:.4f}, ask={ask_price:.4f}, size=${quote_size:.2f}"
        )
        return

    # 5. Cancel stale orders
    try:
        cancel_market_orders(condition_id)
    except PolymarketCLIError as e:
        logger.warning(f"LP: Failed to cancel stale orders for {market_id}: {e}")

    # 6. Post new two-sided orders (atomic: both succeed or neither persists)
    bid_shares = quote_size / bid_price
    ask_shares = quote_size / ask_price

    try:
        result_bid = create_limit_order(
            token_id=token_id,
            side="buy",
            price=bid_price,
            size=round(bid_shares, 2),
            post_only=True,
        )
    except PolymarketCLIError as e:
        logger.error(f"LP: Failed to post bid for {market_id}: {e}")
        return

    try:
        result_ask = create_limit_order(
            token_id=token_id,
            side="sell",
            price=ask_price,
            size=round(ask_shares, 2),
            post_only=True,
        )
    except PolymarketCLIError as e:
        # Ask failed — cancel the bid to avoid lopsided quotes
        logger.error(f"LP: Ask failed for {market_id}, cancelling orphaned bid: {e}")
        try:
            cancel_market_orders(condition_id)
        except PolymarketCLIError as cancel_err:
            logger.error(f"LP: Failed to cancel orphaned bid: {cancel_err}")
        return

    # Both sides succeeded — save to database
    save_lp_order({
        "market_id": market_id,
        "token_id": token_id,
        "order_id": result_bid.get("orderID", "unknown"),
        "side": "buy",
        "price": bid_price,
        "size": round(bid_shares, 2),
    })
    save_lp_order({
        "market_id": market_id,
        "token_id": token_id,
        "order_id": result_ask.get("orderID", "unknown"),
        "side": "sell",
        "price": ask_price,
        "size": round(ask_shares, 2),
    })
    logger.info(f"LP orders posted: {market_id[:12]} bid={bid_price:.4f} ask={ask_price:.4f}")


def refresh_all_lp_markets() -> None:
    """Refresh LP orders for all configured markets."""
    markets = _load_lp_markets()
    if not markets:
        logger.info("LP: No markets configured")
        return

    for market_config in markets:
        try:
            refresh_lp_orders(market_config)
        except Exception as e:
            logger.error(f"LP: Error refreshing {market_config.get('market_id')}: {e}")
