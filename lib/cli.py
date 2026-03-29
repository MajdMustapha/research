"""
Wrapper for polymarket-cli (Rust binary, v0.1.5).
Binary location: /usr/local/bin/polymarket (or from $PATH).
All methods return parsed Python objects or raise PolymarketCLIError.
"""

import subprocess
import json
import time
from threading import Lock
from typing import Optional, Union

from lib.logger import get_logger

logger = get_logger(__name__)


class PolymarketCLIError(Exception):
    pass


# ─── Rate Limiter ─────────────────────────────────────────────────────────────

_order_lock = Lock()
_order_timestamps: list[float] = []
_MAX_ORDERS_PER_MINUTE = 50  # CLI limit is 60, leave 10 margin


def _rate_limit_order():
    """
    Enforce max 50 order calls/minute (leaving 10 margin).
    Blocks until a slot is available.
    """
    global _order_timestamps
    with _order_lock:
        now = time.time()
        _order_timestamps = [t for t in _order_timestamps if now - t < 60]
        if len(_order_timestamps) >= _MAX_ORDERS_PER_MINUTE:
            sleep_time = 60 - (now - _order_timestamps[0]) + 0.1
            logger.warning(f"Rate limit: sleeping {sleep_time:.1f}s")
            time.sleep(max(0, sleep_time))
        _order_timestamps.append(time.time())


# ─── Core Runner ──────────────────────────────────────────────────────────────

def _run(args: list[str], require_wallet: bool = False) -> Union[dict, list, None]:
    """
    Run a polymarket-cli command with JSON output.
    Raises PolymarketCLIError on non-zero exit or JSON parse failure.
    """
    cmd = ["polymarket", "-o", "json"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            error_msg = result.stdout or result.stderr
            raise PolymarketCLIError(
                f"CLI error (exit {result.returncode}): {error_msg}"
            )
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise PolymarketCLIError(f"CLI timeout: {' '.join(args)}")
    except json.JSONDecodeError as e:
        raise PolymarketCLIError(
            f"JSON parse error: {e} — output: {result.stdout[:200]}"
        )


# ─── Market Data (no wallet needed) ──────────────────────────────────────────

def get_market(market_id_or_slug: str) -> dict:
    return _run(["markets", "get", market_id_or_slug])


def search_markets(query: str, limit: int = 10) -> list:
    return _run(["markets", "search", query, "--limit", str(limit)])


def list_markets(active: bool = True, limit: int = 50) -> list:
    return _run(["markets", "list", "--active", str(active).lower(), "--limit", str(limit)])


def get_market_price(token_id: str) -> dict:
    return _run(["clob", "midpoint", token_id])


def get_batch_prices(token_ids: list[str]) -> dict:
    return _run(["clob", "midpoints", ",".join(token_ids)])


def get_order_book(token_id: str) -> dict:
    return _run(["clob", "book", token_id])


def get_price_history(token_id: str, interval: str = "1d") -> list:
    return _run(["clob", "price-history", token_id, "--interval", interval])


def get_spread(token_id: str) -> dict:
    return _run(["clob", "spread", token_id])


def get_fee_rate(token_id: str) -> dict:
    return _run(["clob", "fee-rate", token_id])


# ─── Wallet Data (any address, no auth needed) ────────────────────────────────

def get_wallet_positions(address: str) -> list:
    return _run(["data", "positions", address])


def get_wallet_trades(address: str, limit: int = 50) -> list:
    return _run(["data", "trades", address, "--limit", str(limit)])


def get_wallet_value(address: str) -> dict:
    return _run(["data", "value", address])


def get_leaderboard(
    period: str = "month", order_by: str = "pnl", limit: int = 50
) -> list:
    return _run([
        "data", "leaderboard",
        "--period", period,
        "--order-by", order_by,
        "--limit", str(limit),
    ])


# ─── Authenticated: Account ───────────────────────────────────────────────────

def get_balance(asset_type: str = "collateral") -> dict:
    return _run(["clob", "balance", "--asset-type", asset_type])


def get_my_orders(market: Optional[str] = None) -> list:
    args = ["clob", "orders"]
    if market:
        args += ["--market", market]
    return _run(args)


def get_my_trades() -> list:
    return _run(["clob", "trades"])


# ─── Authenticated: Trading ───────────────────────────────────────────────────

def create_limit_order(
    token_id: str,
    side: str,
    price: float,
    size: float,
    post_only: bool = True,
) -> dict:
    """
    Place a limit order. post_only=True ensures maker (zero fees).
    """
    _rate_limit_order()
    args = [
        "clob", "create-order",
        "--token", token_id,
        "--side", side,
        "--price", f"{price:.4f}",
        "--size", f"{size:.2f}",
    ]
    if post_only:
        args.append("--post-only")
    return _run(args)


def create_market_order(token_id: str, side: str, amount_usdc: float) -> dict:
    """Market order — ONLY for emergency exits (taker fees apply)."""
    _rate_limit_order()
    return _run([
        "clob", "market-order",
        "--token", token_id,
        "--side", side,
        "--amount", f"{amount_usdc:.2f}",
    ])


def post_lp_orders(
    token_ids: list[str],
    side: str,
    prices: list[float],
    sizes: list[float],
) -> dict:
    """Batch LP order posting. One API call for N markets."""
    _rate_limit_order()
    return _run([
        "clob", "post-orders",
        "--tokens", ",".join(token_ids),
        "--side", side,
        "--prices", ",".join(f"{p:.4f}" for p in prices),
        "--sizes", ",".join(f"{s:.2f}" for s in sizes),
    ])


def cancel_order(order_id: str) -> dict:
    _rate_limit_order()
    return _run(["clob", "cancel", order_id])


def cancel_all_orders() -> dict:
    """Emergency stop — cancels everything."""
    logger.warning("CANCEL ALL ORDERS called")
    _rate_limit_order()
    return _run(["clob", "cancel-all"])


def cancel_market_orders(market_condition_id: str) -> dict:
    _rate_limit_order()
    return _run(["clob", "cancel-market", "--market", market_condition_id])


# ─── LP Rewards ───────────────────────────────────────────────────────────────

def get_daily_rewards(date: str) -> dict:
    return _run(["clob", "rewards", "--date", date])


def get_current_rewards() -> dict:
    return _run(["clob", "current-rewards"])


def check_order_scoring(order_id: str) -> dict:
    return _run(["clob", "order-scoring", order_id])


def get_market_reward_info(condition_id: str) -> dict:
    return _run(["clob", "market-reward", condition_id])
