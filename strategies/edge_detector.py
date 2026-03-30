"""
Edge detection: compares model probability to market price, accounting for fees.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any

from config import Config
from connectors.open_meteo import (
    ensemble_bucket_probability,
    ensemble_confidence,
    fetch_forecast_tmax,
)
from strategies.gaussian_model import gaussian_bucket_probability, kelly_size
from strategies.resolution_parser import parse_bucket_range

logger = logging.getLogger(__name__)

FEE_PARAMS: dict[str, dict[str, float]] = {
    "weather": {"feeRate": 0.05, "exponent": 1},
    "geopolitics": {"feeRate": 0.0, "exponent": 0},
}


def taker_fee(price: float, category: str = "weather") -> float:
    """
    Returns effective taker fee for a given share price.
    Category 'weather': feeRate=0.05, exponent=1.
    """
    params = FEE_PARAMS.get(category, FEE_PARAMS["weather"])
    return price * params["feeRate"] * (price * (1 - price)) ** params["exponent"]


def edge_after_fees(
    model_prob: float,
    entry_price: float,
    category: str = "weather",
) -> float:
    """
    True edge accounting for maker entry (0 fee) and taker exit.
    Conservative: always model the sell leg as a taker.
    """
    sell_price = model_prob
    sell_fee = taker_fee(sell_price, category)
    return model_prob - entry_price - sell_fee


def compute_bucket_probability(
    lat: float,
    lon: float,
    date: str,
    lo: float,
    hi: float,
    forecast_centre: float,
    sigma: float,
    config: Config,
) -> tuple[float, str]:
    """
    Compute bucket probability using ensemble first, Gaussian fallback.
    Returns (probability, model_used).
    """
    ensemble_prob = ensemble_bucket_probability(lat, lon, date, lo, hi)
    if ensemble_prob is not None:
        midpoint = (lo + hi) / 2 if hi != float("inf") else lo + 2
        conf = ensemble_confidence(lat, lon, date, midpoint)
        if conf >= config.ENSEMBLE_MIN_CONFIDENCE:
            return ensemble_prob, "ensemble"

    prob = gaussian_bucket_probability(forecast_centre, sigma, lo, hi)
    return prob, "gaussian"


def detect_edges(
    city_config: dict,
    date: str,
    buckets: list[dict],
    bucket_prices: dict[str, float],
    config: Config,
    bankroll: float = 100.0,
    forecast_override: dict[str, float] | None = None,
) -> list[dict]:
    """
    For a single city+date, detect mispricings across all buckets.
    Returns list of edge signals with sizing.
    """
    lat = city_config["lat"]
    lon = city_config["lon"]
    sigma = city_config.get("sigma_table", {}).get(
        str(date[5:7].lstrip("0")), config.SIGMA_24H
    )

    if forecast_override:
        forecasts = forecast_override
    else:
        try:
            forecasts = fetch_forecast_tmax(lat, lon, date)
        except Exception as exc:
            logger.warning("Forecast fetch failed for %s: %s", city_config["name"], exc)
            return []

    if not forecasts:
        return []

    model_values = list(forecasts.values())
    model_spread = max(model_values) - min(model_values)
    if model_spread > config.MODEL_CONSENSUS_MAX_SPREAD:
        logger.info(
            "%s: model spread %.1f C > %.1f C limit, skipping",
            city_config["name"], model_spread, config.MODEL_CONSENSUS_MAX_SPREAD,
        )
        return []

    forecast_centre = statistics.mean(model_values)

    # Determine Kelly alpha based on city health
    alpha = config.KELLY_ALPHA
    city_health = city_config.get("health", {})
    if city_health.get("status") == "degraded":
        alpha = city_health.get("kelly_alpha_override", config.KELLY_ALPHA_DEGRADED)

    # Determine min edge (may be overridden by drift guard)
    min_edge = config.MIN_EDGE_PCT
    if city_health.get("min_edge_override"):
        min_edge = city_health["min_edge_override"]

    signals: list[dict] = []
    for bucket in buckets:
        label = bucket.get("label", "")
        token_id = bucket.get("token_id", "")
        lo, hi = parse_bucket_range(label)
        if lo == 0.0 and hi == 0.0:
            continue

        entry_price = bucket_prices.get(label) or bucket_prices.get(token_id)
        if entry_price is None or entry_price <= 0 or entry_price >= 1.0:
            continue

        if not (lo <= forecast_centre + 2 and hi >= forecast_centre - 2):
            continue

        model_prob, model_used = compute_bucket_probability(
            lat, lon, date, lo, hi, forecast_centre, sigma, config,
        )

        edge = edge_after_fees(model_prob, entry_price, config.FEE_CATEGORY)
        if edge < min_edge:
            continue

        cost = kelly_size(
            model_prob, entry_price, bankroll,
            alpha=alpha,
            max_cost_per_bucket=config.MAX_COST_PER_BUCKET,
            min_cost_per_bucket=config.MIN_COST_PER_BUCKET,
        )
        shares = int(cost / entry_price) if entry_price > 0 else 0
        if shares < 1:
            continue

        signals.append({
            "label": label,
            "token_id": token_id,
            "lo": lo,
            "hi": hi,
            "entry_price": entry_price,
            "model_prob": round(model_prob, 4),
            "edge": round(edge, 4),
            "model_used": model_used,
            "forecast_centre": round(forecast_centre, 2),
            "model_spread": round(model_spread, 2),
            "shares": shares,
            "cost": round(shares * entry_price, 4),
            "kelly_alpha": alpha,
        })

    return signals
