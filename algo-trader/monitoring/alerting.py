"""Telegram alerting for trade notifications and system events."""

import logging
import os

import requests

logger = logging.getLogger(__name__)


class TelegramAlerter:
    """
    Sends alerts via Telegram Bot API.
    No-op if telegram_enabled is False in config.
    """

    def __init__(self, config: dict):
        reporting_cfg = config.get("reporting", {})
        self.enabled = reporting_cfg.get("telegram_enabled", False)
        self.bot_token = os.getenv(
            "TELEGRAM_BOT_TOKEN", reporting_cfg.get("telegram_bot_token", "")
        )
        self.chat_id = os.getenv(
            "TELEGRAM_CHAT_ID", reporting_cfg.get("telegram_chat_id", "")
        )

    def send_alert(self, message: str, level: str = "INFO"):
        """Send a message via Telegram."""
        if not self.enabled or not self.bot_token or not self.chat_id:
            logger.debug(f"Telegram alert (disabled): [{level}] {message}")
            return

        prefix = {"WARNING": "\u26a0\ufe0f", "ERROR": "\u274c", "CRITICAL": "\U0001f6a8"}.get(level, "\u2139\ufe0f")

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": f"{prefix} {message}",
                "parse_mode": "Markdown",
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")

    def trade_alert(self, symbol: str, side: str, size: float, price: float, pnl: float | None = None):
        """Send a formatted trade notification."""
        if pnl is not None:
            emoji = "\u2705" if pnl > 0 else "\u274c"
            msg = f"*Trade Closed* {emoji}\n{side} {size:.6f} {symbol} @ {price:.2f}\nPnL: {pnl:+.2f} USDT"
        else:
            msg = f"*Trade Opened*\n{side} {size:.6f} {symbol} @ {price:.2f}"
        self.send_alert(msg)

    def circuit_breaker_alert(self, state: str):
        """Alert on circuit breaker state change."""
        self.send_alert(f"*Circuit Breaker*: State changed to `{state}`", level="WARNING")

    def daily_summary(self, equity: float, daily_pnl: float, open_positions: int):
        """Send daily performance summary."""
        msg = (
            f"*Daily Summary*\n"
            f"Equity: {equity:.2f} USDT\n"
            f"Daily PnL: {daily_pnl:+.2f} USDT\n"
            f"Open positions: {open_positions}"
        )
        self.send_alert(msg)
