#!/usr/bin/env python3
"""
Midnight UTC P&L report generator.
Run via cron: 0 0 * * * python /opt/polymarket-system/scripts/daily_report.py
"""

import asyncio
import os
import sys
from datetime import date, timedelta, timezone, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.state import (
    init_db,
    get_bankroll,
    get_open_positions,
    get_connection,
    save_daily_pnl,
    log_event,
)
from lib.logger import get_logger
from agents.executor.monitor import mark_to_market_paper_trades
from agents.lp_manager.rewards import record_daily_rewards
from agents.notifier.bot import send_daily_report
from agents.notifier.formatter import format_daily_report

logger = get_logger("daily_report")


def gather_daily_data() -> dict:
    """Collect all data needed for the daily report."""
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = date.today().strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        # Realized P&L from closed positions today
        pnl_row = conn.execute(
            """SELECT COALESCE(SUM(pnl_usdc), 0) as total_pnl
            FROM positions WHERE closed_at >= ? AND closed_at < ?""",
            (yesterday, today),
        ).fetchone()
        realized_pnl = float(pnl_row["total_pnl"])

        # Trades opened/closed
        opened_row = conn.execute(
            """SELECT COUNT(*) as cnt FROM positions
            WHERE opened_at >= ? AND opened_at < ?""",
            (yesterday, today),
        ).fetchone()

        closed_row = conn.execute(
            """SELECT COUNT(*) as cnt FROM positions
            WHERE closed_at >= ? AND closed_at < ?""",
            (yesterday, today),
        ).fetchone()

        # Signals
        signals_row = conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN action_taken IN ('approved', 'approved_paper') THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN action_taken = 'rejected' THEN 1 ELSE 0 END) as rejected
            FROM signals WHERE created_at >= ? AND created_at < ?""",
            (yesterday, today),
        ).fetchone()
    finally:
        conn.close()

    # LP rewards
    rewards_data = record_daily_rewards()

    bankroll = get_bankroll()

    return {
        "date": yesterday,
        "starting_balance": bankroll,  # Approximate — use previous day's ending if available
        "ending_balance": bankroll,
        "realized_pnl": realized_pnl,
        "lp_rewards_usdc": rewards_data.get("lp_rewards_usdc", 0),
        "num_trades_opened": int(opened_row["cnt"]),
        "num_trades_closed": int(closed_row["cnt"]),
        "api_costs_usd": 0,  # TODO: track API costs from Anthropic usage
        "signals_sent": int(signals_row["total"]),
        "approved": int(signals_row["approved"]),
        "rejected": int(signals_row["rejected"]),
    }


async def main():
    init_db()
    logger.info("Generating daily report...")

    # Mark-to-market paper trades
    await mark_to_market_paper_trades()

    # Gather data
    data = gather_daily_data()

    # Save to database
    save_daily_pnl(data["date"], data)

    # Format and send
    report = format_daily_report(data)
    try:
        await send_daily_report(report)
        logger.info("Daily report sent to Telegram")
    except Exception as e:
        logger.error(f"Failed to send daily report: {e}")

    log_event("daily_report", "system", f"Report generated for {data['date']}")
    logger.info(f"Daily report complete: {data['date']}")


if __name__ == "__main__":
    asyncio.run(main())
