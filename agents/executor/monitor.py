"""
Fill monitoring + position tracker.
Polls open orders and updates position status on fill/cancel.
"""

import asyncio
from datetime import datetime, timezone

from lib.cli import get_my_orders, get_market_price, PolymarketCLIError
from lib.state import (
    get_open_positions,
    close_position,
    get_open_paper_trades,
    log_event,
    db_write,
)
from lib.logger import get_logger

logger = get_logger(__name__)


async def monitor_fills(poll_interval: int = 30) -> None:
    """
    Continuously poll for order fills and update position status.
    """
    logger.info("Fill monitor started")
    while True:
        try:
            await _check_fills()
        except Exception as e:
            logger.error(f"Fill monitor error: {e}")
        await asyncio.sleep(poll_interval)


async def _check_fills() -> None:
    """Check all open positions for fills or cancellations."""
    positions = get_open_positions()
    if not positions:
        return

    try:
        orders = get_my_orders()
        if orders is None:
            return
    except PolymarketCLIError as e:
        logger.warning(f"Failed to fetch orders: {e}")
        return

    order_map = {o.get("orderID"): o for o in orders if o.get("orderID")}

    for pos in positions:
        order_id = pos.get("order_id")
        if not order_id:
            continue

        order = order_map.get(order_id)
        if order is None:
            # Order not found — may have been filled or cancelled
            logger.info(f"Order {order_id} no longer in active orders — checking position")
            continue

        status = order.get("status", "").lower()
        if status == "filled":
            logger.info(f"Order {order_id} filled for position {pos['id']}")
            log_event("fill", "executor_monitor", f"Order {order_id} filled")
        elif status == "cancelled":
            logger.info(f"Order {order_id} cancelled — closing position {pos['id']}")
            close_position(pos["id"], exit_price=pos["entry_price"], pnl=0.0)


async def mark_to_market_paper_trades() -> None:
    """
    Update simulated P&L for open paper trades using current market prices.
    Run as part of the daily report.
    """
    paper_trades = get_open_paper_trades()
    if not paper_trades:
        return

    for trade in paper_trades:
        try:
            price_data = get_market_price(trade["token_id"])
            current_price = float(price_data["mid"])

            if trade["side"] == "yes":
                simulated_pnl = (current_price - trade["entry_price"]) * trade["shares"]
            else:
                simulated_pnl = (trade["entry_price"] - current_price) * trade["shares"]

            with db_write() as conn:
                conn.execute(
                    """UPDATE paper_trades
                    SET simulated_exit_price = ?, simulated_pnl_usdc = ?
                    WHERE id = ?""",
                    (current_price, round(simulated_pnl, 2), trade["id"]),
                )
            logger.info(
                f"Paper trade {trade['id']} MTM: entry={trade['entry_price']:.3f}, "
                f"current={current_price:.3f}, PnL=${simulated_pnl:.2f}"
            )
        except Exception as e:
            logger.warning(f"Failed to MTM paper trade {trade['id']}: {e}")
