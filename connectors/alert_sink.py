"""
Lightweight Telegram alert sink for critical agent failures.
Both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are optional.
If absent, alerts are silently skipped. Never raises.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_PREFIX = {
    "info": "[INFO]",
    "warn": "[WARN]",
    "critical": "[CRIT]",
}


def alert(message: str, level: str = "info") -> None:
    """Post a message to a Telegram bot. Never raises."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    prefix = _PREFIX.get(level, "[INFO]")
    text = f"WeatherBot {prefix} {message}"

    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=5,
        )
    except Exception:
        pass
