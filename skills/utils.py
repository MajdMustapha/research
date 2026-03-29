#!/usr/bin/env python3
"""Shared utilities for all PortfolioMind skills."""

import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import finnhub
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def get_today() -> str:
    """Return today's date as YYYY-MM-DD string."""
    return date.today().isoformat()


def get_workspace_dir(ticker: str, dt: str = None) -> Path:
    """Return and create workspace/{date}/{ticker}/ directory."""
    dt = dt or get_today()
    d = PROJECT_ROOT / "workspace" / dt / ticker
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(data: dict, filename: str, ticker: str, dt: str = None) -> Path:
    """Write data as JSON to workspace and print to stdout."""
    path = get_workspace_dir(ticker, dt) / filename
    content = json.dumps(data, indent=2, default=str)
    path.write_text(content)
    print(content)
    return path


def read_json(filename: str, ticker: str, dt: str = None) -> dict:
    """Read JSON from workspace. Raises FileNotFoundError if missing."""
    path = get_workspace_dir(ticker, dt) / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing: {path}")
    return json.loads(path.read_text())


def merge_into_raw_data(new_data, key: str, ticker: str, dt: str = None):
    """Merge new_data under key into raw_data.json (accumulate pattern)."""
    path = get_workspace_dir(ticker, dt) / "raw_data.json"
    existing = json.loads(path.read_text()) if path.exists() else {}
    existing[key] = new_data
    path.write_text(json.dumps(existing, indent=2, default=str))


def get_finnhub_client() -> finnhub.Client:
    """Return configured Finnhub client. Exits if key missing."""
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        print("ERROR: FINNHUB_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)
    return finnhub.Client(api_key=key)


def rate_limit_sleep(seconds: float = 1.0):
    """Sleep between Finnhub calls to respect 60 req/min limit."""
    time.sleep(seconds)


def load_config() -> dict:
    """Load config.yaml from project root."""
    path = PROJECT_ROOT / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def get_position(ticker: str) -> dict:
    """Get position data for a ticker from config. Returns empty dict if not found."""
    config = load_config()
    return config.get("portfolio", {}).get("positions", {}).get(ticker, {})


def compute_portfolio_value(prices: dict = None) -> dict:
    """Compute total portfolio value and per-position weights.

    Args:
        prices: dict of {ticker: current_price}. If None, returns cost-basis only.

    Returns:
        dict with total_value, total_cost, positions detail, unrealized_pnl
    """
    config = load_config()
    positions = config["portfolio"]["positions"]
    result = {"positions": {}, "total_value": 0, "total_cost": 0}

    for ticker, pos in positions.items():
        shares = pos["shares"]
        avg_cost = pos["avg_cost"]
        cost_basis = shares * avg_cost
        current_price = prices.get(ticker, avg_cost) if prices else avg_cost
        market_value = shares * current_price
        pct_from_cost = (current_price - avg_cost) / avg_cost if avg_cost else 0

        result["positions"][ticker] = {
            "shares": shares,
            "avg_cost": avg_cost,
            "cost_basis": round(cost_basis, 2),
            "current_price": round(current_price, 2),
            "market_value": round(market_value, 2),
            "pct_from_cost": round(pct_from_cost, 4),
            "unrealized_pnl": round(market_value - cost_basis, 2),
        }
        result["total_value"] += market_value
        result["total_cost"] += cost_basis

    result["total_value"] = round(result["total_value"], 2)
    result["total_cost"] = round(result["total_cost"], 2)
    result["unrealized_pnl"] = round(result["total_value"] - result["total_cost"], 2)
    result["portfolio_drawdown_pct"] = round(
        (result["total_value"] - result["total_cost"]) / result["total_cost"] * 100, 2
    ) if result["total_cost"] else 0

    # Compute weights
    for ticker in result["positions"]:
        if result["total_value"] > 0:
            result["positions"][ticker]["weight_pct"] = round(
                result["positions"][ticker]["market_value"] / result["total_value"] * 100, 2
            )
        else:
            result["positions"][ticker]["weight_pct"] = 0

    return result


def error_exit(msg: str):
    """Print error to stderr and exit 1."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def timestamp_now() -> str:
    """Return current ISO8601 timestamp."""
    return datetime.now().isoformat(timespec="seconds")
