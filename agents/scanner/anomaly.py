"""
Price deviation detector.
Runs every 2 minutes, checks all tracked markets for rapid moves.
"""

import os
from datetime import datetime, timezone

import yaml

from lib.cli import get_batch_prices, get_market_price, PolymarketCLIError
from lib.state import log_event
from lib.logger import get_logger

logger = get_logger(__name__)

# In-memory price history for anomaly detection
# {token_id: [(timestamp, price), ...]}
_price_history: dict[str, list[tuple[float, float]]] = {}

# Max history entries per token (10 min window at 2 min intervals = 5 entries + buffer)
_MAX_HISTORY = 10


def _load_tracked_markets() -> list:
    markets_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "markets.yaml",
    )
    with open(markets_path) as f:
        config = yaml.safe_load(f)
    return config.get("tracked_markets") or []


def check_price_anomalies() -> list[dict]:
    """
    Fetch current prices for all tracked markets.
    Flag if: price moved >5% in last 10 minutes.
    Returns list of anomaly dicts.
    """
    markets = _load_tracked_markets()
    if not markets:
        return []

    # Collect all token IDs
    token_ids = []
    token_to_market = {}
    for m in markets:
        yes_token = m.get("yes_token_id")
        if yes_token:
            token_ids.append(yes_token)
            token_to_market[yes_token] = m

    if not token_ids:
        return []

    # Fetch prices
    now = datetime.now(timezone.utc).timestamp()
    anomalies = []

    try:
        if len(token_ids) > 1:
            prices = get_batch_prices(token_ids)
        else:
            prices = {}
            for tid in token_ids:
                price_data = get_market_price(tid)
                if price_data:
                    prices[tid] = price_data.get("mid")
    except PolymarketCLIError as e:
        logger.warning(f"Failed to fetch batch prices: {e}")
        return []

    if not prices:
        return []

    for token_id in token_ids:
        current_price_str = prices.get(token_id)
        if current_price_str is None:
            continue

        try:
            current_price = float(current_price_str)
        except (ValueError, TypeError):
            continue

        # Record in history
        if token_id not in _price_history:
            _price_history[token_id] = []
        _price_history[token_id].append((now, current_price))

        # Trim old entries
        cutoff = now - 660  # 11 minutes (10 min window + buffer)
        _price_history[token_id] = [
            (t, p) for t, p in _price_history[token_id] if t >= cutoff
        ][-_MAX_HISTORY:]

        # Check for >5% move in 10 minutes
        ten_min_ago = now - 600
        old_prices = [
            p for t, p in _price_history[token_id] if t <= ten_min_ago + 30
        ]

        if old_prices:
            oldest_price = old_prices[0]
            if oldest_price > 0:
                move_pct = abs(current_price - oldest_price) / oldest_price
                if move_pct > 0.05:
                    market = token_to_market.get(token_id, {})
                    anomaly = {
                        "token_id": token_id,
                        "market_id": market.get("market_id", ""),
                        "market_question": market.get("question", ""),
                        "old_price": oldest_price,
                        "current_price": current_price,
                        "move_pct": round(move_pct * 100, 2),
                        "direction": "up" if current_price > oldest_price else "down",
                    }
                    anomalies.append(anomaly)
                    logger.warning(
                        f"Price anomaly: {market.get('question', token_id)[:40]} "
                        f"moved {move_pct*100:.1f}% in 10min "
                        f"({oldest_price:.4f} -> {current_price:.4f})"
                    )
                    log_event(
                        "anomaly",
                        "scanner",
                        f"Price move {move_pct*100:.1f}%: {token_id[:12]}",
                    )

    return anomalies
