"""
Daily LP rebate tracker.
Run at 01:00 UTC to record yesterday's rewards.
"""

from datetime import date, timedelta

from lib.cli import get_daily_rewards, check_order_scoring, PolymarketCLIError
from lib.state import get_active_lp_orders, save_daily_pnl, log_event
from lib.logger import get_logger

logger = get_logger(__name__)


def record_daily_rewards() -> dict:
    """
    Fetch yesterday's LP rewards and store in daily_pnl table.
    Returns reward data for inclusion in daily report.
    """
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        rewards = get_daily_rewards(yesterday)
        total_rewards = float(rewards.get("totalRewards", 0)) if rewards else 0.0

        log_event("lp_manager", "daily_rewards", f"{yesterday}: ${total_rewards:.2f}")
        logger.info(f"LP rewards for {yesterday}: ${total_rewards:.2f}")

        return {
            "date": yesterday,
            "lp_rewards_usdc": total_rewards,
            "raw": rewards,
        }
    except PolymarketCLIError as e:
        logger.error(f"Failed to fetch daily rewards: {e}")
        return {"date": yesterday, "lp_rewards_usdc": 0.0, "error": str(e)}


def check_active_order_scores() -> list[dict]:
    """
    Check scoring status of all active LP orders.
    Verifies orders are earning rewards.
    """
    active_orders = get_active_lp_orders()
    results = []

    for order in active_orders:
        order_id = order.get("order_id")
        if not order_id:
            continue

        try:
            scoring = check_order_scoring(order_id)
            is_scoring = scoring.get("isScoring", False) if scoring else False
            results.append({
                "order_id": order_id,
                "market_id": order["market_id"],
                "is_scoring": is_scoring,
                "scoring_data": scoring,
            })
            if not is_scoring:
                logger.warning(f"LP order {order_id} is NOT scoring rewards")
        except PolymarketCLIError as e:
            logger.warning(f"Failed to check scoring for order {order_id}: {e}")
            results.append({
                "order_id": order_id,
                "market_id": order["market_id"],
                "is_scoring": None,
                "error": str(e),
            })

    return results
