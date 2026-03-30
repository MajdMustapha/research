"""SQLite-based trade logging."""

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from backtest.engine import Trade

logger = logging.getLogger(__name__)


class TradeLogger:
    """Logs trades to SQLite database."""

    def __init__(self, config: dict):
        db_path = config.get("reporting", {}).get("db_path", "trades.db")
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    size REAL NOT NULL,
                    pnl REAL NOT NULL,
                    fees REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    regime TEXT,
                    strategy TEXT
                )
            """)
            conn.commit()

    def log_trade(self, trade: Trade, regime: str = "", strategy: str = ""):
        """Insert a completed trade into the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO trades (symbol, side, entry_price, exit_price, size, pnl, fees,
                                    entry_time, exit_time, regime, strategy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.symbol,
                    trade.side,
                    trade.entry_price,
                    trade.exit_price,
                    trade.size,
                    trade.pnl,
                    trade.fees,
                    str(trade.entry_time),
                    str(trade.exit_time),
                    regime,
                    strategy,
                ),
            )
            conn.commit()
        logger.info(f"Trade logged: {trade.side} {trade.symbol} PnL={trade.pnl:.2f}")

    def get_trades(self, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
        """Retrieve trades with optional date filter."""
        query = "SELECT * FROM trades"
        params = []

        conditions = []
        if start:
            conditions.append("entry_time >= ?")
            params.append(str(start))
        if end:
            conditions.append("exit_time <= ?")
            params.append(str(end))

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_daily_summary(self, day: date | None = None) -> dict:
        """Get aggregated daily trading stats."""
        if day is None:
            day = date.today()

        day_str = str(day)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total_trades,
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    COALESCE(SUM(fees), 0) as total_fees,
                    COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) as winners,
                    COALESCE(SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END), 0) as losers
                FROM trades
                WHERE date(exit_time) = ?
                """,
                (day_str,),
            ).fetchone()

            total = row[0]
            return {
                "date": day_str,
                "total_trades": total,
                "total_pnl": row[1],
                "total_fees": row[2],
                "winners": row[3],
                "losers": row[4],
                "win_rate": row[3] / total if total > 0 else 0,
            }
