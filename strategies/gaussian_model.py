"""
Gaussian bucket probability model and fractional Kelly position sizing.
The Gaussian model is the fallback when ensemble data is unavailable.
"""
from __future__ import annotations

import scipy.stats as stats


def gaussian_bucket_probability(
    forecast_centre: float,
    sigma: float,
    lo: float,
    hi: float,
) -> float:
    """
    Given a forecast of `forecast_centre` degrees with uncertainty `sigma`,
    return the probability of the actual high landing in [lo, hi].

    sigma tuning:
      - 24h forecast: sigma ~1.5 C (ECMWF 24h MAE is ~1.2-1.8 C for daily max)
      - 48h forecast: sigma ~2.2 C
      - 1h forecast:  sigma ~0.8 C
    """
    dist = stats.norm(loc=forecast_centre, scale=sigma)
    if hi == float("inf"):
        return float(1.0 - dist.cdf(lo))
    if lo == float("-inf"):
        return float(dist.cdf(hi))
    return float(dist.cdf(hi) - dist.cdf(lo))


def kelly_size(
    model_prob: float,
    entry_price: float,
    bankroll: float,
    alpha: float = 0.20,
    max_cost_per_bucket: float = 5.0,
    min_cost_per_bucket: float = 1.0,
) -> float:
    """
    Fractional Kelly position size in USD.

    For a binary bet paying $1 if correct, $0 if not:
      f* = (model_prob - entry_price) / (1 - entry_price)
    Fractional Kelly: f = alpha * f*
    USD size = f * bankroll, capped at max and floored at min.
    """
    edge = model_prob - entry_price
    if edge <= 0 or entry_price >= 1.0:
        return 0.0
    full_kelly = edge / (1 - entry_price)
    fractional = alpha * full_kelly
    raw_size = fractional * bankroll
    return max(min_cost_per_bucket, min(max_cost_per_bucket, raw_size))
