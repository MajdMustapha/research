"""
Position monitor.
Checks fill status, early-close triggers, CTF redemption,
maker rebate tracking, Brier score drift, and edge decay.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from config import Config
from connectors.cli import cli
from connectors.alert_sink import alert

logger = logging.getLogger(__name__)


# ── Position monitoring ──────────────────────────────────────────────

def monitor_open_positions(
    open_positions: list[dict],
    config: Config,
) -> list[dict]:
    """
    Called every agent cycle. Checks fill status and early-close triggers.
    Returns list of position updates.
    """
    updates: list[dict] = []

    for pos in open_positions:
        token_id = pos.get("token_id", "")
        if not token_id:
            continue

        # Check current price for early-close
        try:
            price_resp = cli(f"clob price {token_id} --side sell")
            current_price = float(price_resp.get("price", 0))
        except (RuntimeError, ValueError):
            continue

        entry_price = pos.get("entry_price", 0)
        if entry_price <= 0:
            continue

        # Early-close trigger: price reached 2.5x entry
        if current_price >= entry_price * config.EARLY_CLOSE_MULTIPLIER:
            if config.LIVE_MODE:
                try:
                    cli(
                        f"clob market-order "
                        f"--token {token_id} "
                        f"--side sell "
                        f"--amount {pos.get('shares', 0)}"
                    )
                    pos["status"] = "early_closed"
                    pos["exit_price"] = current_price
                    updates.append({**pos, "action": "early_closed"})
                    logger.info(
                        "Early close: %s %s at %.4f (entry %.4f)",
                        pos.get("city"), pos.get("bucket_label"), current_price, entry_price,
                    )
                except RuntimeError as exc:
                    logger.warning("Early close failed: %s", exc)
            else:
                # Paper mode — just mark it
                pos["status"] = "early_closed"
                pos["exit_price"] = current_price
                updates.append({**pos, "action": "early_closed"})

    # Redeem resolved winning positions (live mode only)
    if config.LIVE_MODE:
        _redeem_resolved(config)

    return updates


def _redeem_resolved(config: Config) -> None:
    """Redeem any resolved winning CTF positions."""
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "")
    if not wallet:
        return

    try:
        all_positions = cli(f"data positions {wallet}")
        if not isinstance(all_positions, list):
            return
        for p in all_positions:
            if (p.get("resolved")
                    and p.get("side") == "YES"
                    and float(p.get("value", 0)) > 0):
                try:
                    cli(f"ctf redeem --condition {p['conditionId']}")
                    logger.info("Redeemed: condition %s", p["conditionId"])
                except RuntimeError:
                    pass
    except RuntimeError:
        pass


# ── Maker rebate tracking ───────────────────────────────────────────

def fetch_daily_rebates(date_str: str | None = None) -> float:
    """Fetch maker rebate earnings for a given date."""
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        resp = cli(f"clob earnings --date {date_str}")
        return float(resp.get("totalEarnings", 0))
    except (RuntimeError, KeyError, ValueError):
        return 0.0


# ── Brier score drift ───────────────────────────────────────────────

def update_brier_score(
    city: str,
    resolved_trades: list[dict],
    window: int = 30,
) -> float:
    """
    Compute rolling Brier score over last `window` resolved trades for a city.
    Brier = mean((model_prob - outcome)^2), outcome = 1 if won else 0.
    """
    recent = [t for t in resolved_trades if t.get("city") == city][-window:]
    if len(recent) < 10:
        return 0.15  # default

    brier = sum(
        (t.get("model_prob", 0.5) - (1.0 if t.get("won") else 0.0)) ** 2
        for t in recent
    ) / len(recent)
    return brier


def apply_drift_guard(
    city: str,
    brier: float,
    agent_state: dict,
    config: Config,
) -> dict:
    """Update agent_state with drift flags per city."""
    city_state = agent_state.setdefault("city_health", {}).setdefault(city, {})
    city_state["brier_score"] = round(brier, 4)

    if brier > config.BRIER_SUSPEND_THRESHOLD:
        city_state["status"] = "suspended"
        city_state["reason"] = f"brier={brier:.3f} — model severely miscalibrated"
        alert(f"{city} SUSPENDED: Brier={brier:.3f}", "critical")
    elif brier > config.BRIER_DEGRADED_THRESHOLD:
        city_state["status"] = "degraded"
        city_state["kelly_alpha_override"] = config.KELLY_ALPHA_DEGRADED
        city_state["min_edge_override"] = 0.16
        alert(f"{city} model drift (brier={brier:.3f})", "warn")
    else:
        city_state["status"] = "healthy"
        city_state.pop("kelly_alpha_override", None)
        city_state.pop("min_edge_override", None)

    return agent_state


# ── Edge decay detector ──────────────────────────────────────────────

def check_edge_decay(
    city: str,
    recent_trades: list[dict],
    config: Config,
    agent_state: dict,
) -> dict:
    """
    Compute 7-day rolling avg_edge_at_entry for city.
    Suspend/caution if edge is compressing.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    city_trades = [
        t for t in recent_trades
        if t.get("city") == city and (t.get("placed_at", "") or "") >= cutoff
    ]
    if len(city_trades) < 5:
        return agent_state

    avg_edge = sum(t.get("edge_at_entry", 0) for t in city_trades) / len(city_trades)
    city_state = agent_state.setdefault("city_health", {}).setdefault(city, {})
    city_state["rolling_avg_edge_7d"] = round(avg_edge, 4)

    if avg_edge < config.EDGE_DECAY_SUSPEND:
        city_state["edge_status"] = "suspended"
        city_state["edge_suspend_reason"] = f"avg_edge={avg_edge:.3f} < {config.EDGE_DECAY_SUSPEND}"
        alert(f"{city} edge decay: avg={avg_edge:.3f}", "warn")
    elif avg_edge < config.EDGE_DECAY_CAUTION:
        city_state["edge_status"] = "caution"
    else:
        city_state["edge_status"] = "healthy"

    return agent_state
