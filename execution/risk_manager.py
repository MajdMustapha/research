"""
Risk manager — 9 pre-trade gates.
All gates must pass before any order is submitted.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from config import Config
from connectors.cli import cli
from connectors.goldsky import fetch_first10_wallets

logger = logging.getLogger(__name__)


def pre_trade_checks(
    ladder_spec: dict,
    config: Config,
    agent_state: dict,
) -> tuple[bool, str]:
    """
    Run all 9 pre-trade gates. Return (approved, reason).
    Gates execute in order; first failure short-circuits.
    """
    city = ladder_spec.get("city", "")
    total_cost = ladder_spec.get("total_cost", 0)

    # Gate 1: USDC.e balance
    approved, reason = _check_balance(total_cost, config)
    if not approved:
        return False, reason

    # Gate 2: Slippage per bucket
    for bucket in ladder_spec.get("buckets", []):
        approved, reason = _check_slippage(bucket, config)
        if not approved:
            return False, reason

    # Gate 3: Minimum depth
    for bucket in ladder_spec.get("buckets", []):
        approved, reason = _check_depth(bucket)
        if not approved:
            return False, reason

    # Gate 4: Crowding score
    approved, reason = _check_crowding_score(city, config, agent_state)
    if not approved:
        return False, reason

    # Gate 5: Daily loss limit
    approved, reason = _check_daily_loss(config, agent_state)
    if not approved:
        return False, reason

    # Gate 6: Open ladders limit
    approved, reason = _check_open_ladders(config, agent_state)
    if not approved:
        return False, reason

    # Gate 7: Correlated city group limit
    approved, reason = _check_correlation_limit(city, config, agent_state)
    if not approved:
        return False, reason

    # Gate 8: Organic pricing
    for bucket in ladder_spec.get("buckets", []):
        approved, reason = _check_organic_pricing(bucket)
        if not approved:
            return False, reason

    # Gate 9: Book concentration
    for bucket in ladder_spec.get("buckets", []):
        approved, reason = _check_book_concentration(bucket)
        if not approved:
            return False, reason

    return True, "approved"


def _check_balance(total_cost: float, config: Config) -> tuple[bool, str]:
    try:
        resp = cli("clob balance --asset-type collateral")
        balance = float(resp.get("balance", 0))
    except (RuntimeError, ValueError) as exc:
        return False, f"balance_check_failed: {exc}"

    required = total_cost * 1.05
    if balance < required:
        return False, f"insufficient_balance: ${balance:.2f} < ${required:.2f}"
    return True, "approved"


def _check_slippage(bucket: dict, config: Config) -> tuple[bool, str]:
    token_id = bucket.get("token_id", "")
    shares = bucket.get("shares", 0)
    if not token_id or shares <= 0:
        return True, "approved"

    slip = estimate_slippage(token_id, shares)
    if slip > config.MAX_SLIPPAGE_PCT:
        return False, f"slippage_too_high: {bucket.get('label', '')} = {slip:.1%}"
    return True, "approved"


def estimate_slippage(token_id: str, shares: int) -> float:
    """Walk the CLI book output to estimate fill slippage."""
    try:
        book = cli(f"clob book {token_id}")
    except RuntimeError:
        return float("inf")

    asks = sorted(book.get("asks", []), key=lambda x: float(x.get("price", 999)))
    if not asks:
        return float("inf")

    best_ask = float(asks[0]["price"])
    remaining = shares
    total_cost = 0.0
    for level in asks:
        price = float(level.get("price", 0))
        size = float(level.get("size", 0))
        take = min(remaining, size)
        total_cost += take * price
        remaining -= take
        if remaining <= 0:
            break

    if remaining > 0:
        return float("inf")

    avg_fill = total_cost / shares
    return (avg_fill - best_ask) / best_ask if best_ask > 0 else float("inf")


def _check_depth(bucket: dict, min_depth: float = 500.0) -> tuple[bool, str]:
    token_id = bucket.get("token_id", "")
    if not token_id:
        return True, "approved"
    try:
        book = cli(f"clob book {token_id}")
        asks = book.get("asks", [])
        depth = sum(float(a.get("price", 0)) * float(a.get("size", 0)) for a in asks)
        if depth < min_depth:
            return False, f"insufficient_depth: {bucket.get('label', '')} = ${depth:.0f}"
    except RuntimeError:
        return False, f"depth_check_failed: {bucket.get('label', '')}"
    return True, "approved"


def _check_crowding_score(
    city: str, config: Config, agent_state: dict,
) -> tuple[bool, str]:
    city_configs = agent_state.get("city_configs", {})
    city_cfg = city_configs.get(city, {})
    score = city_cfg.get("opportunity_score", 100)
    if score < config.MIN_OPPORTUNITY_SCORE:
        return False, f"city_too_crowded: {city} score={score}"
    return True, "approved"


def _check_daily_loss(config: Config, agent_state: dict) -> tuple[bool, str]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_pnl = agent_state.get("daily_realised_pnl", {}).get(today, 0)
    if daily_pnl < -config.DAILY_LOSS_LIMIT:
        return False, f"daily_loss_limit: ${daily_pnl:.2f} < -${config.DAILY_LOSS_LIMIT}"
    return True, "approved"


def _check_open_ladders(config: Config, agent_state: dict) -> tuple[bool, str]:
    open_positions = agent_state.get("open_positions", [])
    if len(open_positions) >= config.MAX_OPEN_LADDERS:
        return False, f"max_open_ladders: {len(open_positions)} >= {config.MAX_OPEN_LADDERS}"
    return True, "approved"


def _check_correlation_limit(
    city: str, config: Config, agent_state: dict,
) -> tuple[bool, str]:
    city_group = next(
        (g for g, cities in config.CORRELATED_CITY_GROUPS.items() if city in cities),
        None,
    )
    if city_group is None:
        return True, "approved"

    today = datetime.now(timezone.utc).date().isoformat()
    open_positions = agent_state.get("open_positions", [])
    active_in_group = [
        p for p in open_positions
        if p.get("date", "")[:10] == today
        and p.get("city") in config.CORRELATED_CITY_GROUPS[city_group]
    ]
    if len(active_in_group) >= config.MAX_LADDERS_PER_CITY_GROUP:
        return False, f"correlation_limit: {city_group} has {len(active_in_group)} active"
    return True, "approved"


def _check_organic_pricing(
    bucket: dict,
    min_unique_wallets: int = 3,
    lookback_hours: int = 2,
) -> tuple[bool, str]:
    token_id = bucket.get("token_id", "")
    if not token_id:
        return True, "approved"

    try:
        drop_ts = int(
            (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp()
        )
        wallets = fetch_first10_wallets([token_id], drop_ts)
        if len(wallets) < min_unique_wallets:
            return False, f"thin_book: {bucket.get('label', '')} only {len(wallets)} wallets"
    except Exception:
        pass
    return True, "approved"


def _check_book_concentration(
    bucket: dict,
    max_single_wallet_pct: float = 0.60,
) -> tuple[bool, str]:
    token_id = bucket.get("token_id", "")
    if not token_id:
        return True, "approved"

    try:
        book = cli(f"clob book {token_id}")
        asks = book.get("asks", [])
        if not asks:
            return False, f"empty_book: {bucket.get('label', '')}"
        sizes = [float(a.get("size", 0)) for a in asks]
        total = sum(sizes)
        top3 = sum(sorted(sizes, reverse=True)[:3])
        if total > 0 and top3 / total > max_single_wallet_pct:
            return False, f"book_concentrated: {bucket.get('label', '')} top3={top3/total:.0%}"
    except RuntimeError:
        pass
    return True, "approved"
