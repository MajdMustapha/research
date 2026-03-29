"""Scanner agent entry point — runs news + wallet + anomaly loops."""

import asyncio
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.state import init_db, log_event
from lib.logger import get_logger
from agents.scanner.news import poll_rss_feeds, check_articles_for_markets, cleanup_old_articles
from agents.scanner.wallets import poll_wallet_trades, check_all_baskets
from agents.scanner.anomaly import check_price_anomalies

logger = get_logger("scanner")


async def news_loop(interval: int = 60):
    """Poll RSS feeds every 60 seconds."""
    while True:
        try:
            articles = poll_rss_feeds()
            if articles:
                relevant = check_articles_for_markets(articles)
                for match in relevant:
                    await _process_news_signal(match)
            # Cleanup old article GUIDs weekly
            cleanup_old_articles(days=7)
        except Exception as e:
            logger.error(f"News loop error: {e}")
        await asyncio.sleep(interval)


async def wallet_loop(interval: int = 300):
    """Poll wallets every 5 minutes."""
    while True:
        try:
            poll_wallet_trades()
            signals = check_all_baskets()
            for signal in signals:
                await _process_wallet_signal(signal)
        except Exception as e:
            logger.error(f"Wallet loop error: {e}")
        await asyncio.sleep(interval)


async def anomaly_loop(interval: int = 120):
    """Check price anomalies every 2 minutes."""
    while True:
        try:
            anomalies = check_price_anomalies()
            for anomaly in anomalies:
                logger.warning(f"Anomaly detected: {anomaly}")
        except Exception as e:
            logger.error(f"Anomaly loop error: {e}")
        await asyncio.sleep(interval)


async def _process_news_signal(match: dict):
    """Send news signal to analyst for scoring."""
    try:
        from agents.analyst.claude_client import analyse_signal
        from lib.cli import get_market, get_price_history
        from lib.state import save_signal

        market = match["market"]
        article = match["article"]

        full_market = get_market(market["market_id"])
        price_history = get_price_history(
            market.get("yes_token_id", ""), interval="1d"
        )

        signal_data = {
            "source": "news",
            "headline": article["title"],
            "description": article["description"],
            "link": article["link"],
        }

        result = analyse_signal(full_market, signal_data, price_history or [])

        if result.get("signal_valid"):
            signal_id = save_signal({
                "source": "news",
                "market_id": market["market_id"],
                "token_id": market.get("yes_token_id"),
                "market_question": full_market.get("question"),
                "current_price": result.get("current_market_prob"),
                "estimated_true_prob": result.get("estimated_true_prob"),
                "edge": result.get("edge"),
                "confidence": result.get("confidence"),
                "claude_reasoning": result.get("reasoning"),
                "recommended_side": result.get("recommended_side"),
            })

            from agents.notifier.bot import send_signal_alert
            telegram_id = await send_signal_alert(signal_id, result, full_market)
            from lib.state import update_signal_telegram_id
            update_signal_telegram_id(signal_id, telegram_id)

            logger.info(f"Signal #{signal_id} sent to operator for approval")
    except Exception as e:
        logger.error(f"Failed to process news signal: {e}")


async def _process_wallet_signal(signal: dict):
    """Send wallet consensus signal to analyst for scoring."""
    try:
        from agents.analyst.claude_client import analyse_signal
        from lib.cli import get_market, get_price_history
        from lib.state import save_signal

        market_id = signal["market_id"]
        full_market = get_market(market_id)
        token_id = full_market.get("clobTokenIds", [""])[0]
        price_history = get_price_history(token_id, interval="1d")

        result = analyse_signal(full_market, signal, price_history or [])

        if result.get("signal_valid"):
            signal_id = save_signal({
                "source": "wallet",
                "market_id": market_id,
                "token_id": token_id,
                "market_question": full_market.get("question"),
                "current_price": result.get("current_market_prob"),
                "estimated_true_prob": result.get("estimated_true_prob"),
                "edge": result.get("edge"),
                "confidence": result.get("confidence"),
                "claude_reasoning": result.get("reasoning"),
                "recommended_side": result.get("recommended_side"),
                "wallet_consensus_count": signal.get("wallet_count"),
            })

            from agents.notifier.bot import send_signal_alert
            telegram_id = await send_signal_alert(signal_id, result, full_market)
            from lib.state import update_signal_telegram_id
            update_signal_telegram_id(signal_id, telegram_id)
    except Exception as e:
        logger.error(f"Failed to process wallet signal: {e}")


async def main():
    init_db()
    log_event("startup", "scanner", "Scanner agent started")
    logger.info("Scanner agent started")

    import yaml
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "settings.yaml"
    )
    with open(settings_path) as f:
        settings = yaml.safe_load(f)

    scanner_cfg = settings.get("scanner", {})

    await asyncio.gather(
        news_loop(scanner_cfg.get("news_poll_interval_seconds", 60)),
        wallet_loop(scanner_cfg.get("wallet_poll_interval_seconds", 300)),
        anomaly_loop(scanner_cfg.get("anomaly_check_interval_seconds", 120)),
    )


if __name__ == "__main__":
    asyncio.run(main())
