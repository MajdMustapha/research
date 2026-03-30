"""
Polymarket Gamma API connector.
Used ONLY for backtest data: resolved markets, metadata, price history.
Live order execution uses polymarket-cli (connectors/cli.py).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


def fetch_resolved_weather_markets(
    days: int = 180,
    limit: int = 200,
) -> list[dict]:
    """
    Pull all resolved Polymarket weather/temperature markets from Gamma API.
    Returns list of market dicts with buckets, resolution info, token IDs.
    """
    all_markets: list[dict] = []
    offset = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    while True:
        try:
            resp = httpx.get(f"{GAMMA_BASE}/markets", params={
                "closed": "true",
                "limit": limit,
                "offset": offset,
                "order": "end_date_iso",
                "ascending": "false",
            }, timeout=30)
            resp.raise_for_status()
            markets = resp.json()
        except Exception as exc:
            logger.warning("Gamma API fetch failed at offset %d: %s", offset, exc)
            break

        if not markets:
            break

        for m in markets:
            tags = [t.get("slug", "") if isinstance(t, dict) else str(t)
                    for t in (m.get("tags", []) or [])]
            tag_slugs = tags + [m.get("tag_slug", "")]
            is_weather = any(
                t in ("weather", "temperature", "daily-temperature")
                for t in tag_slugs
            )
            if not is_weather:
                q = (m.get("question", "") or "").lower()
                is_weather = "temperature" in q or "highest temp" in q

            if not is_weather:
                continue

            end_date = m.get("end_date_iso") or m.get("closed_time")
            if end_date:
                try:
                    ed = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    if ed < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            all_markets.append(m)

        offset += limit
        if len(markets) < limit:
            break
        time.sleep(0.3)

    logger.info("Fetched %d resolved weather markets from Gamma API", len(all_markets))
    return all_markets


def fetch_price_history(
    token_id: str,
    start_ts: int,
    end_ts: int,
    fidelity: int = 60,
) -> list[tuple[int, float]]:
    """
    Fetch minute-by-minute price history for a token from CLOB.
    Returns list of (unix_timestamp, price) tuples.
    """
    try:
        resp = httpx.get(f"{CLOB_BASE}/prices-history", params={
            "market": token_id,
            "startTs": start_ts,
            "endTs": end_ts,
            "fidelity": fidelity,
        }, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Price history fetch failed for %s: %s", token_id, exc)
        return []

    history = data.get("history", data) if isinstance(data, dict) else data
    result: list[tuple[int, float]] = []

    if isinstance(history, list):
        for entry in history:
            if isinstance(entry, dict):
                ts = entry.get("t") or entry.get("timestamp") or entry.get("ts")
                price = entry.get("p") or entry.get("price")
                if ts is not None and price is not None:
                    result.append((int(ts), float(price)))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                result.append((int(entry[0]), float(entry[1])))

    return sorted(result, key=lambda x: x[0])


def parse_market_buckets(market: dict) -> list[dict]:
    """
    Parse bucket information from a Gamma API market response.
    Returns list of {"label": str, "token_id": str, "outcome": str}.
    """
    buckets: list[dict] = []

    tokens = market.get("tokens", []) or []
    for token in tokens:
        buckets.append({
            "label": token.get("outcome", ""),
            "token_id": token.get("token_id", ""),
            "outcome": token.get("outcome", ""),
            "winner": token.get("winner", False),
        })

    if not buckets:
        outcomes = market.get("outcomes", []) or []
        outcome_prices = market.get("outcomePrices", []) or []
        clobTokenIds = market.get("clobTokenIds", []) or []
        for i, outcome in enumerate(outcomes):
            buckets.append({
                "label": outcome,
                "token_id": clobTokenIds[i] if i < len(clobTokenIds) else "",
                "outcome": outcome,
                "winner": False,
            })

    return buckets
