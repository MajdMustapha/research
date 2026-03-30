"""Track open positions and unrealized PnL."""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from backtest.engine import Trade

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    side: str  # BUY or SELL
    size: float
    entry_price: float
    current_price: float
    stop_price: float
    entry_time: datetime
    unrealized_pnl: float = 0.0

    def update_price(self, price: float):
        """Update current price and recalculate unrealized PnL."""
        self.current_price = price
        if self.side == "BUY":
            self.unrealized_pnl = (price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - price) * self.size

    def is_stopped(self, low: float, high: float) -> bool:
        """Check if the position's stop has been hit."""
        if self.side == "BUY":
            return low <= self.stop_price
        else:
            return high >= self.stop_price


class PositionTracker:
    """Manages a dictionary of open positions keyed by symbol."""

    def __init__(self):
        self._positions: dict[str, Position] = {}

    def open_position(
        self,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        stop_price: float,
        entry_time: datetime,
    ) -> Position:
        """Open a new position."""
        pos = Position(
            symbol=symbol,
            side=side,
            size=size,
            entry_price=entry_price,
            current_price=entry_price,
            stop_price=stop_price,
            entry_time=entry_time,
        )
        self._positions[symbol] = pos
        logger.info(f"Position opened: {side} {size:.6f} {symbol} @ {entry_price:.2f}")
        return pos

    def close_position(self, symbol: str, exit_price: float, exit_time: datetime, fees: float = 0.0) -> Trade | None:
        """Close a position and return the completed Trade."""
        pos = self._positions.pop(symbol, None)
        if pos is None:
            logger.warning(f"No open position for {symbol}")
            return None

        if pos.side == "BUY":
            pnl = (exit_price - pos.entry_price) * pos.size - fees
        else:
            pnl = (pos.entry_price - exit_price) * pos.size - fees

        trade = Trade(
            symbol=symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            size=pos.size,
            pnl=pnl,
            fees=fees,
        )
        logger.info(f"Position closed: {symbol} PnL={pnl:.2f}")
        return trade

    def update_prices(self, prices: dict[str, float]):
        """Update current prices for all open positions."""
        for symbol, price in prices.items():
            if symbol in self._positions:
                self._positions[symbol].update_price(price)

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def get_open_positions(self) -> list[Position]:
        return list(self._positions.values())

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions.values())

    def count(self) -> int:
        return len(self._positions)
