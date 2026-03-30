"""
Claude Code agent loop for live mode.
One invocation = one complete agent cycle:
  1. Load state
  2. Startup checks (geoblock, balance, API health)
  3. If model drop window is due: scan + signal + execute
  4. Check open positions (fill status, early-close, redemption)
  5. Weekly: run crowd-rank if due
  6. Save state
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from config import Config
from connectors.alert_sink import alert
from connectors.cli import cli

logger = logging.getLogger(__name__)

STATE_PATH = os.environ.get("AGENT_STATE_PATH", "data/agent_state.json")


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "last_model_drop_processed": None,
            "last_crowd_rank_run": None,
            "open_positions": [],
            "paper_positions": [],
            "daily_realised_pnl": {},
            "maker_rebate_income": {},
            "city_configs": {},
            "city_health": {},
            "live_mode": False,
            "last_balance_check": None,
        }


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def startup_checks() -> bool:
    """
    Must pass ALL checks before any scanning or trading.
    Returns True if all clear, False if agent should exit.
    """
    # 1. Geoblock
    try:
        geo = cli("clob geoblock")
        if geo.get("blocked", True):
            alert("Geoblock detected — agent cannot trade from this location", "critical")
            return False
    except RuntimeError as exc:
        alert(f"CLI geoblock check failed: {exc}", "critical")
        return False

    # 2. API health
    try:
        cli("clob ok")
    except RuntimeError:
        alert("Polymarket CLOB API unhealthy", "warn")
        return False

    # 3. Balance
    try:
        bal = cli("clob balance --asset-type collateral")
        usdc = float(bal.get("balance", 0))
        if usdc < 10.0:
            alert(f"USDC balance too low: ${usdc:.2f}", "warn")
    except RuntimeError:
        alert("Balance check failed", "warn")

    return True


def is_model_drop_due(state: dict, config: Config) -> bool:
    """Returns True if a GFS/ECMWF drop window has occurred since last processed."""
    last = state.get("last_model_drop_processed")
    now = datetime.now(timezone.utc)
    for trigger_time in config.GFS_TRIGGER_TIMES:
        h, m = map(int, trigger_time.split(":"))
        trigger_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= trigger_today:
            if last is None or trigger_today.isoformat() > last:
                return True
    return False


def step(config: Config) -> None:
    """One complete agent cycle. Called by main.py live."""
    state = load_state()
    logger.info("Agent cycle started. Last update: %s", state.get("last_updated", "never"))

    if not startup_checks():
        save_state(state)
        logger.warning("Startup checks failed — exiting cycle")
        return

    # Update balance
    try:
        bal = cli("clob balance --asset-type collateral")
        state["last_balance_check"] = {
            "usdc": float(bal.get("balance", 0)),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except RuntimeError:
        pass

    # Model drop scan
    if is_model_drop_due(state, config):
        logger.info("Model drop window due — scanning markets")
        from execution.live_trader import scan_and_execute
        scan_and_execute(state, config)
        state["last_model_drop_processed"] = datetime.now(timezone.utc).isoformat()

    # Position monitoring (every cycle)
    from monitor.position_monitor import (
        apply_drift_guard,
        check_edge_decay,
        fetch_daily_rebates,
        monitor_open_positions,
        update_brier_score,
    )

    updates = monitor_open_positions(state.get("open_positions", []), config)
    state["open_positions"] = [
        p for p in state.get("open_positions", [])
        if p.get("status") not in ("early_closed", "resolved", "redeemed")
    ]

    # Daily maker rebate fetch
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rebate = fetch_daily_rebates(today)
    state.setdefault("maker_rebate_income", {})[today] = rebate

    # Brier score and edge decay checks for each city with data
    all_resolved = state.get("paper_positions", []) + state.get("open_positions", [])
    resolved = [p for p in all_resolved if p.get("status") in ("resolved", "early_closed", "won", "lost")]
    cities_with_data = set(p.get("city") for p in resolved if p.get("city"))
    for city in cities_with_data:
        brier = update_brier_score(city, resolved)
        state = apply_drift_guard(city, brier, state, config)
        state = check_edge_decay(city, resolved, config, state)

    # Weekly crowd-rank
    last_crowd = state.get("last_crowd_rank_run")
    if last_crowd:
        try:
            days_since = (
                datetime.now(timezone.utc) - datetime.fromisoformat(last_crowd)
            ).days
        except (ValueError, TypeError):
            days_since = 999
    else:
        days_since = 999

    if days_since >= config.CROWD_RANK_REFRESH_DAYS:
        logger.info("Running weekly crowd-rank refresh")
        try:
            from analysis.crowding_detector import CrowdingDetector
            reports = CrowdingDetector().run(days=90)
            city_configs = {}
            for r in reports:
                city_configs[r.city] = {
                    "opportunity_score": r.opportunity_score,
                    "entry_window_minutes": r.entry_window_minutes,
                    "sigma_table": r.sigma_table,
                    "resolution_station": r.resolution_station,
                    "trend": r.trend,
                }
            state["city_configs"] = city_configs
            state["last_crowd_rank_run"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            logger.warning("Crowd-rank failed: %s", exc)

    save_state(state)
    logger.info("Agent cycle complete. %d open positions, %d paper positions.",
                len(state.get("open_positions", [])),
                len(state.get("paper_positions", [])))
