"""LP Manager agent entry point — runs quoting loop + WebSocket monitor."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import yaml

from lib.state import init_db, log_event
from lib.logger import get_logger
from lib.health import run_startup_checks
from agents.lp_manager.quoter import refresh_all_lp_markets
from agents.lp_manager.monitor import monitor_order_book_with_reconnect, on_price_move
from agents.lp_manager.rewards import record_daily_rewards

logger = get_logger("lp_manager")


def _load_lp_markets() -> list:
    markets_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "markets.yaml"
    )
    with open(markets_path) as f:
        config = yaml.safe_load(f)
    return config.get("lp_markets") or []


async def quote_loop(interval: int = 300):
    """Refresh LP orders every 5 minutes."""
    while True:
        try:
            refresh_all_lp_markets()
        except Exception as e:
            logger.error(f"Quote loop error: {e}")
        await asyncio.sleep(interval)


async def rewards_loop():
    """Check rewards daily at 01:00 UTC."""
    from datetime import datetime, timezone, timedelta

    while True:
        now = datetime.now(timezone.utc)
        # Calculate seconds until next 01:00 UTC
        next_run = now.replace(hour=1, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        wait_seconds = (next_run - now).total_seconds()

        await asyncio.sleep(wait_seconds)
        try:
            record_daily_rewards()
        except Exception as e:
            logger.error(f"Rewards loop error: {e}")


async def main():
    init_db()

    if not run_startup_checks():
        logger.error("Startup checks failed — LP manager not starting")
        return

    log_event("startup", "lp_manager", "LP Manager agent started")
    logger.info("LP Manager agent started")

    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "settings.yaml"
    )
    with open(settings_path) as f:
        settings = yaml.safe_load(f)

    interval = settings.get("lp_manager", {}).get("refresh_interval_seconds", 300)

    tasks = [
        quote_loop(interval),
        rewards_loop(),
    ]

    # Start WebSocket monitors for each LP market
    lp_markets = _load_lp_markets()
    for market_config in lp_markets:
        token_id = market_config.get("yes_token_id")
        if token_id:
            tasks.append(monitor_order_book_with_reconnect(token_id, on_price_move))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
