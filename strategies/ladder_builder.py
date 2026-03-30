"""
Ladder builder: groups edge signals into ladders by (city, date).
A ladder is a set of 2-4 adjacent underpriced buckets that together
provide coverage around the forecast centre.
"""
from __future__ import annotations

import logging
from config import Config

logger = logging.getLogger(__name__)


def build_ladder(
    city: str,
    date: str,
    signals: list[dict],
    config: Config,
) -> dict | None:
    """
    Group edge signals into a single ladder for a (city, date) pair.
    Returns ladder spec or None if insufficient signals.
    """
    if len(signals) < 1:
        return None

    # Sort by bucket range (ascending lo)
    signals = sorted(signals, key=lambda s: s["lo"])

    # Cap total cost per ladder
    total_cost = 0.0
    selected: list[dict] = []
    for sig in signals:
        if total_cost + sig["cost"] > config.MAX_COST_PER_LADDER:
            remaining = config.MAX_COST_PER_LADDER - total_cost
            if remaining >= config.MIN_COST_PER_BUCKET and sig["entry_price"] > 0:
                adjusted_shares = int(remaining / sig["entry_price"])
                if adjusted_shares >= 1:
                    sig = {**sig, "shares": adjusted_shares, "cost": round(adjusted_shares * sig["entry_price"], 4)}
                    selected.append(sig)
                    total_cost += sig["cost"]
            break
        selected.append(sig)
        total_cost += sig["cost"]

    if not selected:
        return None

    # Compute coverage: what % of forecast +/- 2 sigma is covered by selected buckets
    forecast_centre = selected[0]["forecast_centre"]
    bucket_ranges = [(s["lo"], s["hi"]) for s in selected]
    coverage = _compute_coverage(forecast_centre, bucket_ranges)

    avg_edge = sum(s["edge"] for s in selected) / len(selected)
    expected_value = sum(s["model_prob"] * s["shares"] for s in selected)

    return {
        "city": city,
        "date": date,
        "buckets": selected,
        "total_cost": round(total_cost, 4),
        "expected_value": round(expected_value, 4),
        "avg_edge": round(avg_edge, 4),
        "coverage": round(coverage, 4),
        "num_legs": len(selected),
        "forecast_centre": forecast_centre,
    }


def _compute_coverage(
    forecast_centre: float,
    bucket_ranges: list[tuple[float, float]],
    sigma: float = 2.0,
) -> float:
    """
    Fraction of the interval [forecast - 2*sigma, forecast + 2*sigma]
    covered by the selected bucket ranges.
    """
    lo_bound = forecast_centre - 2 * sigma
    hi_bound = forecast_centre + 2 * sigma
    total_range = hi_bound - lo_bound
    if total_range <= 0:
        return 0.0

    covered = 0.0
    for lo, hi in bucket_ranges:
        effective_lo = max(lo, lo_bound)
        effective_hi = min(hi if hi != float("inf") else hi_bound, hi_bound)
        if effective_hi > effective_lo:
            covered += effective_hi - effective_lo

    return min(1.0, covered / total_range)
