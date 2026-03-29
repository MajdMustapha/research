"""
SQLite state management — single source of truth for all persistent state.
Uses WAL mode for concurrent agent writes.
"""

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import yaml

from lib.logger import get_logger

logger = get_logger(__name__)


def _get_data_dir() -> str:
    """Resolve data directory from settings or env."""
    data_dir = os.getenv("DATA_DIR", "data")
    if not os.path.isabs(data_dir):
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), data_dir)
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


DATABASE_PATH = os.path.join(_get_data_dir(), "state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    market_question TEXT,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    size_usdc REAL NOT NULL,
    shares REAL NOT NULL,
    order_id TEXT,
    status TEXT DEFAULT 'open',
    layer TEXT,
    signal_summary TEXT,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    exit_price REAL,
    pnl_usdc REAL
);

CREATE TABLE IF NOT EXISTS lp_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    status TEXT DEFAULT 'active',
    rewards_earned_usdc REAL DEFAULT 0,
    placed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    market_id TEXT,
    token_id TEXT,
    market_question TEXT,
    current_price REAL,
    estimated_true_prob REAL,
    edge REAL,
    confidence REAL,
    claude_reasoning TEXT,
    recommended_side TEXT,
    wallet_consensus_count INTEGER,
    action_taken TEXT,
    telegram_message_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wallet_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    wallet_domain TEXT,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL,
    size_usdc REAL,
    tx_hash TEXT,
    observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    date TEXT PRIMARY KEY,
    starting_balance REAL,
    ending_balance REAL,
    realized_pnl REAL,
    lp_rewards_usdc REAL,
    num_trades_opened INTEGER,
    num_trades_closed INTEGER,
    api_costs_usd REAL
);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    agent TEXT,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    market_question TEXT,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    size_usdc REAL NOT NULL,
    shares REAL NOT NULL,
    layer TEXT,
    signal_summary TEXT,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    simulated_exit_price REAL,
    simulated_pnl_usdc REAL,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS seen_articles (
    guid TEXT PRIMARY KEY,
    title TEXT,
    seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signal_cooldowns (
    market_id TEXT PRIMARY KEY,
    last_signal_at TIMESTAMP NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    """
    Return a WAL-mode connection.
    WAL allows concurrent readers + serialised writes without lock errors.
    """
    conn = sqlite3.connect(
        DATABASE_PATH,
        check_same_thread=False,
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_write():
    """Context manager for atomic write operations."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialise database schema. Run once at startup."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    logger.info("Database initialised")


# ─── Bankroll ─────────────────────────────────────────────────────────────────

def get_bankroll() -> float:
    """
    Get current bankroll. In paper mode returns paper_bankroll from settings.
    In live mode calls CLI for USDC balance.
    """
    try:
        settings_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
        )
        with open(settings_path) as f:
            settings = yaml.safe_load(f)

        if settings.get("system", {}).get("paper_trade", True):
            return float(settings.get("system", {}).get("paper_bankroll", 1000.0))

        from lib.cli import get_balance
        balance_data = get_balance(asset_type="collateral")
        return float(balance_data.get("balance", 0))
    except Exception as e:
        logger.error(f"Failed to get bankroll: {e}")
        return 0.0


def get_open_positions() -> list[dict]:
    """Return all open directional positions."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'open'"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_lp_capital() -> float:
    """LP capital = bankroll minus sum of open directional position sizes."""
    bankroll = get_bankroll()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(size_usdc), 0) as total FROM positions WHERE status = 'open'"
        ).fetchone()
        directional_deployed = float(row["total"])
        return max(0.0, bankroll - directional_deployed)
    finally:
        conn.close()


# ─── Signals ──────────────────────────────────────────────────────────────────

def save_signal(signal: dict) -> int:
    """Save a new signal. Returns signal_id."""
    with db_write() as conn:
        cursor = conn.execute(
            """INSERT INTO signals
            (source, market_id, token_id, market_question, current_price,
             estimated_true_prob, edge, confidence, claude_reasoning,
             recommended_side, wallet_consensus_count, action_taken, telegram_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.get("source"),
                signal.get("market_id"),
                signal.get("token_id"),
                signal.get("market_question"),
                signal.get("current_price"),
                signal.get("estimated_true_prob"),
                signal.get("edge"),
                signal.get("confidence"),
                signal.get("claude_reasoning"),
                signal.get("recommended_side"),
                signal.get("wallet_consensus_count"),
                signal.get("action_taken"),
                signal.get("telegram_message_id"),
            ),
        )
        return cursor.lastrowid


def get_signal(signal_id: int) -> Optional[dict]:
    """Retrieve a signal by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_signal_action(signal_id: int, action: str, decided_at: datetime) -> None:
    """Update a signal's action status."""
    with db_write() as conn:
        conn.execute(
            "UPDATE signals SET action_taken = ?, decided_at = ? WHERE id = ?",
            (action, decided_at.isoformat(), signal_id),
        )


def update_signal_telegram_id(signal_id: int, telegram_message_id: str) -> None:
    """Set the Telegram message ID for a signal."""
    with db_write() as conn:
        conn.execute(
            "UPDATE signals SET telegram_message_id = ? WHERE id = ?",
            (telegram_message_id, signal_id),
        )


# ─── Positions ────────────────────────────────────────────────────────────────

def save_position(position: dict) -> int:
    """Save a new directional position. Returns position_id."""
    with db_write() as conn:
        cursor = conn.execute(
            """INSERT INTO positions
            (market_id, token_id, market_question, side, entry_price,
             size_usdc, shares, order_id, layer, signal_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                position["market_id"],
                position["token_id"],
                position.get("market_question"),
                position["side"],
                position["entry_price"],
                position["size_usdc"],
                position["shares"],
                position.get("order_id"),
                position.get("layer"),
                position.get("signal_summary"),
            ),
        )
        return cursor.lastrowid


def close_position(position_id: int, exit_price: float, pnl: float) -> None:
    """Close an open position."""
    with db_write() as conn:
        conn.execute(
            """UPDATE positions
            SET status = 'closed', exit_price = ?, pnl_usdc = ?,
                closed_at = ?
            WHERE id = ?""",
            (exit_price, pnl, datetime.now(timezone.utc).isoformat(), position_id),
        )


# ─── LP Orders ────────────────────────────────────────────────────────────────

def save_lp_order(order: dict) -> int:
    """Save an LP order."""
    with db_write() as conn:
        cursor = conn.execute(
            """INSERT INTO lp_orders
            (market_id, token_id, order_id, side, price, size)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                order["market_id"],
                order["token_id"],
                order["order_id"],
                order["side"],
                order["price"],
                order["size"],
            ),
        )
        return cursor.lastrowid


def update_lp_order_status(order_id: str, status: str) -> None:
    """Update LP order status."""
    with db_write() as conn:
        conn.execute(
            "UPDATE lp_orders SET status = ?, updated_at = ? WHERE order_id = ?",
            (status, datetime.now(timezone.utc).isoformat(), order_id),
        )


def get_active_lp_orders() -> list[dict]:
    """Return all active LP orders."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM lp_orders WHERE status = 'active'"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Wallet Activity ─────────────────────────────────────────────────────────

def save_wallet_activity(activity: dict) -> int:
    """Save a wallet trade observation."""
    with db_write() as conn:
        cursor = conn.execute(
            """INSERT INTO wallet_activity
            (wallet_address, wallet_domain, market_id, token_id, side, price, size_usdc, tx_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                activity["wallet_address"],
                activity.get("wallet_domain"),
                activity["market_id"],
                activity["token_id"],
                activity["side"],
                activity.get("price"),
                activity.get("size_usdc"),
                activity.get("tx_hash"),
            ),
        )
        return cursor.lastrowid


def get_wallet_activity(domain: str = None, since_hours: int = 48) -> list[dict]:
    """Get wallet activity, optionally filtered by domain and time window."""
    conn = get_connection()
    try:
        query = "SELECT * FROM wallet_activity WHERE observed_at >= datetime('now', ?)"
        params: list = [f"-{since_hours} hours"]
        if domain:
            query += " AND wallet_domain = ?"
            params.append(domain)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Paper Trades ─────────────────────────────────────────────────────────────

def save_paper_trade(trade: dict) -> int:
    """Save a paper (simulated) trade."""
    with db_write() as conn:
        cursor = conn.execute(
            """INSERT INTO paper_trades
            (signal_id, market_id, token_id, market_question, side,
             entry_price, size_usdc, shares, layer, signal_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.get("signal_id"),
                trade["market_id"],
                trade["token_id"],
                trade.get("market_question"),
                trade["side"],
                trade["entry_price"],
                trade["size_usdc"],
                trade["shares"],
                trade.get("layer"),
                trade.get("signal_summary"),
            ),
        )
        return cursor.lastrowid


def get_open_paper_trades() -> list[dict]:
    """Return paper trades that haven't been resolved."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE resolved_at IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Daily P&L ────────────────────────────────────────────────────────────────

def save_daily_pnl(date: str, data: dict) -> None:
    """Save or update daily P&L record."""
    with db_write() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO daily_pnl
            (date, starting_balance, ending_balance, realized_pnl,
             lp_rewards_usdc, num_trades_opened, num_trades_closed, api_costs_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                date,
                data.get("starting_balance", 0),
                data.get("ending_balance", 0),
                data.get("realized_pnl", 0),
                data.get("lp_rewards_usdc", 0),
                data.get("num_trades_opened", 0),
                data.get("num_trades_closed", 0),
                data.get("api_costs_usd", 0),
            ),
        )


# ─── System Events ───────────────────────────────────────────────────────────

def log_event(event_type: str, agent: str, message: str) -> None:
    """Log a system event to the database."""
    try:
        with db_write() as conn:
            conn.execute(
                "INSERT INTO system_events (event_type, agent, message) VALUES (?, ?, ?)",
                (event_type, agent, message),
            )
    except Exception as e:
        logger.error(f"Failed to log event: {e}")


# ─── Seen Articles (dedup) ───────────────────────────────────────────────────

def is_article_seen(guid: str) -> bool:
    """Check if an article GUID has been seen."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM seen_articles WHERE guid = ?", (guid,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_article_seen(guid: str, title: str = None) -> None:
    """Mark an article as seen."""
    with db_write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_articles (guid, title) VALUES (?, ?)",
            (guid, title),
        )


def cleanup_old_articles(days: int = 7) -> None:
    """Remove articles older than N days."""
    with db_write() as conn:
        conn.execute(
            "DELETE FROM seen_articles WHERE seen_at < datetime('now', ?)",
            (f"-{days} days",),
        )


# ─── Signal Cooldowns ────────────────────────────────────────────────────────

def is_signal_on_cooldown(market_id: str, cooldown_hours: int = 24) -> bool:
    """Check if a market has an active signal cooldown."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT 1 FROM signal_cooldowns
            WHERE market_id = ?
            AND last_signal_at >= datetime('now', ?)""",
            (market_id, f"-{cooldown_hours} hours"),
        ).fetchone()
        return row is not None
    finally:
        conn.close()



def get_today_realized_pnl() -> float:
    """Return sum of realized P&L for today (UTC). Used by daily loss limit."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(pnl_usdc), 0) as total
            FROM positions
            WHERE status = 'closed'
            AND date(closed_at) = date('now')"""
        ).fetchone()
        return float(row["total"])
    finally:
        conn.close()


def is_tx_hash_seen(tx_hash: str) -> bool:
    """Check if a transaction hash has already been recorded in wallet_activity."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM wallet_activity WHERE tx_hash = ?", (tx_hash,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def set_signal_cooldown(market_id: str) -> None:
    """Set a cooldown for a market."""
    with db_write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO signal_cooldowns (market_id, last_signal_at) VALUES (?, ?)",
            (market_id, datetime.now(timezone.utc).isoformat()),
        )
