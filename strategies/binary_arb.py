"""
Binary arbitrage scanner.
When the sum of all YES bucket prices in a temperature market is < $0.95
(accounting for fees and gas), buying all buckets guarantees $1.00 payout.
"""
from __future__ import annotations

from strategies.edge_detector import taker_fee


def scan_binary_arb(
    market_buckets: list[dict],
    fee_threshold: float = 0.05,
    gas_per_leg: float = 0.003,
) -> dict | None:
    """
    market_buckets: [{"label": "29-31 C", "token_id": "...", "yes_price": 0.11}, ...]

    Check: sum(yes_prices) + fees + gas < 1.00?
    If yes, returns arb spec. If no, returns None.
    """
    if not market_buckets:
        return None

    total_cost = sum(b["yes_price"] for b in market_buckets)
    gas_cost = gas_per_leg * len(market_buckets)
    sell_fees = sum(taker_fee(b["yes_price"]) for b in market_buckets)
    total_with_costs = total_cost + gas_cost + sell_fees

    if total_with_costs >= 1.00 - fee_threshold:
        return None

    profit = 1.00 - total_with_costs
    return {
        "type": "binary_arb",
        "buckets": market_buckets,
        "total_cost": round(total_cost, 4),
        "gas_cost": round(gas_cost, 4),
        "sell_fees": round(sell_fees, 4),
        "net_profit": round(profit, 4),
        "roi": round(profit / total_cost, 4) if total_cost > 0 else 0.0,
    }
