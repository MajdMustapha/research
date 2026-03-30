"""
UMA oracle dispute state monitor.
Polls for active disputes on markets where we hold positions.
If disputed, capital is excluded from available_balance until resolution.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

UMA_SUBGRAPH = (
    "https://api.thegraph.com/subgraphs/name/"
    "umaprotocol/mainnet-optimistic-oracle-v3"
)

DISPUTE_QUERY = """
query($markets: [String!]) {
  requestPrices(where: {identifier_in: $markets, disputed: true}) {
    identifier
    timestamp
    expirationTime
    disputed
  }
}
"""


def check_dispute_state(open_condition_ids: list[str]) -> dict[str, bool]:
    """
    Returns {condition_id: is_disputed} for each open position.
    Disputed positions should be excluded from available_balance.
    """
    if not open_condition_ids:
        return {}

    result: dict[str, bool] = {cid: False for cid in open_condition_ids}

    try:
        resp = httpx.post(
            UMA_SUBGRAPH,
            json={
                "query": DISPUTE_QUERY,
                "variables": {"markets": open_condition_ids},
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        disputed_ids = {
            rp["identifier"]
            for rp in data.get("requestPrices", [])
            if rp.get("disputed")
        }
        for cid in open_condition_ids:
            if cid in disputed_ids:
                result[cid] = True
                logger.warning("Dispute detected: condition %s", cid)
    except Exception as exc:
        logger.debug("UMA dispute check failed: %s", exc)

    return result
