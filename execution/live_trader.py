"""
Live trader — GTC order placement via polymarket-cli.
Includes cancel guard and partial-fill exit.
Only active when LIVE_MODE=true (never set by default).
"""
from __future__ import annotations

import logging
import os
import statistics
import time
from datetime import datetime, timezone
from typing import Any

from config import Config
from connectors.cli import cli
from connectors.alert_sink import alert
from connectors.open_meteo import fetch_forecast_tmax
from connectors.polymarket_gamma import parse_market_buckets
from execution.paper_trader import paper_trade_ladder
from execution.risk_manager import pre_trade_checks
from strategies.binary_arb import scan_binary_arb
from strategies.edge_detector import detect_edges
from strategies.ladder_builder import build_ladder
from strategies.resolution_parser import STATION_COORDS

logger = logging.getLogger(__name__)


def scan_and_execute(state: dict, config: Config) -> None:
    """
    Main scan loop: for each active city, fetch forecasts, detect edges,
    build ladders, run risk checks, and execute (paper or live).
    """
    city_configs = state.get("city_configs", {})

    for city_cfg in config.CITIES:
        city = city_cfg["name"]
        if not city_cfg.get("active", False):
            continue

        # Check city health
        city_health = state.get("city_health", {}).get(city, {})
        if city_health.get("status") == "suspended":
            logger.info("Skipping %s: suspended (%s)", city, city_health.get("reason", ""))
            continue
        if city_health.get("edge_status") == "suspended":
            logger.info("Skipping %s: edge decay suspended", city)
            continue

        # Merge crowding-detector overrides
        merged_cfg = {**city_cfg}
        if city in city_configs:
            merged_cfg.update(city_configs[city])
        merged_cfg["health"] = city_health

        # Search for active temperature markets
        try:
            markets = cli(f'markets search "temperature {city}"')
        except RuntimeError as exc:
            logger.warning("Market search failed for %s: %s", city, exc)
            continue

        if not isinstance(markets, list):
            markets = [markets] if isinstance(markets, dict) else []

        for market in markets:
            _process_market(market, merged_cfg, state, config)


def _process_market(
    market: dict,
    city_cfg: dict,
    state: dict,
    config: Config,
) -> None:
    """Process a single market: detect edges, build ladder, execute."""
    city = city_cfg["name"]
    buckets = parse_market_buckets(market)
    if not buckets:
        return

    # Get today's date for the market
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Fetch current prices for all buckets
    token_ids = [b["token_id"] for b in buckets if b.get("token_id")]
    if not token_ids:
        return

    bucket_prices: dict[str, float] = {}
    try:
        ids_str = ",".join(token_ids)
        prices = cli(f'clob batch-prices "{ids_str}" --side buy')
        if isinstance(prices, dict):
            for tid, price in prices.items():
                for b in buckets:
                    if b["token_id"] == tid:
                        bucket_prices[b["label"]] = float(price)
        elif isinstance(prices, list):
            for i, b in enumerate(buckets):
                if i < len(prices):
                    bucket_prices[b["label"]] = float(prices[i])
    except RuntimeError as exc:
        logger.debug("Batch price fetch failed: %s", exc)
        return

    # Binary arb scan (takes priority)
    arb_buckets = [
        {"label": b["label"], "token_id": b["token_id"],
         "yes_price": bucket_prices.get(b["label"], 1.0)}
        for b in buckets if b.get("token_id")
    ]
    arb = scan_binary_arb(arb_buckets)
    if arb:
        logger.info("Binary arb detected for %s: profit=$%.4f", city, arb["net_profit"])
        # TODO: execute binary arb in live mode
        return

    # Directional edge detection
    bankroll = state.get("last_balance_check", {}).get("usdc", 100.0)
    signals = detect_edges(
        city_cfg, date, buckets, bucket_prices, config, bankroll=bankroll,
    )
    if not signals:
        return

    ladder = build_ladder(city, date, signals, config)
    if not ladder:
        return

    logger.info(
        "Ladder signal: %s %s — %d legs, $%.2f cost, %.1f%% avg edge",
        city, date, ladder["num_legs"], ladder["total_cost"], ladder["avg_edge"] * 100,
    )

    if not config.LIVE_MODE:
        paper_trade_ladder(ladder, config, state)
        return

    # Live mode — run risk checks
    approved, reason = pre_trade_checks(ladder, config, state)
    if not approved:
        logger.info("Ladder rejected: %s — %s", city, reason)
        return

    # Place GTC orders
    result = place_ladder_gtc(ladder, config)
    if result["status"] == "filled":
        _record_live_positions(ladder, result, state)
        alert(f"Ladder filled: {city} {date} — {ladder['num_legs']} legs", "info")
    else:
        logger.info("Ladder %s: %s %s", result["status"], city, date)


def place_ladder_gtc(ladder_spec: dict, config: Config) -> dict:
    """
    Place all ladder legs as GTC limit orders via polymarket-cli.
    Poll for fills every 60s. Cancel all unfilled legs after timeout.
    """
    order_ids: dict[str, str] = {}
    cancel_after = config.ORDER_CANCEL_AFTER_MINUTES * 60
    start = time.time()

    for bucket in ladder_spec["buckets"]:
        try:
            resp = cli(
                f"clob create-order "
                f"--token {bucket['token_id']} "
                f"--side buy "
                f"--price {bucket['entry_price'] * 0.99:.4f} "
                f"--size {bucket['shares']}"
            )
            order_ids[bucket["label"]] = resp.get("orderID", "")
        except RuntimeError as exc:
            for oid in order_ids.values():
                try:
                    cli(f"clob cancel {oid}")
                except RuntimeError:
                    pass
            return {"status": "submit_failed", "error": str(exc), "filled_legs": {}}

    filled: dict[str, dict] = {}
    while time.time() - start < cancel_after:
        time.sleep(60)
        for label in list(order_ids.keys()):
            try:
                status = cli(f"clob order {order_ids[label]}")
                if status.get("status") == "MATCHED":
                    filled[label] = status
                    del order_ids[label]
            except RuntimeError:
                pass
        if not order_ids:
            return {"status": "filled", "filled_legs": filled}

    # Timeout — cancel remaining
    for oid in order_ids.values():
        try:
            cli(f"clob cancel {oid}")
        except RuntimeError:
            pass

    if filled:
        # Partial fill — exit filled positions to avoid unbalanced ladder
        for label, fill in filled.items():
            try:
                cli(
                    f"clob market-order "
                    f"--token {fill.get('asset', '')} "
                    f"--side sell "
                    f"--amount {fill.get('sizeMatched', 0)}"
                )
            except RuntimeError:
                pass
        return {"status": "partial_cancelled", "filled_legs": {}}

    return {"status": "timeout_cancelled", "filled_legs": {}}


def _record_live_positions(
    ladder: dict, result: dict, state: dict,
) -> None:
    """Record filled positions in agent state."""
    for label, fill_data in result.get("filled_legs", {}).items():
        bucket = next((b for b in ladder["buckets"] if b["label"] == label), {})
        state.setdefault("open_positions", []).append({
            "city": ladder["city"],
            "date": ladder["date"],
            "bucket_label": label,
            "token_id": bucket.get("token_id", ""),
            "order_id": fill_data.get("orderID", ""),
            "entry_price": bucket.get("entry_price", 0),
            "shares": bucket.get("shares", 0),
            "status": "OPEN",
            "placed_at": datetime.now(timezone.utc).isoformat(),
            "cancel_after": "",
        })
