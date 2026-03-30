"""
Backtest simulator.
Runs the full backtest pipeline: collect data, simulate entries,
compute P&L, and validate out-of-sample.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from config import Config
from strategies.edge_detector import edge_after_fees, taker_fee
from strategies.gaussian_model import gaussian_bucket_probability, kelly_size
from strategies.resolution_parser import parse_bucket_range

logger = logging.getLogger(__name__)


def run_backtest_for_market(
    market: dict,
    config: Config,
    sigma_table: dict[str, float] | None = None,
) -> dict:
    """
    Simulate a single market entry and resolution.
    market: record from data_collector.collect_backtest_data()
    """
    forecast = market.get("forecast", {})
    if not forecast:
        return {"action": "skip", "reason": "no_forecast", "city": market.get("city")}

    model_values = list(forecast.values())
    model_spread = max(model_values) - min(model_values)
    if model_spread > config.MODEL_CONSENSUS_MAX_SPREAD:
        return {"action": "skip", "reason": "models_disagree",
                "city": market.get("city"), "model_spread": model_spread}

    forecast_centre = statistics.mean(model_values)
    city = market.get("city", "")
    date = market.get("date", "")

    # Get sigma — prefer calibrated table, fall back to config default
    month_key = str(int(date[5:7])) if date else "0"
    if sigma_table and month_key in sigma_table:
        sigma = sigma_table[month_key]
    else:
        city_cfg = market.get("city_config", {})
        sigma = city_cfg.get("sigma_table", {}).get(month_key, config.SIGMA_24H)

    # Identify target buckets
    buckets = market.get("buckets", [])
    bucket_prices = market.get("bucket_prices", {})
    entry_positions: list[dict] = []

    for bucket in buckets:
        label = bucket.get("label", "")
        lo, hi = parse_bucket_range(label)
        if lo == 0.0 and hi == 0.0:
            continue

        if not (lo <= forecast_centre + 2 and hi >= forecast_centre - 2):
            continue

        # Get entry price from price history (first 5 minutes average)
        prices_at_entry = bucket_prices.get(label, [])
        if not prices_at_entry:
            continue

        first_prices = [p for _, p in prices_at_entry[:5]]
        if not first_prices:
            continue
        entry_price = statistics.mean(first_prices)
        if entry_price <= 0 or entry_price >= 1.0:
            continue

        model_prob = gaussian_bucket_probability(forecast_centre, sigma, lo, hi)
        edge = edge_after_fees(model_prob, entry_price, config.FEE_CATEGORY)
        if edge < config.MIN_EDGE_PCT:
            continue

        shares = kelly_size(
            model_prob, entry_price, bankroll=100.0,
            alpha=config.KELLY_ALPHA,
            max_cost_per_bucket=config.MAX_COST_PER_BUCKET,
            min_cost_per_bucket=config.MIN_COST_PER_BUCKET,
        )
        num_shares = int(shares / entry_price) if entry_price > 0 else 0
        if num_shares < 1:
            continue

        # Pessimistic fill simulation (conservative paper mode)
        fill_price = entry_price * 1.005
        cost = fill_price * num_shares + 0.003  # gas

        entry_positions.append({
            "label": label,
            "lo": lo, "hi": hi,
            "entry_price": entry_price,
            "fill_price": fill_price,
            "model_prob": model_prob,
            "edge": edge,
            "shares": num_shares,
            "cost": cost,
        })

    if not entry_positions:
        return {"action": "skip", "reason": "no_edge", "city": city}

    # Simulate resolution
    winning_bucket = market.get("resolved_bucket")
    total_cost = sum(p["cost"] for p in entry_positions)
    total_payout = 0.0
    results: list[dict] = []

    for pos in entry_positions:
        won = pos["label"] == winning_bucket
        payout = 1.0 * pos["shares"] if won else 0.0
        pnl = payout - pos["cost"]
        total_payout += payout
        results.append({
            "bucket": pos["label"],
            "won": won,
            "cost": pos["cost"],
            "payout": payout,
            "pnl": pnl,
            "model_prob": pos["model_prob"],
            "edge": pos["edge"],
        })

    net_pnl = total_payout - total_cost
    return {
        "action": "entered",
        "city": city,
        "date": date,
        "forecast_centre": forecast_centre,
        "model_spread": model_spread,
        "actual_high": market.get("actual_high_c"),
        "positions": results,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "net_pnl": net_pnl,
        "roi": net_pnl / total_cost if total_cost > 0 else 0,
        "any_won": any(r["won"] for r in results),
    }


def calibrate_sigma(markets: list[dict]) -> dict[str, float]:
    """
    Calibrate sigma per month from forecast errors on the training set.
    Returns {month_str: sigma} where month_str is "1".."12".
    """
    errors_by_month: dict[str, list[float]] = {}
    for m in markets:
        forecast = m.get("forecast", {})
        actual = m.get("actual_high_c")
        date = m.get("date", "")
        if not forecast or actual is None or not date:
            continue

        fc = statistics.mean(forecast.values())
        error = actual - fc
        month = str(int(date[5:7]))
        errors_by_month.setdefault(month, []).append(error)

    sigma_table: dict[str, float] = {}
    for month, errors in errors_by_month.items():
        if len(errors) >= 5:
            sigma_table[month] = float(np.std(errors))
        elif len(errors) >= 2:
            # Fall back to per-season if monthly samples < 5
            sigma_table[month] = float(np.std(errors))

    return sigma_table


def summarise(results: list[dict]) -> dict:
    """Summarise a list of backtest results into aggregate metrics."""
    entered = [r for r in results if r.get("action") == "entered"]
    skipped = [r for r in results if r.get("action") == "skip"]

    if not entered:
        return {
            "total_markets": len(results),
            "entered": 0,
            "skipped": len(skipped),
            "net_pnl": 0.0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "avg_roi": 0.0,
            "avg_edge": 0.0,
            "total_cost": 0.0,
        }

    wins = [r for r in entered if r.get("any_won")]
    total_pnl = sum(r["net_pnl"] for r in entered)
    total_cost = sum(r["total_cost"] for r in entered)

    all_edges = []
    for r in entered:
        for pos in r.get("positions", []):
            all_edges.append(pos["edge"])

    return {
        "total_markets": len(results),
        "entered": len(entered),
        "skipped": len(skipped),
        "wins": len(wins),
        "win_rate": len(wins) / len(entered) if entered else 0,
        "net_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(entered), 2) if entered else 0,
        "avg_roi": round(total_pnl / total_cost, 4) if total_cost > 0 else 0,
        "avg_edge": round(statistics.mean(all_edges), 4) if all_edges else 0,
        "total_cost": round(total_cost, 2),
        "results": entered,
    }


def run_full_backtest(
    config: Config,
    days: int = 180,
    cities: list[str] | None = None,
    horizon: int = 24,
) -> dict:
    """Run backtest without train/test split."""
    from backtest.data_collector import collect_backtest_data

    markets = collect_backtest_data(config, days=days, cities=cities, horizon=horizon)
    sigma_table = calibrate_sigma(markets)

    results = []
    for market in markets:
        result = run_backtest_for_market(market, config, sigma_table=sigma_table)
        results.append(result)

    return {
        "full": summarise(results),
        "sigma_table": sigma_table,
        "gate_passed": None,
    }


def run_split_backtest(
    config: Config,
    days: int = 180,
    holdout_days: int = 30,
    cities: list[str] | None = None,
    horizon: int = 24,
) -> dict:
    """
    Split backtest with look-ahead bias check.
    Calibrates sigma on train window only, simulates on both windows.
    """
    from backtest.data_collector import collect_backtest_data

    all_markets = collect_backtest_data(config, days=days, cities=cities, horizon=horizon)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=holdout_days)).strftime("%Y-%m-%d")
    train_markets = [m for m in all_markets if (m.get("date", "") or "") < cutoff]
    test_markets = [m for m in all_markets if (m.get("date", "") or "") >= cutoff]

    logger.info("Split backtest: %d train, %d test (cutoff %s)",
                len(train_markets), len(test_markets), cutoff)

    # Calibrate on train only
    sigma_table = calibrate_sigma(train_markets)

    # Simulate on both with train-calibrated sigma
    train_results = [run_backtest_for_market(m, config, sigma_table) for m in train_markets]
    test_results = [run_backtest_for_market(m, config, sigma_table) for m in test_markets]

    train_summary = summarise(train_results)
    test_summary = summarise(test_results)

    gate_passed = (
        test_summary["net_pnl"] > 0
        and test_summary["win_rate"] > 0.60
    )

    return {
        "train": train_summary,
        "test": test_summary,
        "sigma_table": sigma_table,
        "gate_passed": gate_passed,
    }
