"""
Goldsky subgraph connector for Polymarket on-chain data.
Used exclusively by the crowding detector (analysis/crowding_detector.py).
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

GOLDSKY_ENDPOINT = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/polymarket-orderbook-0x/prod/gn"
)

PLATFORM_WALLETS = {
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
}

WALLET_QUERY = """
query($token_ids: [String!], $after_ts: Int!, $before_ts: Int!) {
  orderFilledEvents(
    where: {
      makerAssetId_in: $token_ids,
      timestamp_gte: $after_ts,
      timestamp_lte: $before_ts
    }
    first: 500
  ) {
    maker
    timestamp
    makerAmountFilled
  }
}
"""

_last_request_time: float = 0.0


def _rate_limit() -> None:
    """Enforce 0.5s between Goldsky requests to avoid undocumented rate limits."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.5:
        time.sleep(0.5 - elapsed)
    _last_request_time = time.time()


def _query(query: str, variables: dict, max_retries: int = 3) -> dict:
    """Execute a Goldsky GraphQL query with retry and backoff."""
    for attempt in range(max_retries):
        _rate_limit()
        try:
            resp = httpx.post(
                GOLDSKY_ENDPOINT,
                json={"query": query, "variables": variables},
                timeout=20,
            )
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Goldsky 429, backing off %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("data", {})
        except Exception as exc:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                logger.warning("Goldsky query failed after %d retries: %s", max_retries, exc)
                return {}
    return {}


def fetch_first10_wallets(token_ids: list[str], model_drop_ts: int) -> set[str]:
    """
    Fetch all wallets that traded any of the market's bucket tokens
    within the 10 minutes following the model drop.
    """
    data = _query(WALLET_QUERY, {
        "token_ids": token_ids,
        "after_ts": model_drop_ts,
        "before_ts": model_drop_ts + 600,
    })
    events = data.get("orderFilledEvents", [])
    return {
        e["maker"].lower()
        for e in events
        if e["maker"].lower() not in PLATFORM_WALLETS
    }


def fetch_wallets_in_window(
    token_ids: list[str], start_ts: int, duration_seconds: int = 3600,
) -> list[dict]:
    """
    Fetch all fill events for tokens within a time window.
    Returns raw events with maker + makerAmountFilled.
    """
    data = _query(WALLET_QUERY, {
        "token_ids": token_ids,
        "after_ts": start_ts,
        "before_ts": start_ts + duration_seconds,
    })
    return data.get("orderFilledEvents", [])
