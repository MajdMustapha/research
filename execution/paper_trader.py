"""
Pessimistic paper trader.
Simulates realistic execution costs so paper P&L tracks live P&L.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from config import Config
from strategies.edge_detector import taker_fee

logger = logging.getLogger(__name__)


def simulate_fill(
    entry_price: float,
    shares: int,
    category: str = "weather",
    mode: str = "conservative",
) -> dict:
    """
    Simulate a realistic GTC limit order fill.
    Conservative mode: 0.5% worse fill + gas cost.
    Optimistic mode: exact entry price.
    """
    if mode == "conservative":
        fill_price = entry_price * 1.005
    else:
        fill_price = entry_price

    gas_cost = 0.003
    cost = fill_price * shares + gas_cost

    return {
        "quoted_price": entry_price,
        "fill_price": round(fill_price, 6),
        "shares": shares,
        "cost": round(cost, 4),
        "gas_cost": gas_cost,
        "mode": f"{mode}_paper",
    }


def paper_trade_ladder(
    ladder_spec: dict,
    config: Config,
    agent_state: dict,
) -> dict:
    """
    Execute a paper trade for the entire ladder.
    Logs to agent_state["paper_positions"].
    """
    positions: list[dict] = []

    for bucket in ladder_spec.get("buckets", []):
        fill = simulate_fill(
            entry_price=bucket["entry_price"],
            shares=bucket["shares"],
            category=config.FEE_CATEGORY,
            mode=config.PAPER_MODE,
        )

        position = {
            "city": ladder_spec["city"],
            "date": ladder_spec["date"],
            "bucket_label": bucket["label"],
            "token_id": bucket.get("token_id", ""),
            "entry_price": bucket["entry_price"],
            "fill_price": fill["fill_price"],
            "shares": bucket["shares"],
            "cost": fill["cost"],
            "model_prob": bucket.get("model_prob", 0),
            "edge": bucket.get("edge", 0),
            "status": "OPEN",
            "placed_at": datetime.now(timezone.utc).isoformat(),
            "mode": "paper",
        }
        positions.append(position)

    agent_state.setdefault("paper_positions", []).extend(positions)

    total_cost = sum(p["cost"] for p in positions)
    logger.info(
        "Paper trade: %s %s — %d legs, $%.2f total",
        ladder_spec["city"],
        ladder_spec["date"],
        len(positions),
        total_cost,
    )

    return {
        "status": "paper_filled",
        "legs": len(positions),
        "total_cost": total_cost,
        "positions": positions,
    }
